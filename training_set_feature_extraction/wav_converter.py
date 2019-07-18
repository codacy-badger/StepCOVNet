import os
from os import listdir
from os.path import isfile, join

import subprocess
import re


def get_file_names(mypath):
    return [f for f in listdir(mypath) if isfile(join(mypath, f))]


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Convert audio files to .wav format.")
    parser.add_argument("--audio",
                        type=str,
                        help="input audio path")
    parser.add_argument("--wav",
                        type=str,
                        help="output wav path")
    args = parser.parse_args()

    if not os.path.isdir(args.audio):
        raise OSError('Audio files path %s not found' % args.audio)

    if not os.path.isdir(args.wav):
        raise OSError('Wavs path %s not found' % args.wav)

    audio_path = args.audio
    wav_path = args.wav
    file_names = get_file_names(audio_path)
    existing_wavs = get_file_names(wav_path)

    print("Starting .wav conversion\n-----------------------------------------")

    for file_name in file_names:
        new_file_name = re.sub("[^a-z0-9-_]", "", file_name.lower()[:-4])
        if new_file_name + '.wav' in existing_wavs:
            print("Skipping...", file_name + " already converted!")
            continue
        print("Converting " + file_name)
        try:
            subprocess.call(
                ['ffmpeg', '-y', '-loglevel', 'quiet', '-i',
                 audio_path + file_name, '-ar', '44100', join(wav_path, new_file_name + '.wav')]
            )
        except:
            print("Failed to convert", file_name)
