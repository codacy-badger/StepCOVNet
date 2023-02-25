from collections import defaultdict

import numpy as np

from stepcovnet.common.utils import apply_timeseries_scalers
from stepcovnet.common.utils import get_samples_ngram_with_mask
from stepcovnet.common.utils import normalize_tokenized_arrows
from stepcovnet.data.Tokenizers import Tokenizers


class TrainingFeatureGenerator(object):
    def __init__(self, dataset_path, dataset_type, batch_size, indexes, num_samples, lookback=1, scalers=None,
                 difficulty="challenge", warmup=False, shuffle=True, tokenizer_name=None):
        self.dataset_path = dataset_path
        self.dataset_type = dataset_type
        self.train_indexes = indexes
        self.num_samples = num_samples
        self.scalers = scalers
        self.lookback = lookback
        self.batch_size = batch_size
        self.difficulty = difficulty
        self.shuffle = shuffle
        self.tokenizer = None if tokenizer_name is None else Tokenizers[tokenizer_name].value

        # The Tensorflow calls the generator three times before starting a training job. We will "warmup" the data
        # yielding by returning the same data for the three calls. This way the indexing is aligned correctly.
        self.warmup_countdown = 3 if warmup else 0
        self.rng = np.random.default_rng(42)

    def __len__(self):
        return int(np.ceil(self.num_samples / self.batch_size))

    def __call__(self):
        with self.dataset_type(self.dataset_path) as dataset:
            dataset.set_difficulty(self.difficulty)
            self.song_index = 0
            self.song_start_index = None
            new_song = True
            if self.shuffle:
                self.rng.shuffle(self.train_indexes)
            while True:
                features = defaultdict(lambda: np.array([]))
                if self.song_index >= len(self.train_indexes):
                    self.song_index = 0
                song_start_index, song_end_index = dataset.song_index_ranges[self.train_indexes[self.song_index]]
                if self.song_start_index is None or self.song_start_index >= song_end_index or \
                        self.song_start_index < song_start_index or self.warmup_countdown > 0:
                    self.song_start_index = song_start_index
                    self.warmup_countdown = max(self.warmup_countdown - 1, 0)
                # We only return partial batches when at the end of the training data. Otherwise, use start of next song
                # to append data to the batch.
                while len(features["y_batch"]) == 0 or self.song_index < len(self.train_indexes):
                    start_index = self.song_start_index
                    y_batch_len = len(features["y_batch"])
                    end_index = min(start_index + self.batch_size - y_batch_len, song_end_index)

                    mask_padding_value = 0 if new_song else 1
                    lookback_index_padding_start = max(start_index - self.lookback, song_start_index)
                    lookback_padding_added = start_index - lookback_index_padding_start
                    if self.tokenizer is not None:
                        arrows = dataset.string_arrows[lookback_index_padding_start:end_index]
                        arrow_features, arrow_mask = self.get_tokenized_arrow_features(arrows, mask_padding_value,
                                                                                       lookback_padding_added)
                    else:
                        arrows = dataset.label_encoded_arrows[lookback_index_padding_start:end_index]
                        arrow_features, arrow_mask = self.get_arrow_features(arrows, mask_padding_value,
                                                                             lookback_padding_added)

                    audio_data = dataset.features[lookback_index_padding_start:end_index]
                    audio_features = self.get_audio_features(audio_data, lookback_padding_added)

                    arrows = dataset.onehot_encoded_arrows[start_index:end_index]
                    sample_weights = dataset.sample_weights[start_index:end_index]

                    features = self.append_existing_data(features=features, arrow_features=arrow_features,
                                                         arrow_mask=arrow_mask, audio_features=audio_features,
                                                         arrows=arrows, sample_weights=sample_weights)
                    self.song_start_index = end_index
                    # Break if collected enough data for a batch or end of song list.
                    # Otherwise, change to next song to collect more.
                    if len(features["y_batch"]) >= self.batch_size or self.song_index + 1 >= len(self.train_indexes):
                        new_song = False
                        break
                    else:
                        self.song_index += 1
                        song_start_index, song_end_index = \
                            dataset.song_index_ranges[self.train_indexes[self.song_index]]
                        self.song_start_index = song_start_index
                        new_song = True

                if self.song_start_index >= song_end_index:
                    new_song = True
                    self.song_index += 1

                if len(features["y_batch"]) > 0:
                    scaled_audio_features = apply_timeseries_scalers(features=features["audio_features"],
                                                                     scalers=self.scalers)
                    x_batch = {"arrow_input": features["arrow_features"],
                               "arrow_mask": features["arrow_mask"],
                               "audio_input": scaled_audio_features}
                    yield x_batch, features["y_batch"], features["sample_weights_batch"]

    @staticmethod
    def append_existing_data(features, arrow_features, arrow_mask, audio_features, arrows, sample_weights):
        # Append or set features/labels/sample weights based on if existing data is present
        if not features or any(len(value) == 0 for value in features.values()):
            features["arrow_features"] = arrow_features
            features["arrow_mask"] = arrow_mask
            features["audio_features"] = audio_features
            features["y_batch"] = arrows
            features["sample_weights_batch"] = sample_weights
        else:
            if isinstance(features["arrow_features"], list) or isinstance(features["arrow_mask"], list):
                features["arrow_features"].extend(arrow_features)
                features["arrow_mask"].extend(arrow_mask)
                # Normalize again after appending in the case where split batches have different max lengths
                features["arrow_features"], features["arrow_mask"] = \
                    normalize_tokenized_arrows(arrow_features=features["arrow_features"],
                                               arrow_mask=features["arrow_mask"])
            else:
                features["arrow_features"] = np.concatenate((features["arrow_features"], arrow_features), axis=0)
                features["arrow_mask"] = np.concatenate((features["arrow_mask"], arrow_mask), axis=0)
            features["audio_features"] = np.concatenate((features["audio_features"], audio_features), axis=0)
            features["y_batch"] = np.concatenate((features["y_batch"], arrows), axis=0)
            features["sample_weights_batch"] = np.concatenate((features["sample_weights_batch"], sample_weights),
                                                              axis=0)

        return features

    def get_tokenized_arrow_features(self, arrows, mask_padding_value, lookback_padding_added):
        arrow_features, arrow_mask = get_samples_ngram_with_mask(arrows, self.lookback,
                                                                 reshape=True,
                                                                 sample_padding_value='0000',
                                                                 mask_padding_value=mask_padding_value)
        arrow_features = arrow_features[lookback_padding_added:]
        arrow_mask = arrow_mask[lookback_padding_added:].astype(np.int32)
        decoded_arrows = [" ".join(line) for line in arrow_features]
        arrow_features = [self.tokenizer(line, return_tensors='tf',
                                         add_prefix_space=True)['input_ids']
                              .numpy()[0][1:].astype(np.int32)
                          for line in decoded_arrows]
        # Lookback data from ngram returns empty value in index 0. Also, arrow features should only
        # contain previously seen features. Therefore, removing last element and last lookback from
        # arrows features.
        arrow_features = arrow_features[:-1]
        arrow_mask = list(arrow_mask[:-1, 1:])
        return normalize_tokenized_arrows(arrow_features=arrow_features, arrow_mask=arrow_mask)

    def get_arrow_features(self, arrows, mask_padding_value, lookback_padding_added):
        arrow_features, arrow_mask = get_samples_ngram_with_mask(arrows, self.lookback,
                                                                 reshape=True,
                                                                 mask_padding_value=mask_padding_value)
        arrow_features = arrow_features[lookback_padding_added:]
        arrow_mask = arrow_mask[lookback_padding_added:]
        # Lookback data from ngram returns empty value in index 0. Also, arrow features should only
        # contain previously seen features. Therefore, removing last element and last lookback from
        # arrows features.
        arrow_features = arrow_features[:-1, 1:]
        arrow_mask = arrow_mask[:-1, 1:]

        return arrow_features.astype(np.int32), arrow_mask.astype(np.int32)

    def get_audio_features(self, audio_data, lookback_padding_added):
        audio_features, _ = get_samples_ngram_with_mask(audio_data, self.lookback, squeeze=False)
        audio_features = audio_features[lookback_padding_added:]
        # Lookback data from ngram returns empty value in index 0.
        # Therefore, removing first element from audio features.
        audio_features = audio_features[1:]

        return audio_features.astype(np.float64)
