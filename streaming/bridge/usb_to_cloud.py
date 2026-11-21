"""usb_to_cloud.py: stream a USB-connected watch online, for while the watch has no Wi-Fi yet.

WHAT THIS FILE DOES
    Reads the normal watch firmware (firmware/FinalPrototype) over the USB cable, picks out
    the still/moving result it prints every 0.5 s, and sends the same messages the Wi-Fi
    watch would send, to the same /ingest:
      - an "activity" message only when the state changes (same 3 s debounce as the watch)
      - a "fall" message when the watch prints a confirmed fall
      - a "heartbeat" every 60 s
    Messages that fail (no internet) are kept and retried in order.
    The server and database can't tell the difference, so nothing changes later when the
    watch sends over Wi-Fi itself (firmware/watch_streaming).

    Only ONE program can open the watch's COM port: stop watch_bridge.py while this runs.
    There is no voice check-in here. After a fall the watch stops sampling until it gets 'r',
    so this script sends 'r' after --resume-after seconds.

    python streaming/bridge/usb_to_cloud.py --port COM5 --url https://<service>.run.app --key <key>
    python streaming/bridge/usb_to_cloud.py --port COM5 --url http://localhost:8080 --key dev
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ingest_client import IngestClient  # noqa: E402

FALL_MARKERS = ("FALL DETECTED", "# EVENT FALL")      # same as basestation/voice/watch_bridge.py
_READABLE = re.compile(r"Activity:\s*(Still|Moving)\b", re.IGNORECASE)   # RAW_STREAM 0
_RAW = re.compile(r"^# result,\d+,([01]),")                               # RAW_STREAM 1
_HAR = {"0": "still", "1": "moving"}


def parse_line(line: str) -> str | None:
    """'still' / 'moving' / 'fall' for a line that matters, else None."""
    if any(m in line for m in FALL_MARKERS):
        return "fall"
    m = _READABLE.search(line)
    if m:
        return m.group(1).lower()
    m = _RAW.match(line)
    if m:
        return _HAR[m.group(1)]
    return None


class Debouncer:
    """Same rule as sgstream::onHar(): a new state must be seen `confirm` times in a row."""

    def __init__(self, confirm: int = 6):
        self.confirm = confirm
        self.sent: str | None = None
        self._cand: str | None = None
        self._count = 0

    def update(self, state: str) -> str | None:
        """Feed one result. Returns the state when it becomes a confirmed change, else None."""
        if state == self.sent:
            self._cand, self._count = None, 0
            return None
        if state != self._cand:
            self._cand, self._count = state, 1
        else:
            self._count += 1
        if self._count >= self.confirm:
            self.sent, self._cand, self._count = state, None, 0
            return state
        return None


class Outbox:
    """Messages waiting to be sent, oldest first; retried with backoff like the watch."""

    def __init__(self, client: IngestClient, maxlen: int = 500):
        self.client = client
        self.q: deque[dict] = deque(maxlen=maxlen)
        self._next_try = 0.0
        self._backoff = 1.0

    def add(self, type_: str, **fields) -> None:
        self.q.append(self.client.build(type_, **fields))

    def flush(self) -> None:
        while self.q and time.monotonic() >= self._next_try:
            body = self.q[0]
            code, reply = self.client.post(body)
            if 200 <= code < 300 or code in (401, 422):
                self.q.popleft()
                self._backoff = 1.0
                if code in (401, 422):
                    print(f"  cloud | refused ({code} {reply}), dropped: check --key / --device")
                else:
                    print(f"  cloud | sent {body['type']} {body.get('state', '')}".rstrip())
            else:
                print(f"  cloud | send failed ({code}), {len(self.q)} waiting, retry in {self._backoff:.0f} s")
                self._next_try = time.monotonic() + self._backoff
                self._backoff = min(self._backoff * 2, 30.0)
                return


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True, help="watch COM port, e.g. COM5")
    p.add_argument("--url", default=os.environ.get("STREAM_URL", "http://localhost:8080"))
    p.add_argument("--key", default=os.environ.get("STREAM_DEVICE_KEY", ""))
    p.add_argument("--device", default="watch-01")
    p.add_argument("--confirm", type=int, default=6, help="results in a row before a change counts")
    p.add_argument("--heartbeat", type=float, default=60.0, help="seconds between heartbeats")
    p.add_argument("--resume-after", type=float, default=15.0,
                   help="seconds after a fall before sending 'r' to resume the watch (0 = never)")
    p.add_argument("--quiet", action="store_true", help="don't echo every watch line")
    a = p.parse_args()

    import serial  # pyserial

    client = IngestClient(a.url, a.key, a.device)
    out = Outbox(client)
    deb = Debouncer(a.confirm)
    ser = serial.Serial(a.port, 115200, timeout=0.5)
    print(f"Watch on {a.port} -> {client.url}/ingest as {a.device} (boot {client.boot_id}). Ctrl+C to stop.")

    last_hb = time.monotonic()
    resume_at = None
    halted = False
    try:
        while True:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                if not a.quiet:
                    print(f"  watch | {line}")
                kind = parse_line(line)
                if kind == "fall":
                    if not halted:                     # one fall prints the alert line many times
                        halted = True
                        out.add("fall", state=deb.sent, detector="FinalPrototype (USB)")
                        if a.resume_after > 0:
                            resume_at = time.monotonic() + a.resume_after
                elif kind:
                    halted = False                     # results again = the watch resumed
                    changed = deb.update(kind)
                    if changed:
                        out.add("activity", state=changed, detector="har_v0.5 (USB)")
            now = time.monotonic()
            if resume_at is not None and now >= resume_at:
                ser.write(b"r")
                print("  watch | sent 'r': resume monitoring")
                resume_at = None
            if now - last_hb >= a.heartbeat:
                last_hb = now
                if not out.q:
                    out.add("heartbeat", state=deb.sent)
            out.flush()
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        if out.q:
            print(f"Stopped with {len(out.q)} message(s) not sent.")


if __name__ == "__main__":
    main()
