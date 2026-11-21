"""test_bridge.py: tests for the USB stopgap (bridge/usb_to_cloud.py) and tools/ingest_client.py.

WHAT THIS FILE DOES
    Checks, without a watch or a server, that the watch's serial lines are read correctly,
    that the debounce only reports real changes (the same rule as the watch firmware), that
    failed sends are kept and retried in order, and that messages get the right fields.

    python -m pytest streaming/tests -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bridge"))
import usb_to_cloud as b  # noqa: E402
from ingest_client import IngestClient  # noqa: E402


@pytest.mark.parametrize("line, want", [
    ("[   12.5 s]  Activity: Moving  | peak  1.20 g | fall check: -", "moving"),
    ("[   13.0 s]  Activity: Still   | peak  1.01 g | fall check: score 0.10 (alarm at 0.50) -> not a fall", "still"),
    ("# result,1042,1,1.33,-1.000,0", "moving"),
    ("# result,1043,0,1.00,-1.000,0", "still"),
    ("ALERT ALERT ALERT ALERT ALERT ALERT ALERT FALL DETECTEDDD!!!", "fall"),
    ("# EVENT FALL,1200,0.910,peak_g=3.20,turn_deg=40.0,min_g_before=0.4,max_dps_before=300", "fall"),
    ("# EVENT REJECTED,1200,0.910,peak_g=3.20,turn_deg=5.0", None),
    ("# stream: activity -> moving", None),
    ("# status ...", None),
])
def test_parse_line(line, want):
    assert b.parse_line(line) == want


def test_debounce_needs_n_in_a_row():
    d = b.Debouncer(confirm=3)
    seq = ["still"] * 3 + ["moving", "still", "moving", "moving", "moving", "moving"]
    got = [d.update(s) for s in seq]
    # still confirmed on the 3rd; the single "moving" flicker is ignored; moving confirmed on its 3rd in a row
    assert got == [None, None, "still", None, None, None, None, "moving", None]


def test_debounce_resets_when_back_to_sent_state():
    d = b.Debouncer(confirm=2)
    d.update("still")
    d.update("still")
    assert [d.update(s) for s in ["moving", "still", "moving"]] == [None, None, None]


class FakeClient(IngestClient):
    def __init__(self, replies):
        super().__init__("http://x", "k")
        self.replies, self.sent = list(replies), []

    def post(self, body):
        self.sent.append(body)
        return self.replies.pop(0)


def test_outbox_keeps_order_and_retries(monkeypatch):
    c = FakeClient([(0, {}), (200, {}), (200, {})])
    out = b.Outbox(c)
    out.add("activity", state="moving")
    out.add("heartbeat", state="moving")
    t = [100.0]
    monkeypatch.setattr(b.time, "monotonic", lambda: t[0])
    out.flush()                                # fails: both kept, backoff started
    assert len(out.q) == 2
    out.flush()                                # too early: nothing sent
    assert len(c.sent) == 1
    t[0] += 1.5
    out.flush()                                # retry succeeds, then the next one
    assert not out.q and [m["seq"] for m in c.sent] == [0, 0, 1]


def test_outbox_drops_refused():
    out = b.Outbox(FakeClient([(401, {"detail": "bad device key"})]))
    out.add("activity", state="still")
    out.flush()
    assert not out.q


def test_client_builds_watch_shaped_messages():
    c = IngestClient("https://svc.run.app/ingest", "k", "watch-02")
    assert c.url == "https://svc.run.app"
    m0, m1 = c.build("activity", state="moving", confidence=None), c.build("heartbeat")
    assert m0["device_id"] == "watch-02" and m0["state"] == "moving" and "confidence" not in m0
    assert (m0["seq"], m1["seq"]) == (0, 1) and m0["boot_id"] == m1["boot_id"] and len(m0["boot_id"]) == 8
