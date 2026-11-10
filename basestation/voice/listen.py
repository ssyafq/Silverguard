"""Ears: records from the microphone and turns speech into text.

Uses faster-whisper, which runs on the laptop's CPU, so the person's voice is never
sent over the internet. The mic is only opened during a check-in (privacy), and the
recording is never saved — only the transcript is kept.
"""

import time

import numpy as np
import sounddevice as sd

from basestation.voice.settings import voice_config

SAMPLE_RATE = 16_000            # Whisper expects 16 kHz mono audio

_model = None


def load_model():
    """Load the Whisper model (slow the first time: it downloads, then loads ~ a few s)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(voice_config()["stt_model"], device="cpu", compute_type="int8")
    return _model


def record(seconds: float) -> np.ndarray:
    """Record `seconds` of mono audio from the default microphone."""
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def is_silent(audio: np.ndarray) -> bool:
    """True if the recording is too quiet to contain speech (loudness below silence_rms)."""
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    return rms < float(voice_config()["silence_rms"])


def transcribe(audio) -> tuple[str, str]:
    """Turn audio (a 16 kHz numpy array, or a path to an audio file) into (text, language)."""
    segments, info = load_model().transcribe(
        audio,
        vad_filter=True,                                   # skip silent parts (stops Whisper inventing words)
        initial_prompt=voice_config()["stt_initial_prompt"],
        condition_on_previous_text=False,
    )
    text = "".join(seg.text for seg in segments).strip()   # decoding happens here
    return text, info.language


def listen() -> dict:
    """Record one reply and transcribe it. Returns transcript, language and timings."""
    seconds = float(voice_config()["record_seconds"])
    t0 = time.perf_counter()
    audio = record(seconds)
    t1 = time.perf_counter()

    if is_silent(audio):
        text, language = "", ""
    else:
        text, language = transcribe(audio)
    t2 = time.perf_counter()
    return {"transcript": text, "language": language, "record_s": t1 - t0, "stt_s": t2 - t1}


if __name__ == "__main__":
    load_model()
    print(f"Speak now ({voice_config()['record_seconds']} s)...")
    print(listen())
