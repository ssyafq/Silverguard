"""The real watch detects a fall over USB → voice check-in on the WATCH's speaker + mic → Telegram alert.

    python -m basestation.voice.watch_bridge                 # finds the watch's COM port by itself
    python -m basestation.voice.watch_bridge --port COM5
    python -m basestation.voice.watch_bridge --manual        # press Enter to test a check-in, no fall needed
    python -m basestation.voice.watch_bridge --laptop-audio  # old firmware: laptop speaker + mic instead

Flow:  drop/fall the watch → firmware prints "FALL DETECTED" and pauses
       → watch speaker says "Are you okay?" → watch mic records the reply (streamed to the laptop)
       → Whisper + Claude sort it into ok / help / none
       → help or no reply: Telegram alert to the caregiver (+ dashboard rows if set up in .env)
       → sends 'r' so the watch resumes.

Safety: if the watch's audio fails mid check-in, the caregiver is still alerted (demo.handle_event),
and if the watch is unplugged, a Telegram message says so.

Firmware: Melv-Codes/ESP32-SpeakerUSBVer (INMP441 mic + MAX98357A speaker; wiring in its WIRING.md).
With --laptop-audio, firmware/silverguard_logger (v0.4) also works. Close the Arduino Serial Monitor
first — only one program can open the COM port.

This is a LAPTOP STAND-IN for the Railway server: the watch talks over the USB cable here.
On the real system the watch sends the event over Wi-Fi (Obj 1 / Obj 3).
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

import serial                                   # pip install pyserial
import soundfile as sf

from basestation.alerts import telegram
from basestation.sync.uploader import Cloud, now_iso
from basestation.voice import esp32_audio, listen, speak
from basestation.voice.demo import handle_event
from basestation.voice.settings import dashboard_config

FALL_MARKERS = ("FALL DETECTED", "# EVENT FALL")
DETECTOR = "ESP32-SpeakerUSBVer v0.5 (USB)"


# ---------- route speak / listen through the watch's speaker + mic ----------

def _watch_play_file(path) -> None:
    data, samplerate = sf.read(str(path), dtype="float32")
    if not esp32_audio.play_audio(data, samplerate):
        raise RuntimeError("watch speaker playback failed over USB")


def _watch_speak_offline(text: str, lang: str) -> None:
    """Offline backup voice (pyttsx3) rendered to a WAV, then played on the watch speaker."""
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
        _watch_play_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _watch_record(seconds: float):
    audio = esp32_audio.record_audio(seconds, samplerate=listen.SAMPLE_RATE)
    if audio is None:
        raise RuntimeError("watch mic recording failed over USB")
    return audio


def use_watch_audio() -> None:
    speak._play_file = _watch_play_file
    speak._speak_offline = _watch_speak_offline
    listen.record = _watch_record


# ---------- main loop ----------

def resume(ser: serial.Serial) -> None:
    ser.reset_input_buffer()                     # drop the alert lines repeated while we were talking
    ser.write(b"r")                              # tell the watch to resume detecting
    ser.flush()


def run_check_in(cloud: Cloud, ser: serial.Serial, source: str) -> None:
    t0 = time.perf_counter()
    outcome = handle_event("fall", typed=False, cloud=cloud, detector=DETECTOR, source=source)
    print(f"\n[done] outcome={outcome}  (fall -> end of check-in: {time.perf_counter() - t0:.1f} s)")
    resume(ser)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch over USB -> voice check-in -> Telegram alert")
    parser.add_argument("--port", default="auto", help="watch COM port, e.g. COM5 (default: auto-detect)")
    parser.add_argument("--manual", action="store_true", help="press Enter to run a check-in instead of waiting for a fall")
    parser.add_argument("--laptop-audio", action="store_true", help="use the laptop speaker + mic (old v0.4 firmware)")
    parser.add_argument("--quiet", action="store_true", help="don't echo the watch's lines")
    args = parser.parse_args()

    ser = esp32_audio.get_serial(args.port)
    if ser is None:
        sys.exit("Couldn't open the watch's COM port.\n"
                 "1. Plug the watch in.  2. Close the Arduino Serial Monitor.  3. Try --port COMx.")
    if not args.laptop_audio:
        use_watch_audio()

    cloud = Cloud()
    print("Watch:            ", ser.port, "(laptop speaker + mic)" if args.laptop_audio else "(watch speaker + mic)")
    print("Telegram alerts:  ", "ON" if telegram.is_configured() else "OFF (see basestation/alerts/telegram.py)")
    print("Online dashboard: ", "ON (Supabase)" if cloud.enabled else "OFF")
    print("Getting ready: spoken lines + speech-to-text model (first run downloads it)...")
    speak.prepare_all_lines()
    listen.load_model()
    resume(ser)                                  # in case the watch is still paused from an earlier fall

    try:
        if args.manual:
            print("\nManual mode. Press Enter to run a check-in on the watch, or type 'quit'.")
            while input("\n> ").strip().lower() not in ("quit", "exit", "q"):
                run_check_in(cloud, ser, source="triggered manually over USB")
        else:
            print(f"\nListening to the watch on {ser.port}. Make it fall (Ctrl+C to stop).\n")
            while True:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                cloud.watch_last_data_at = now_iso()  # dashboard: watch is connected
                if not args.quiet:
                    print(f"  watch | {line}")
                if any(m in line for m in FALL_MARKERS):
                    print("\n>>> Fall detected by the watch - starting the voice check-in\n")
                    run_check_in(cloud, ser, source="detected by the watch")
                    print("Watch resumed. Make it fall again, or Ctrl+C to stop.\n")
    except KeyboardInterrupt:
        pass
    except serial.SerialException as e:
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
