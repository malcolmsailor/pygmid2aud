#!/usr/bin/env python3
"""Records pygame's midi playback to an audio file.

Works on my mac. No promises that it will work on anyone else's computer.

Python dependencies:
mido
pyaudio
pygame

Other dependencies:
PortAudio
Soundflower
ffmpeg (not required if output format is '.wav')
SwitchAudioSource
do-not-disturb-cli
    https://github.com/sindresorhus/do-not-disturb-cli

The playback will not be audible during recording. Also, any other sounds on
your computer (e.g., email notifications) will be recorded as well. So best
to mute these!
"""
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

import mido
import pyaudio
import pygame

from increment_fname import increment_fname

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100

"""NB: when calling the pyaudio stream's read() function, I had to set
`exception_on_overflow=False` to avoid `OSError: [Errno -9981] Input
overflowed`. This is presumably not ideal since it means we are missing some
frames in the final recording, but so far this damage hasn't been audible
to me.
"""

SCRIPT_DIR = os.path.dirname((os.path.realpath(__file__)))
NOISY_APPS = os.path.join(SCRIPT_DIR, ".noisy_apps")


def get_existing_output_device():
    device = (
        subprocess.run(
            ["SwitchAudioSource", "-c", "-t", "output"],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    print(f"Existing audio output device is {device}")
    return device


def soundflower_on():
    subprocess.run(
        ["SwitchAudioSource", "-t", "output", "-s", "Soundflower (2ch)"],
        capture_output=True,
        check=True,
    )
    print("Switching output device to Soundflower (2ch)")


def soundflower_off(orig_device):
    subprocess.run(
        ["SwitchAudioSource", "-t", "output", "-s", orig_device],
        capture_output=True,
        check=True,
    )
    print(f"Restoring output device to {orig_device}")


def get_dur(midi_path):
    dur = mido.MidiFile(midi_path).length
    print(f"Duration is {dur} seconds")
    return dur


def pygame_play(midi_path):
    pygame.mixer.init()
    pygame.mixer.music.load(midi_path)
    pygame.mixer.music.play()


def get_pyaudio_and_stream(format_, channels, rate, frames_per_buffer):
    def _get_soundflower_index(p):
        for i in range(p.get_device_count()):
            device_info = p.get_device_info_by_index(i)
            if device_info["name"] == "Soundflower (2ch)":
                return device_info["index"]
        raise Exception("Soundflower not found!")

    py_audio = pyaudio.PyAudio()
    input_device_index = _get_soundflower_index(py_audio)
    stream = py_audio.open(
        format=format_,
        channels=channels,
        rate=rate,
        input=True,
        frames_per_buffer=frames_per_buffer,
        input_device_index=input_device_index,
    )
    return py_audio, stream


def get_frames(
    stream, rate, dur, frames_per_buffer, frames, extra_dur=1,
):
    for _ in range(math.ceil(rate / frames_per_buffer * (dur + extra_dur))):
        frames.append(
            # See note above re: exception_on_overflow
            stream.read(frames_per_buffer, exception_on_overflow=False)
        )


def close_pyaudio(py_audio, stream):
    stream.stop_stream()
    stream.close()
    py_audio.terminate()


def progress_bar(dur):
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    increment = dur / width
    for _ in range(width):
        sys.stdout.write("=")
        sys.stdout.flush()
        time.sleep(increment)
    sys.stdout.write("\n")


def write_wav(frames, wav_path, py_audio, format_, channels, rate):
    with wave.open(wav_path, "wb") as outf:
        outf.setnchannels(channels)
        outf.setsampwidth(py_audio.get_sample_size(format_))
        outf.setframerate(rate)
        outf.writeframes(b"".join(frames))


def record(
    midi_path, temp_out_path, format_, channels, rate, frames_per_buffer,
):

    dur = get_dur(midi_path)
    frames = []
    py_audio, stream = get_pyaudio_and_stream(
        format_, channels, rate, frames_per_buffer
    )
    time.sleep(1)
    recording_thread = threading.Thread(
        target=get_frames, args=[stream, rate, dur, frames_per_buffer, frames],
    )
    recording_thread.start()
    # pygame mixer needs to be initialized AFTER get_pyaudio_and_stream()
    #   or we get inscrutable errors from PortAudio (via pyAudio)
    pygame_play(midi_path)
    progress_bar(dur)
    recording_thread.join()
    close_pyaudio(py_audio, stream)
    if frames:
        write_wav(frames, temp_out_path, py_audio, format_, channels, rate)
        return True
    return False


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("midi_path")
    parser.add_argument(
        "-o",
        "--output-path",
        help=(
            "Default is same as midi path, but with .m4a extension. If the "
            "extension is not '.wav', the wav file will be attempted to be "
            "converted with ffmpeg. Will attempt to create the directory if "
            "it does not exist."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite output files (otherwise filename is incremented)",
    )
    args = parser.parse_args()
    return (
        args.midi_path,
        args.output_path,
        args.overwrite,
        # maybe provide arguments for these:
        FORMAT,
        CHANNELS,
        RATE,
        CHUNK,
    )


def write_to_output_path(midi_path, output_path, temp_out_path, overwrite):
    if output_path is None:
        output_path = os.path.splitext(midi_path)[0] + ".m4a"
    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
    if output_path.endswith(".wav"):
        shutil.move(temp_out_path, output_path)
        print(f"Output written to {output_path}")
    else:
        if not overwrite:
            output_path = increment_fname(output_path, n_digits=2)
        print(f"Converting wave output to {output_path}")
        subprocess.run(
            [
                "ffmpeg",
                "-y" if overwrite else "-n",
                "-i",
                temp_out_path,
                output_path,
            ],
            capture_output=True,
            check=True,
        )


def dnd_off():
    dnd_status = (
        subprocess.run(
            ["do-not-disturb", "status"], capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    if dnd_status == "off":
        subprocess.run(["do-not-disturb", "on"], check=True)
    return dnd_status


def restore_dnd(orig_status):
    if orig_status == "off":
        subprocess.run(["do-not-disturb", "off"], check=True)


def check_for_noisy_apps():
    if not os.path.exists(NOISY_APPS):
        print("Warning: no file called .noisy_apps foung in pygmid2aud folder")
        input("<press enter to continue>")
        return
    with open(NOISY_APPS, "r", encoding="utf-8") as inf:
        apps = [app for app in inf.read().split("\n") if app]
    ps_result = subprocess.run(
        ["ps", "aux"], capture_output=True, check=True
    ).stdout.decode()
    open_apps = []
    for app in apps:
        if re.search(f"{app}", ps_result, re.IGNORECASE):
            open_apps.append(app)
    if open_apps:
        print(
            "The following potentially noisy apps are open, please close them "
            "and then try again:"
        )
        for app in open_apps:
            print(" " * 4 + app)
        print(
            "(The list of potentially noisy apps "
            "to be checked is in .noisy_apps in the pygmid2aud folder)"
        )
        sys.exit(1)


def main():
    (
        midi_path,
        output_path,
        overwrite,
        format_,
        channels,
        rate,
        frames_per_buffer,
    ) = get_args()
    orig_dnd_status = dnd_off()

    check_for_noisy_apps()
    _, temp_out_path = tempfile.mkstemp(suffix=".wav")
    orig_device = get_existing_output_device()
    soundflower_on()
    try:
        result = record(
            midi_path,
            temp_out_path,
            format_,
            channels,
            rate,
            frames_per_buffer,
        )
    except:
        soundflower_off(orig_device)
        restore_dnd(orig_dnd_status)
        raise
    else:
        soundflower_off(orig_device)
        restore_dnd(orig_dnd_status)
        if result:
            write_to_output_path(
                midi_path, output_path, temp_out_path, overwrite
            )


if __name__ == "__main__":
    main()
