"""Tests for the caregiver dashboard (sample data, no internet needed).

    python -m pytest tests/test_dashboard.py -v
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from basestation.dashboard import metrics as M
from basestation.dashboard import sample_data

NOW = datetime(2026, 11, 10, 7, 15, 30, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)


@pytest.fixture(scope="module")
def data():
    return sample_data.build(NOW)


def test_newest_event_is_open_fall_without_reply(data):
    e = M.active_alert(data["events"])
    assert e["type"] == "fall" and e["status"] == "open" and e["alerted_at"]
    assert M.what_they_said(e, data["checkins"]) == "(no reply, asked twice)"
    assert M.outcome(e) == ("Open · caregiver alerted", "alert")


def test_outcome_labels(data):
    by_title = {M.what_they_said(e, data["checkins"]): M.outcome(e)[0] for e in data["events"]}
    assert by_title["“I'm okay, I just sat down too hard”"] == "False alarm · they said OK"
    assert by_title["“I was having a nap”"] == "Resolved"
    assert by_title["“My knee hurts, I can't get up”"] == "Resolved · acked in 2 min"


def test_week_counts(data):
    c = M.week_counts(data["events"], NOW)
    assert (c["open"], c["false_alarm"], c["resolved"]) == (1, 1, 2)


def test_health_numbers(data):
    h = M.health(data["events"], data["checkins"], data["activity"], NOW)
    assert round(h["false_alarms_per_day"], 2) == 0.14
    assert M.fmt_dur(h["caregiver_response_s"]) == "2 m 10 s"
    assert 98 < h["uptime_pct"] < 100          # two short sample gaps


def test_activity_runs_do_not_overlap(data):
    runs = M.activity_runs(data["activity"], NOW - timedelta(hours=24), NOW)
    assert runs and all(a < b for a, b, _ in runs)
    assert all(r1[1] <= r2[0] for r1, r2 in zip(runs, runs[1:]))


def test_sleep_card(data):
    s = M.sleep_last_night(data["activity"], NOW, 8)
    assert 6 * 60 < s["total_min"] < 9 * 60 and s["wakeups"] >= 1


def test_dead_base_station_is_never_ok():
    status = {"base_station_seen_at": NOW - timedelta(minutes=10), "watch_last_data_at": None, "battery_pct": None}
    c = M.connection_state(status, NOW, 120, 120)
    assert not c["base_ok"] and c["watch_state"] == "never"
    assert not M.connection_state({}, NOW, 120, 120)["base_ok"]      # never seen = not ok


def test_formatting():
    assert M.fmt_hm(267) == "4 h 27 m"
    assert M.fmt_dur(20) == "20 s" and M.fmt_dur(130) == "2 m 10 s"
    assert M.fmt_event_time(NOW - timedelta(days=1), NOW, 8).startswith("Yest")


# ── reading the real database: rows arrive as JSON text ─────────────────────

class _Rows:
    """Minimal stand-in for a Supabase query: ignores filters, returns stored rows."""
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):              # select / eq / gte / order / limit / in_ / range
        return lambda *a, **k: self

    def execute(self):
        return type("Resp", (), {"data": [dict(r) for r in self.rows]})()


def test_supabase_rows_are_parsed():
    from basestation.dashboard.data import SupabaseSource
    tables = {
        "status": [{"id": 1, "base_station_seen_at": "2026-11-09T23:15:00+00:00", "watch_last_data_at": None,
                    "battery_pct": None}],
        "events": [{"id": "e1", "t": "2026-11-09T23:14:10.123+00:00", "type": "fall", "confidence": None,
                    "detector": "typed demo", "status": "open", "duration_min": None, "snippet": None,
                    "alerted_at": "2026-11-09T23:14:31+00:00", "acknowledged_at": None, "resolved_at": None}],
        "checkins": [{"id": "c1", "event_id": "e1", "t": "2026-11-09T23:14:20+00:00", "attempt": 1,
                      "transcript": "help me", "reply": "help", "used_fallback": False, "latency_s": 9.1, "llm_s": 1.4}],
        "event_steps": [{"id": "s1", "event_id": "e1", "t": "2026-11-09T23:14:31+00:00", "text": "Caregiver alerted on Telegram"}],
        "activity_minutes": [],
    }
    src = SupabaseSource.__new__(SupabaseSource)
    src.db = type("DB", (), {"table": lambda self, name: _Rows(tables[name])})()
    d = src.load()

    e = d["events"][0]
    assert e["t"].tzinfo is not None and e["alerted_at"] > e["t"]
    assert M.what_they_said(e, d["checkins"]) == "“help me”"
    assert M.outcome(e) == ("Open · caregiver alerted", "alert")
    conn = M.connection_state(d["status"], NOW, 120, 120)
    assert conn["base_ok"] and conn["watch_state"] == "never"     # voice-only demo: watch not set up yet
    assert d["activity"].empty


# ── the whole page, run headlessly ──────────────────────────────────────────

APP = str(Path(__file__).resolve().parents[1] / "basestation" / "dashboard" / "app.py")


def test_page_renders_and_ack_button_works():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert any(b.label == "I'm on it" for b in at.button)

    at.button(key="ack_btn").click().run()
    assert not at.exception, at.exception
    assert any(b.label == "Mark resolved" for b in at.button)      # banner switched to "you're handling it"

    at.button(key="resolve_banner").click().run()
    assert not at.exception, at.exception
    assert not any(b.key == "ack_btn" for b in at.button)          # nothing open any more


def test_page_shows_cant_see_state():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.checkbox[0].check().run()                            # "Base station offline"
    assert not at.exception, at.exception
    page = " ".join(h.proto.body for h in at.get("html"))
    assert "Can't see" in page and "Base station offline" in page
