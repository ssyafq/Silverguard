"""test_ingest.py: tests for the Cloud Run ingest service (server/main.py).

WHAT THIS FILE DOES
    Sends fake watch messages to the service through FastAPI's TestClient, using the
    in-memory store, so no Google account or internet is needed. Checks that changes,
    falls and heartbeats are stored right, retries are not duplicated, and bad keys or bad
    data are refused.

    python -m pytest streaming/tests -q
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["SILVERGUARD_NO_AUTOAPP"] = "1"          # don't build the Firestore app at import
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import main  # noqa: E402

KEY = {"X-Device-Key": "secret-1"}


@pytest.fixture
def setup():
    store = main.MemoryStore()
    client = TestClient(main.create_app(store=store, device_keys={"watch-01": "secret-1"}))
    return client, store


def ev(type_, seq, **kw):
    return {"device_id": "watch-01", "type": type_, "boot_id": "ab12cd34", "seq": seq,
            "uptime_ms": 1000 + seq, **kw}


def test_health(setup):
    client, _ = setup
    assert client.get("/health").json() == {"ok": True}


def test_activity_change_updates_state_and_logs_transition(setup):
    client, store = setup
    r = client.post("/ingest", json=ev("activity", 0, state="moving"), headers=KEY)
    assert r.status_code == 200 and r.json() == {"ok": True, "duplicate": False}
    assert store.devices["watch-01"]["state"] == "moving"
    assert "since" in store.devices["watch-01"]
    assert list(store.transitions["watch-01"]) == ["ab12cd34-0"]


def test_retry_is_not_duplicated_and_does_not_move_since(setup):
    client, store = setup
    client.post("/ingest", json=ev("activity", 3, state="still"), headers=KEY)
    since = store.devices["watch-01"]["since"]
    r = client.post("/ingest", json=ev("activity", 3, state="still"), headers=KEY)
    assert r.json()["duplicate"] is True
    assert len(store.transitions["watch-01"]) == 1
    assert store.devices["watch-01"]["since"] == since


def test_heartbeat_only_touches_last_seen(setup):
    client, store = setup
    client.post("/ingest", json=ev("heartbeat", 0, state="still"), headers=KEY)
    d = store.devices["watch-01"]
    assert "last_seen" in d and "state" not in d
    assert d["heartbeat_state"] == "still"
    assert store.transitions == {}


def test_fall_creates_open_event(setup):
    client, store = setup
    client.post("/ingest", json=ev("fall", 7, confidence=0.91, peak_g=3.2, turn_deg=40.0), headers=KEY)
    e = store.events["ab12cd34-7"]
    assert e["type"] == "fall" and e["status"] == "open" and e["confidence"] == 0.91
    assert "last_fall_at" in store.devices["watch-01"]


@pytest.mark.parametrize("headers", [{}, {"X-Device-Key": "wrong"}])
def test_bad_key_refused(setup, headers):
    client, store = setup
    r = client.post("/ingest", json=ev("activity", 0, state="moving"), headers=headers)
    assert r.status_code == 401 and store.devices == {}


def test_unknown_device_refused(setup):
    client, _ = setup
    body = ev("activity", 0, state="moving") | {"device_id": "watch-99"}
    assert client.post("/ingest", json=body, headers=KEY).status_code == 401


@pytest.mark.parametrize("body", [
    ev("activity", 0),                        # activity without a state
    ev("activity", 0, state="running"),       # unknown state
    ev("dance", 0),                           # unknown type
    ev("activity", -1, state="still"),        # negative seq
])
def test_bad_payload_rejected(setup, body):
    client, _ = setup
    assert client.post("/ingest", json=body, headers=KEY).status_code == 422


def test_parse_device_keys():
    assert main.parse_device_keys("watch-01=a, watch-02=b=c,,junk") == {"watch-01": "a", "watch-02": "b=c"}
