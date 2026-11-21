"""test_firestore_source.py: tests for the reading side (dashboard/firestore_source.py).

WHAT THIS FILE DOES
    Checks the pure helpers that turn Firestore data into what the dashboard shows: whether
    the watch counts as online, and how much time was spent still / moving. No database is
    used: the inputs are written out by hand.

    python -m pytest streaming/tests -q
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import firestore_source as fs  # noqa: E402

T0 = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_is_online():
    assert fs.is_online(at(0), now=at(2))
    assert not fs.is_online(at(0), now=at(3))
    assert not fs.is_online(None)


def test_time_in_state_splits_by_changes():
    changes = [(at(10), "moving"), (at(40), "still")]
    out = fs.time_in_state(changes, at(0), at(60), state_before="still")
    assert out == {"still": 30 * 60, "moving": 30 * 60}


def test_time_in_state_unknown_before_first_change():
    out = fs.time_in_state([(at(15), "moving")], at(0), at(60))
    assert out == {"unknown": 15 * 60, "moving": 45 * 60}


def test_time_in_state_ignores_changes_outside_range():
    changes = [(at(-5), "moving"), (at(30), "still"), (at(90), "moving")]
    out = fs.time_in_state(changes, at(0), at(60))
    assert out == {"moving": 30 * 60, "still": 30 * 60}


def test_time_in_state_no_changes():
    assert fs.time_in_state([], at(0), at(60), state_before="still") == {"still": 3600}
