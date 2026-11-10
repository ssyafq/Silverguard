"""Made-up data that mirrors the dashboard design (PNG), so the page works before any
sensor or database is connected. Shown with a "Sample data" badge — never as real data.

Times are built around 'now' so the newest fall is always a few minutes old.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

# A typical day, by local clock time (HH:MM start, label). Each day gets small random shifts.
DAY_PLAN = [
    ("00:00", "sleeping"), ("06:30", "light"), ("07:30", "active"), ("08:10", "resting"),
    ("11:30", "light"), ("12:00", "resting"), ("14:30", "light"), ("16:30", "active"),
    ("17:10", "resting"), ("21:20", "light"), ("22:30", "sleeping"),
]
WAKEUPS = [("01:40", 5), ("03:15", 4), ("05:05", 6)]          # short night-time wake-ups (min)
ENMO_MG = {"sleeping": 5, "resting": 20, "light": 70, "active": 210}


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _label_at(minute_of_day: int, rng_shift: int) -> str:
    mod = (minute_of_day - rng_shift) % 1440
    label = DAY_PLAN[0][1]
    for start, lab in DAY_PLAN:
        if mod >= _hm(start):
            label = lab
    for start, length in WAKEUPS:
        if _hm(start) <= mod < _hm(start) + length:
            label = "resting"
    return label


def _snippet(rng: random.Random, peak: float) -> dict:
    """10 s of acceleration magnitude (SMV) around an impact at t=0: wobble, free-fall dip,
    impact spike, small rebound, then lying still at ~1 g."""
    hz, values = 50, []
    for i in range(-5 * hz, 5 * hz):
        t = i / hz
        g = 1.0 + rng.gauss(0, 0.025)
        if -1.5 < t < -0.6:
            g += 0.12 * math.sin(t * 9)                       # stumbling
        if -0.6 <= t < -0.05:
            g -= 0.65 * math.sin(math.pi * (t + 0.6) / 0.55)  # free-fall dip
        if -0.05 <= t < 0.25:
            g += (peak - 1) * math.exp(-((t - 0.04) ** 2) / 0.003)   # impact
        if 0.25 <= t < 0.6:
            g -= 0.35 * math.sin(math.pi * (t - 0.25) / 0.35)  # rebound
        values.append(round(max(g, 0.05), 3))
    return {"t0_s": -5, "hz": hz, "smv": values}


def build(now: datetime | None = None, offset_h: float = 8, seed: int = 7) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    tz = timezone(timedelta(hours=offset_h))
    rng = random.Random(seed)
    ln = now.astimezone(tz)

    def at_local(days_ago: int, hhmm: str) -> datetime:
        d = (ln - timedelta(days=days_ago)).date()
        h, m = map(int, hhmm.split(":"))
        return datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(timezone.utc)

    # ── events ──
    events, checkins, steps = [], [], []

    def add_event(t, type_, status, confidence=None, detector=None, duration_min=None,
                  alerted_after_s=None, ack_after_s=None, resolved_after_s=None, peak=None):
        e = {
            "id": str(uuid.UUID(int=rng.getrandbits(128))), "t": t, "type": type_, "status": status,
            "confidence": confidence, "detector": detector, "duration_min": duration_min,
            "snippet": _snippet(rng, peak) if peak else None,
            "alerted_at": t + timedelta(seconds=alerted_after_s) if alerted_after_s else None,
            "acknowledged_at": None, "resolved_at": None,
        }
        if ack_after_s:
            e["acknowledged_at"] = e["alerted_at"] + timedelta(seconds=ack_after_s)
        if resolved_after_s:
            e["resolved_at"] = t + timedelta(seconds=resolved_after_s)
        events.append(e)
        return e

    def add_checkin(e, after_s, attempt, transcript, reply, llm_s):
        checkins.append({"event_id": e["id"], "t": e["t"] + timedelta(seconds=after_s), "attempt": attempt,
                         "transcript": transcript, "reply": reply, "used_fallback": False,
                         "latency_s": 9.5 + rng.random(), "llm_s": llm_s})

    def add_step(e, after_s, text):
        steps.append({"event_id": e["id"], "t": e["t"] + timedelta(seconds=after_s), "text": text})

    # 1. Newest: possible fall a few minutes ago, no reply → alert, not yet acknowledged
    fall_now = now - timedelta(minutes=13, seconds=20)
    e1 = add_event(fall_now, "fall", "open", 0.93, "watch forest v0.4", alerted_after_s=20, peak=3.4)
    add_step(e1, 0, "Fall detected on the watch (score 0.93)")
    add_step(e1, 1, "Base station asked “Are you okay?”")
    add_checkin(e1, 9, 1, "", "none", None)
    add_step(e1, 9, "No reply, asked again")
    add_checkin(e1, 19, 2, "", "none", None)
    add_step(e1, 19, "Still no reply")
    add_step(e1, 20, "Caregiver alerted on Telegram")

    # 2. Yesterday evening: fall alarm, but they said they're fine
    e2 = add_event(at_local(1, "21:40"), "fall", "false_alarm", 0.71, "watch forest v0.4", peak=2.8)
    add_step(e2, 0, "Fall detected on the watch (score 0.71)")
    add_step(e2, 1, "Base station asked “Are you okay?”")
    add_checkin(e2, 8, 1, "I'm okay, I just sat down too hard", "ok", 1.3)
    add_step(e2, 8, "Heard “I'm okay, I just sat down too hard” → ok")
    add_step(e2, 9, "No alert: they said they're OK")

    # 3. Yesterday afternoon: long inactivity, they were napping
    e3 = add_event(at_local(1, "14:15"), "inactivity", "resolved", None, "inactivity rule", duration_min=52,
                   resolved_after_s=10)
    add_step(e3, 0, "No movement for 52 min during the day")
    add_step(e3, 1, "Base station asked “Are you okay?”")
    add_checkin(e3, 8, 1, "I was having a nap", "ok", 1.1)
    add_step(e3, 8, "Heard “I was having a nap” → ok")

    # 4. A few days ago: real fall, asked for help, caregiver acknowledged in 2 min 10 s
    e4 = add_event(at_local(4, "10:08"), "fall", "resolved", 0.88, "watch forest v0.4",
                   alerted_after_s=14, ack_after_s=130, resolved_after_s=25 * 60, peak=3.9)
    add_step(e4, 0, "Fall detected on the watch (score 0.88)")
    add_step(e4, 1, "Base station asked “Are you okay?”")
    add_checkin(e4, 9, 1, "My knee hurts, I can't get up", "help", 1.2)
    add_step(e4, 9, "Heard “My knee hurts, I can't get up” → help")
    add_step(e4, 14, "Caregiver alerted on Telegram")
    add_step(e4, 144, "Caregiver pressed “I'm on it”")

    # ── activity: one row per minute for the last 7 days ──
    start = (now - timedelta(days=7)).replace(second=0)
    minutes = pd.date_range(start, now.replace(second=0), freq="1min", inclusive="left", tz="UTC")
    day_shift = {d: rng.randint(-20, 20) for d in range(9)}
    rows = []
    for m in minutes:
        lm = m.tz_convert(tz)
        label = _label_at(lm.hour * 60 + lm.minute, day_shift[(ln.date() - lm.date()).days])
        rows.append((m.to_pydatetime(), label, max(0.0, ENMO_MG[label] * (0.7 + 0.6 * rng.random()))))
    activity = pd.DataFrame(rows, columns=["minute", "label", "enmo_mg"])

    # The 52-min inactivity was resting; after today's fall the person is lying still.
    span = (activity["minute"] >= e3["t"] - timedelta(minutes=52)) & (activity["minute"] < e3["t"])
    activity.loc[span, "label"] = "resting"
    activity.loc[activity["minute"] >= fall_now.replace(second=0), "label"] = "resting"

    # Two short data gaps (watch off the wrist) a few days ago → uptime just under 100 %.
    for days_ago, hhmm, length in [(3, "09:20", 45), (5, "19:05", 35)]:
        g0 = at_local(days_ago, hhmm)
        activity = activity[~((activity["minute"] >= g0) & (activity["minute"] < g0 + timedelta(minutes=length)))]

    status = {
        "base_station_seen_at": now - timedelta(seconds=8),
        "watch_last_data_at": now - timedelta(seconds=32),
        "battery_pct": None,                  # the watch doesn't report battery yet (needs Obj 1)
    }
    return {"status": status, "events": events, "checkins": checkins, "steps": steps,
            "activity": activity.reset_index(drop=True)}
