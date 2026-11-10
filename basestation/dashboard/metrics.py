"""Turns database rows into the numbers and words on the dashboard.

Plain Python (no Streamlit), so it can be unit-tested and reused by Obj 4 evaluation.

Data shape (same for sample data and Supabase), all times timezone-aware UTC:
    status:   {"base_station_seen_at", "watch_last_data_at", "battery_pct"}
    events:   [{"id", "t", "type", "confidence", "detector", "status", "duration_min",
                "snippet", "alerted_at", "acknowledged_at", "resolved_at"}]
    checkins: [{"event_id", "t", "attempt", "transcript", "reply", "used_fallback", "latency_s", "llm_s"}]
    steps:    [{"event_id", "t", "text"}]
    activity: pandas DataFrame with columns minute (UTC datetime), label, enmo_mg
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

import pandas as pd

LABELS = ["sleeping", "resting", "light", "active"]
LABEL_NAMES = {"sleeping": "Sleeping", "resting": "Resting", "light": "Light activity", "active": "Active"}
STATUSES = ["open", "acknowledged", "resolved", "false_alarm"]


# ── formatting ──────────────────────────────────────────────────────────────

def local(t: datetime, offset_h: float) -> datetime:
    return t.astimezone(timezone(timedelta(hours=offset_h)))


def fmt_hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60} h {m % 60:02d} m"


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} m {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} m"


def fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h {(s % 3600) // 60} min ago"


def fmt_event_time(t: datetime, now: datetime, offset_h: float) -> str:
    lt, ln = local(t, offset_h), local(now, offset_h)
    days = (ln.date() - lt.date()).days
    day = "Today" if days == 0 else "Yest" if days == 1 else lt.strftime("%a")
    return f"{day} {lt:%H:%M}"


# ── connection / "can we see the person?" ───────────────────────────────────

def connection_state(status: dict, now: datetime, base_stale_s: float, watch_stale_s: float) -> dict:
    """Is the base station alive, and is the watch sending data?

    watch_state: "ok" | "stale" | "never" ("never" = no sensor data has ever arrived,
    e.g. while only the voice demo is running). A dead link must never look fine.
    """
    seen = status.get("base_station_seen_at")
    base_age = (now - seen).total_seconds() if seen else None
    watch = status.get("watch_last_data_at")
    watch_age = (now - watch).total_seconds() if watch else None
    return {
        "base_ok": base_age is not None and base_age <= base_stale_s,
        "base_age_s": base_age,
        "base_seen_at": seen,
        "watch_state": "never" if watch is None else ("ok" if watch_age <= watch_stale_s else "stale"),
        "watch_age_s": watch_age,
        "battery_pct": status.get("battery_pct"),
    }


# ── events ──────────────────────────────────────────────────────────────────

def event_title(e: dict) -> str:
    if e["type"] == "fall":
        return "Possible fall"
    if e["type"] == "inactivity":
        d = e.get("duration_min")
        return f"Long inactivity ({int(d)} min)" if d else "Long inactivity"
    return str(e["type"]).replace("_", " ").capitalize()


def checkins_for(event_id, checkins: list[dict]) -> list[dict]:
    return sorted((c for c in checkins if c["event_id"] == event_id), key=lambda c: c["t"])


def steps_for(event_id, steps: list[dict]) -> list[dict]:
    return sorted((s for s in steps if s["event_id"] == event_id), key=lambda s: s["t"])


def what_they_said(e: dict, checkins: list[dict]) -> str:
    """The transcript of the last check-in reply (or a note if there was none)."""
    cs = checkins_for(e["id"], checkins)
    if not cs:
        return "—"
    heard = [c for c in cs if (c.get("transcript") or "").strip()]
    if heard:
        return f"“{heard[-1]['transcript'].strip()}”"
    n = len(cs)
    return "(no reply, asked twice)" if n == 2 else f"(no reply, asked {n}×)"


def last_reply(e: dict, checkins: list[dict]) -> str | None:
    cs = checkins_for(e["id"], checkins)
    return cs[-1]["reply"] if cs else None


def outcome(e: dict) -> tuple[str, str]:
    """(text, kind) for the outcome pill. kind: alert | handling | ok | muted."""
    st = e["status"]
    ack = e.get("acknowledged_at")
    alerted = e.get("alerted_at")
    ack_after = f" in {_minutes((ack - alerted).total_seconds())}" if ack and alerted else ""
    if st == "open":
        return ("Open · caregiver alerted", "alert") if alerted else ("Open · checking in", "alert")
    if st == "acknowledged":
        return f"Acknowledged{ack_after}", "handling"
    if st == "resolved":
        return (f"Resolved · acked{ack_after}", "ok") if ack else ("Resolved", "ok")
    if st == "false_alarm":
        return "False alarm · they said OK", "muted"
    return st, "muted"


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min"


def active_alert(events: list[dict]) -> dict | None:
    """The newest event that still needs the caregiver (open or being handled)."""
    live = [e for e in events if e["status"] in ("open", "acknowledged")]
    return max(live, key=lambda e: e["t"]) if live else None


def week_counts(events: list[dict], now: datetime) -> Counter:
    since = now - timedelta(days=7)
    return Counter(e["status"] for e in events if e["t"] >= since)


# ── activity ────────────────────────────────────────────────────────────────

def window(activity: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    if activity.empty:
        return activity
    m = activity["minute"]
    return activity[(m >= start) & (m < end)].sort_values("minute")


def activity_runs(activity: pd.DataFrame, start: datetime, end: datetime) -> list[tuple]:
    """Join consecutive minutes with the same label into (start, end, label) runs.
    A missing minute breaks a run, so data gaps show up as gaps in the bar."""
    df = window(activity, start, end)
    runs: list[list] = []
    for minute, label in zip(df["minute"], df["label"]):
        minute = minute.to_pydatetime()
        if runs and runs[-1][2] == label and runs[-1][1] == minute:
            runs[-1][1] = minute + timedelta(minutes=1)
        else:
            runs.append([minute, minute + timedelta(minutes=1), label])
    return [tuple(r) for r in runs]


def right_now(activity: pd.DataFrame, now: datetime) -> tuple[str, datetime] | None:
    """(label, since) for the most recent activity state, or None if no recent data."""
    df = window(activity, now - timedelta(hours=24), now + timedelta(minutes=1))
    if df.empty or (now - df["minute"].iloc[-1].to_pydatetime()) > timedelta(minutes=5):
        return None
    runs = activity_runs(df, now - timedelta(hours=24), now + timedelta(minutes=1))
    return runs[-1][2], runs[-1][0]


def active_24h(activity: pd.DataFrame, now: datetime) -> dict:
    df = window(activity, now - timedelta(hours=24), now)
    c = Counter(df["label"]) if not df.empty else Counter()
    return {"total": c["light"] + c["active"], "active": c["active"], "light": c["light"], "has_data": not df.empty}


def sleep_last_night(activity: pd.DataFrame, now: datetime, offset_h: float) -> dict | None:
    """Sleep in the night window 18:00 (day before) → 12:00, for the most recent night."""
    ln = local(now, offset_h)
    noon = ln.replace(hour=12, minute=0, second=0, microsecond=0)
    end_local = noon if ln >= noon else ln                     # night ends at noon today (or now, if earlier)
    start_local = (noon - timedelta(days=1)).replace(hour=18)  # ...and starts at 18:00 yesterday
    df = window(activity, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))
    asleep = df[df["label"] == "sleeping"]
    if asleep.empty:
        return None
    first, last = asleep["minute"].iloc[0], asleep["minute"].iloc[-1]
    inside = df[(df["minute"] >= first) & (df["minute"] <= last)]
    is_sleep = (inside["label"] == "sleeping").astype(int).tolist()
    wakeups = sum(1 for a, b in zip(is_sleep, is_sleep[1:]) if a == 1 and b == 0)
    return {
        "total_min": len(asleep),
        "start": first.to_pydatetime(),
        "end": last.to_pydatetime() + timedelta(minutes=1),
        "wakeups": wakeups,
    }


# ── system health (Obj 4 numbers) ───────────────────────────────────────────

def health(events: list[dict], checkins: list[dict], activity: pd.DataFrame, now: datetime) -> dict:
    since = now - timedelta(days=7)
    week = [e for e in events if e["t"] >= since]
    falls = [e for e in week if e["type"] == "fall"]
    false_alarms = sum(1 for e in falls if e["status"] == "false_alarm")

    to_alert = [(e["alerted_at"] - e["t"]).total_seconds() for e in week if e.get("alerted_at")]
    response = [(e["acknowledged_at"] - e["alerted_at"]).total_seconds()
                for e in week if e.get("acknowledged_at") and e.get("alerted_at")]
    llm = [c["llm_s"] for c in checkins if c.get("llm_s") and c["t"] >= since]

    minutes_seen = len(window(activity, since, now))
    return {
        "false_alarms_per_day": false_alarms / 7,
        "fall_to_alert_s": median(to_alert) if to_alert else None,
        "llm_s": median(llm) if llm else None,
        "uptime_pct": 100 * minutes_seen / (7 * 24 * 60) if minutes_seen else None,
        "caregiver_response_s": median(response) if response else None,
    }
