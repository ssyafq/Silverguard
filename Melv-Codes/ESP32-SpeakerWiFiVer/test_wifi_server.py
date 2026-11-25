"""Fake-watch test for wifi_server.py (Phase C test — no ESP32 needed).

Sends a WAV file (or 2 s of silence) to POST /checkin on your local wifi_server.py
and saves the spoken reply as reply.wav so you can verify the server before flashing.

Usage:
    python Melv-Codes/ESP32-SpeakerWiFiVer/test_wifi_server.py
    python Melv-Codes/ESP32-SpeakerWiFiVer/test_wifi_server.py --wav Melv-Codes/ESP32-SpeakerUSBVer/mic_test.wav
"""

import argparse
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

AUDIO_RATE = 16_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="wifi_server.py base URL")
    ap.add_argument("--wav", default="", help="Optional WAV file to upload as the wearer's reply")
    ap.add_argument("--attempt", type=int, default=1)
    args = ap.parse_args()

    if args.wav:
        data, sr = sf.read(args.wav, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != AUDIO_RATE:
            new_len = int(round((data.size / float(sr)) * AUDIO_RATE))
            data = np.interp(np.linspace(0, 1, new_len, endpoint=False), np.linspace(0, 1, data.size, endpoint=False), data)
        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    else:
        pcm = np.zeros(AUDIO_RATE * 2, dtype="<i2").tobytes()

    print(f"POST {args.url}/checkin ({len(pcm)} bytes, attempt={args.attempt})...")
    r = requests.post(
        f"{args.url}/checkin?device_id=fake-watch&attempt={args.attempt}&score=0.92",
        data=pcm,
        headers={"Content-Type": "application/octet-stream"},
        timeout=30,
    )
    print(f"HTTP {r.status_code} | X-Reply={r.headers.get('X-Reply')} | X-Next={r.headers.get('X-Next')} | {len(r.content)} bytes")
    if r.ok and r.content:
        out = Path(__file__).with_name("reply.wav")
        audio = np.frombuffer(r.content[: len(r.content) - (len(r.content) % 2)], dtype="<i2")
        sf.write(str(out), audio, AUDIO_RATE)
        print(f"Saved server spoken reply to {out}")


if __name__ == "__main__":
    main()
