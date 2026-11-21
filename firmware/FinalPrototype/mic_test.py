"""Record a few seconds from the ESP32 INMP441 mic over USB, print loudness, save a WAV to listen to.

    python Melv-Codes/ESP32-SpeakerUSBVer/mic_test.py            (auto-detect port, 3 s)
    python Melv-Codes/ESP32-SpeakerUSBVer/mic_test.py --port COM5 --seconds 5

Close the Arduino Serial Monitor first.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(THIS_DIR.parents[1]))

import esp32_audio  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="auto")
ap.add_argument("--seconds", type=float, default=3.0)
args = ap.parse_args()

ser = esp32_audio.get_serial(args.port)
if ser is None:
    sys.exit("Could not open the ESP32 port. Plug it in, close Serial Monitor, or pass --port COMx.")
print(f"Opened {ser.port}. Talk now for {args.seconds:.0f} s...")

audio = esp32_audio.record_audio(args.seconds)
esp32_audio.close_serial()
if audio is None:
    sys.exit("No audio came back (see the [esp32_audio] error above). Is ESP32-SpeakerUSBVer.ino flashed?")

rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
peak = float(np.max(np.abs(audio))) if audio.size else 0.0
zeros = float(np.mean(audio == 0)) * 100 if audio.size else 100.0
out = THIS_DIR / "mic_test.wav"
sf.write(str(out), audio, 16000)

print(f"samples={audio.size}  rms={rms:.4f}  peak={peak:.3f}  exact-zero samples={zeros:.0f}%")
print(f"saved {out} - open it and listen")
if zeros > 90:
    print("-> Almost all zeros: the INMP441 isn't sending data. Check VDD=3.3V, SD=GPIO7, SCK=GPIO4, WS=GPIO5, L/R=GND.")
elif rms < 0.005:
    print("-> Too quiet: the voice check-in treats this as silence (silence_rms 0.005 in config.yaml).")
    print("   Speak closer, or lower silence_rms (e.g. 0.002).")
elif peak > 0.99:
    print("-> Clipping (distorted). Mic works but is too loud; it should still transcribe.")
else:
    print("-> Mic looks OK.")
