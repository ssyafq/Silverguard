"""USB Voice Bridge for ESP32-SpeakerUSBVer (INMP441 Mic + MAX98357A Speaker).

Routes the SilverGuard voice check-in pipeline (`basestation.voice`) through the ESP32's
MAX98357A speaker and INMP441 microphone over USB Serial instead of the laptop's speaker/mic.

Usage (from the repo root):
    # 1. Watch mode (waits for the ESP32 to detect a fall, then runs the voice check-in on the watch):
    python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py

    # 2. Specify COM port explicitly:
    python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py --port COM5

    # 3. Manual test mode (press Enter to trigger a check-in on the ESP32 speaker + mic immediately):
    python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py --manual
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import serial
import soundfile as sf

# Ensure repo root and this folder are importable regardless of working directory
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import esp32_audio  # noqa: E402
from basestation.alerts import telegram  # noqa: E402
from basestation.sync.uploader import Cloud, now_iso  # noqa: E402
from basestation.voice import demo, listen, speak  # noqa: E402
from basestation.voice.settings import dashboard_config  # noqa: E402

FALL_MARKERS = ("FALL DETECTED", "# EVENT FALL")

_orig_play_file = speak._play_file
_orig_speak_offline = speak._speak_offline
_orig_record = listen.record


def _esp32_play_file(path) -> None:
    """Play TTS audio file on the ESP32 MAX98357A speaker over USB Serial."""
    data, samplerate = sf.read(str(path), dtype="float32")
    if esp32_audio.play_audio(data, samplerate):
        return
    raise RuntimeError("ESP32 MAX98357A playback failed over USB Serial")


def _esp32_speak_offline(text: str, lang: str) -> None:
    """Synthesise offline pyttsx3 speech to a temp WAV and stream it to the ESP32 speaker."""
    import pyttsx3

    engine = pyttsx3.init()
    if lang == "zh":
        zh = [v for v in engine.getProperty("voices")
              if "chinese" in v.name.lower() or "zh" in v.id.lower()]
        if not zh:
            engine.stop()
            raise RuntimeError("no offline Chinese voice installed")
        engine.setProperty("voice", zh[0].id)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        engine.save_to_file(text, str(tmp_path))
        engine.runAndWait()
        engine.stop()
        _esp32_play_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _esp32_record(seconds: float):
    """Record 16 kHz mono audio from the ESP32 INMP441 microphone over USB Serial."""
    audio = esp32_audio.record_audio(seconds, samplerate=listen.SAMPLE_RATE)
    if audio is not None:
        return audio
    raise RuntimeError("ESP32 INMP441 mic recording failed over USB Serial")


def install_esp32_audio_hooks() -> None:
    """Redirect basestation.voice.speak and basestation.voice.listen to the ESP32 over USB."""
    speak._play_file = _esp32_play_file
    speak._speak_offline = _esp32_speak_offline
    listen.record = _esp32_record


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SilverGuard voice check-in using ESP32 MAX98357A speaker + INMP441 mic over USB"
    )
    parser.add_argument("--port", default="auto", help="ESP32 COM port, e.g. COM5 (default: auto-detect)")
    parser.add_argument("--manual", action="store_true", help="trigger check-in by pressing Enter instead of waiting for a fall")
    parser.add_argument("--typed", action="store_true", help="type the reply instead of recording from INMP441 mic")
    parser.add_argument("--quiet", action="store_true", help="don't echo the watch's IMU lines")
    args = parser.parse_args()

    ser = esp32_audio.get_serial(args.port)
    if ser is None:
        sys.exit(
            "Could not open the ESP32 serial port.\n"
            "1. Make sure the ESP32-C3 is plugged in via USB.\n"
            "2. Close the Arduino Serial Monitor (only one program can use the COM port).\n"
            "3. Try passing --port COMx explicitly."
        )

    install_esp32_audio_hooks()

    cloud = Cloud()
    print(f"ESP32 USB Audio:  ON ({ser.port} -> MAX98357A speaker + INMP441 mic)")
    print("Telegram alerts: ", "ON" if telegram.is_configured() else "OFF (see basestation/alerts/telegram.py)")
    print("Online dashboard:", "ON (Supabase)" if cloud.enabled else "OFF")
    print("Getting ready: spoken lines + speech-to-text model (first run downloads it)...")
    speak.prepare_all_lines()
    if not args.typed:
        listen.load_model()

    ser.write(b"r")  # resume watch if it was paused from an earlier fall
    ser.flush()

    try:
        if args.manual:
            print("\nManual mode ready. Press Enter to run a check-in on the ESP32 speaker + mic, or 'quit'.")
            while True:
                event = input("\nevent [fall]> ").strip().lower() or "fall"
                if event in ("quit", "exit", "q"):
                    break
                t0 = time.perf_counter()
                outcome = demo.handle_event(
                    event,
                    typed=args.typed,
                    cloud=cloud,
                    detector="ESP32-SpeakerUSBVer (manual)",
                    source="triggered manually over USB",
                )
                print(f"[done] outcome={outcome} ({time.perf_counter() - t0:.1f} s)")
                ser.reset_input_buffer()
                ser.write(b"r")
                ser.flush()
        else:
            print(f"\nListening to the watch on {ser.port}. Make it fall (Ctrl+C to stop).\n")
            while True:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                cloud.watch_last_data_at = now_iso()
                if not args.quiet:
                    print(f"  watch | {line}")
                if any(m in line for m in FALL_MARKERS):
                    print("\n>>> Fall detected by the watch - starting voice check-in on ESP32 speaker + mic\n")
                    t0 = time.perf_counter()
                    outcome = demo.handle_event(
                        "fall",
                        typed=args.typed,
                        cloud=cloud,
                        detector="ESP32-SpeakerUSBVer v0.5 (USB)",
                        source="detected by the watch",
                    )
                    print(f"\n[done] outcome={outcome}  (fall -> end of check-in: {time.perf_counter() - t0:.1f} s)")
                    ser.reset_input_buffer()
                    ser.write(b"r")
                    ser.flush()
                    print("Watch resumed. Make it fall again, or Ctrl+C to stop.\n")
    except KeyboardInterrupt:
        pass
    except serial.SerialException as e:
        # watch unplugged / USB dropped: tell the caregiver that falls are no longer being detected
        print(f"\nLost the watch ({type(e).__name__}). Was the cable unplugged?")
        sent, info = telegram.send_alert(
            f"⚠️ SilverGuard: lost connection to {dashboard_config()['elder_name']}'s watch. "
            "Falls are NOT being detected until it is reconnected.")
        print("Caregiver told on Telegram." if sent else f"Telegram NOT sent: {info}.")
    finally:
        esp32_audio.close_serial()
        cloud.close()


if __name__ == "__main__":
    main()
