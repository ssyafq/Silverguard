"""Tests for the laptop → Supabase uploader and the demo's cloud/alert wiring.
Uses a fake database client, so no Supabase account or internet is needed.

    python -m pytest tests/test_sync.py -v
"""

import pytest

from basestation.sync.uploader import Cloud


class FakeDB:
    """Pretends to be the Supabase client; records every write in .log."""

    def __init__(self):
        self.offline = False
        self.log = []

    def table(self, name):
        return _FakeQuery(self, name)


class _FakeQuery:
    def __init__(self, db, name):
        self.db, self.name, self.op = db, name, None

    def upsert(self, row):
        self.op = ("upsert", row)
        return self

    def update(self, fields):
        self.op = ("update", fields)
        return self

    def eq(self, column, value):
        self.op = (*self.op, value)
        return self

    def execute(self):
        if self.db.offline:
            raise ConnectionError("simulated: no internet")
        self.db.log.append((self.name, *self.op))


def test_rows_wait_while_offline_then_upload_in_order(tmp_path):
    db = FakeDB()
    cloud = Cloud(outbox=tmp_path / "outbox.jsonl", client=db, start_thread=False)
    db.offline = True
    cloud.insert("events", {"id": "e1", "status": "open"})
    cloud.step("e1", "Fall detected")
    cloud.update("events", "e1", {"alerted_at": "now"})
    assert cloud.flush() == 0 and cloud.pending() == 3          # nothing lost

    db.offline = False
    assert cloud.flush() == 3 and cloud.pending() == 0
    assert [entry[0] for entry in db.log] == ["events", "event_steps", "events"]   # same order
    assert db.log[2] == ("events", "update", {"alerted_at": "now"}, "e1")


def test_damaged_outbox_line_is_skipped(tmp_path):
    db = FakeDB()
    box = tmp_path / "outbox.jsonl"
    box.write_text('{"op": "upsert", "table": "events", "row": {"id": "e1"}}\n{broken\n', encoding="utf-8")
    cloud = Cloud(outbox=box, client=db, start_thread=False)
    assert cloud.flush() == 2 and len(db.log) == 1


def test_no_keys_means_local_only(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    cloud = Cloud(outbox=tmp_path / "outbox.jsonl", start_thread=False)
    cloud.insert("events", {"id": "e1"})
    assert not cloud.enabled and cloud.pending() == 0


@pytest.fixture
def quiet_demo(monkeypatch, tmp_path):
    """Run the demo with no speaker, no LLM call, no Telegram, and the CSV log in a temp folder."""
    from basestation.voice import checkin, demo, speak
    from basestation.voice.keywords import classify_keywords
    from basestation.voice.understand import Classification

    monkeypatch.setattr(speak, "say_line", lambda name: {"engine": "test", "seconds": 0.0})
    monkeypatch.setattr(checkin, "classify", lambda text, reason: Classification(classify_keywords(text), False, "", 0.5))
    monkeypatch.setattr(checkin, "LOG_PATH", tmp_path / "voice_log.csv")
    sent = []
    monkeypatch.setattr(demo.telegram, "send_alert", lambda text: (sent.append(text), (True, "sent"))[1])
    return demo, sent


def test_demo_help_reply_alerts_and_uploads(quiet_demo, monkeypatch, tmp_path):
    demo, sent = quiet_demo
    monkeypatch.setattr("builtins.input", lambda prompt="": "help me, 好痛")
    db = FakeDB()
    cloud = Cloud(outbox=tmp_path / "outbox.jsonl", client=db, start_thread=False)

    assert demo.handle_event("fall", typed=True, cloud=cloud) == "alert_help"
    cloud.flush()

    assert len(sent) == 1 and "asked for help" in sent[0]
    event = next(e for e in db.log if e[0] == "events" and e[1] == "upsert")[2]
    assert event["status"] == "open" and event["type"] == "fall"
    checkin_row = next(e for e in db.log if e[0] == "checkins")[2]
    assert checkin_row["reply"] == "help" and checkin_row["transcript"] == "help me, 好痛"
    assert any(e[0] == "events" and e[1] == "update" and "alerted_at" in e[2] for e in db.log)
    steps = [e[2]["text"] for e in db.log if e[0] == "event_steps"]
    assert steps[-1] == "Caregiver alerted on Telegram"


def test_demo_ok_reply_is_false_alarm_without_alert(quiet_demo, monkeypatch, tmp_path):
    demo, sent = quiet_demo
    monkeypatch.setattr("builtins.input", lambda prompt="": "我没事, I'm okay")
    db = FakeDB()
    cloud = Cloud(outbox=tmp_path / "outbox.jsonl", client=db, start_thread=False)

    assert demo.handle_event("fall", typed=True, cloud=cloud) == "no_alert"
    cloud.flush()
    assert sent == []
    assert any(e[0] == "events" and e[1] == "update" and e[2].get("status") == "false_alarm" for e in db.log)
