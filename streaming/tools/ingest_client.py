"""ingest_client.py: Python version of what the watch does when it sends a message.

WHAT THIS FILE DOES
    Builds the same JSON the watch's silverguard_stream.h builds and POSTs it to the
    ingest service (/ingest) with the device key. Used by:
      - tools/send_test_event.py   send one test message from the command line
      - bridge/usb_to_cloud.py     forward a USB-connected watch's data online
    Standard library only, so it runs anywhere without installing anything.

    from ingest_client import IngestClient
    c = IngestClient("http://localhost:8080", key="dev")
    c.send("activity", state="moving")      # -> (200, {"ok": True, "duplicate": False})
"""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request


class IngestClient:
    """One client = one "boot": a random boot_id and a seq counting from 0, like the watch."""

    def __init__(self, url: str, key: str, device_id: str = "watch-01", timeout: float = 10.0):
        self.url = url.rstrip("/")
        if self.url.endswith("/ingest"):
            self.url = self.url[: -len("/ingest")]
        self.key = key
        self.device_id = device_id
        self.timeout = timeout
        self.boot_id = secrets.token_hex(4)
        self.seq = 0
        self._t0 = time.monotonic()

    def build(self, type_: str, **fields) -> dict:
        """The JSON body for one message. Uses up a sequence number."""
        body = {"device_id": self.device_id, "type": type_, "boot_id": self.boot_id,
                "seq": self.seq, "uptime_ms": int((time.monotonic() - self._t0) * 1000)}
        body.update({k: v for k, v in fields.items() if v is not None})
        self.seq += 1
        return body

    def post(self, body: dict) -> tuple[int, dict]:
        """POST a built body. Returns (HTTP status, JSON reply). Status 0 = no connection."""
        req = urllib.request.Request(
            self.url + "/ingest", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Device-Key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": str(e)}

    def send(self, type_: str, **fields) -> tuple[int, dict]:
        return self.post(self.build(type_, **fields))
