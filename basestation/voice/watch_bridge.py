"""Laptop test: the real watch detects a fall over USB → the laptop runs the voice check-in.

    python -m basestation.voice.watch_bridge              # finds the watch's COM port by itself
    python -m basestation.voice.watch_bridge --port COM5

Flow:  drop/fall the watch → firmware prints "FALL DETECTED" and pauses
       → laptop says "Are you okay?" → listens 7 s → Claude sorts the reply → spoken answer
       (+ dashboard rows / Telegram alert if set up in .env) → sends 'r' so the watch resumes.

This is a LAPTOP STAND-IN for the Railway server: here the watch talks over the USB cable and the
laptop's own mic + speaker are used. On the real system the watch sends the event over Wi-Fi and
uses its own speaker + mic (Obj 1 / Obj 3).

Firmware: flash silverguard_logger.ino with RAW_STREAM 0 and HALT_ON_FALL 1 (the defaults).
Close the Arduino Serial Monitor first — only one program can open the COM port.
"""

import argparse
import sys
import time

import serial                                   # pip install pyserial
from serial.tools import list_ports

from basestation.sync.uploader import Cloud, now_iso
from basestation.voice import listen, speak
from basestation.voice.demo import handle_event

FALL_MARKERS = ("FALL DETECTED", "# EVENT FALL")


def find_port() -> str:
    ports = list(list_ports.comports())
    for p in ports:                              # ESP32-C3 native USB = Espressif VID 0x303A
        if p.vid == 0x303A or "USB Serial" in (p.description or "") or "JTAG" in (p.description or ""):
            return p.device
    if len(ports) == 1:
        return ports[0].device
    names = ", ".join(f"{p.device} ({p.description})" for p in ports) or "none"
    sys.exit(f"Couldn't pick the watch's port automatically. Ports found: {names}\n"
             f"Plug the watch in, then run again with --port COMx.")


def open_port(port: str) -> serial.Serial:
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 1
    ser.dtr = ser.rts = False                    # don't reset the ESP32 when opening the port
    ser.open()
    return ser


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch over USB -> voice check-in on the laptop")
    parser.add_argument("--port", help="watch COM port, e.g. COM5 (default: auto-detect)")
    parser.add_argument("--quiet", action="store_true", help="don't echo the watch's lines")
    args = parser.parse_args()

    cloud = Cloud()
    print("Online dashboard:", "ON (Supabase)" if cloud.enabled else "OFF")
    print("Getting ready: spoken lines + speech-to-text model (first run downloads it)...")
    speak.prepare_all_lines()
    listen.load_model()

    port = args.port or find_port()
    ser = open_port(port)
    ser.write(b"r")                              # in case the watch is still paused from an earlier fall
    print(f"\nListening to the watch on {port}. Make it fall (Ctrl+C to stop).\n")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            cloud.watch_last_data_at = now_iso()  # dashboard: watch is connected
            if not args.quiet:
                print(f"  watch | {line}")
            if any(m in line for m in FALL_MARKERS):
                print("\n>>> Fall detected by the watch - starting the voice check-in\n")
                t0 = time.perf_counter()
                outcome = handle_event("fall", typed=False, cloud=cloud,
                                       detector="watch fw v0.4 (USB)", source="detected by the watch")
                print(f"\n[done] outcome={outcome}  (fall -> end of check-in: {time.perf_counter() - t0:.1f} s)")
                ser.reset_input_buffer()          # drop the alert lines repeated while we were talking
                ser.write(b"r")                   # tell the watch to resume detecting
                print("Watch resumed. Make it fall again, or Ctrl+C to stop.\n")
    except KeyboardInterrupt:
        pass
    except serial.SerialException as e:
        print(f"\nLost the watch ({e}). Was the cable unplugged?")
    finally:
        ser.close()
        cloud.close()


if __name__ == "__main__":
    main()
