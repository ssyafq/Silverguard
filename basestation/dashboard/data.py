"""Where the dashboard's data comes from: built-in sample data, or the Supabase database.

Both give the same shape (see metrics.py), so the page code doesn't care which one is used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from basestation.dashboard import sample_data


def _ts(value):
    """Database text → timezone-aware datetime (or None)."""
    if not value:
        return None
    t = pd.Timestamp(value)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.to_pydatetime()


class SampleSource:
    """Made-up data kept in memory. Buttons change it for this browser session only."""

    is_sample = True

    def __init__(self, offset_h: float):
        self.data = sample_data.build(offset_h=offset_h)

    def load(self) -> dict:
        return self.data

    def _event(self, event_id):
        return next(e for e in self.data["events"] if e["id"] == event_id)

    def acknowledge(self, event_id) -> None:
        e = self._event(event_id)
        e["status"], e["acknowledged_at"] = "acknowledged", datetime.now(timezone.utc)

    def resolve(self, event_id) -> None:
        e = self._event(event_id)
        e["status"], e["resolved_at"] = "resolved", datetime.now(timezone.utc)

    # demo switches, to show the "can't see" states without unplugging anything
    def simulate(self, base_offline: bool, watch_offline: bool) -> None:
        now = datetime.now(timezone.utc)
        st = self.data["status"]
        st["base_station_seen_at"] = now - timedelta(minutes=17) if base_offline else now - timedelta(seconds=8)
        st["watch_last_data_at"] = now - timedelta(minutes=9) if watch_offline else now - timedelta(seconds=32)


class SupabaseSource:
    """Reads (and, for the two buttons, updates) the online database."""

    is_sample = False

    def __init__(self, url: str, key: str):
        from supabase import ClientOptions, create_client
        self.db = create_client(url, key, options=ClientOptions(
            postgrest_client_timeout=8, postgrest_client_retry=False))

    def load(self) -> dict:
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()

        rows = self.db.table("status").select("*").eq("id", 1).execute().data
        st = rows[0] if rows else {}
        status = {k: _ts(st.get(k)) for k in ("base_station_seen_at", "watch_last_data_at")}
        status["battery_pct"] = st.get("battery_pct")

        events = self.db.table("events").select("*").gte("t", week_ago).order("t", desc=True).limit(200).execute().data
        for e in events:
            for k in ("t", "alerted_at", "acknowledged_at", "resolved_at"):
                e[k] = _ts(e.get(k))
        ids = [e["id"] for e in events]

        checkins, steps = [], []
        if ids:
            checkins = self.db.table("checkins").select("*").in_("event_id", ids).execute().data
            steps = self.db.table("event_steps").select("*").in_("event_id", ids).execute().data
            for row in checkins + steps:
                row["t"] = _ts(row["t"])

        return {"status": status, "events": events, "checkins": checkins, "steps": steps,
                "activity": self._activity(week_ago)}

    def _activity(self, since_iso: str) -> pd.DataFrame:
        # The database returns at most 1000 rows per request, so read in pages.
        rows, page = [], 0
        while True:
            chunk = (self.db.table("activity_minutes").select("minute,label,enmo_mg")
                     .gte("minute", since_iso).order("minute").range(page * 1000, page * 1000 + 999)
                     .execute().data)
            rows += chunk
            if len(chunk) < 1000 or page >= 11:          # 7 days = 10,080 rows max
                break
            page += 1
        df = pd.DataFrame(rows, columns=["minute", "label", "enmo_mg"])
        if not df.empty:
            df["minute"] = pd.to_datetime(df["minute"], utc=True)
        return df

    def acknowledge(self, event_id) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.table("events").update({"status": "acknowledged", "acknowledged_at": now}).eq("id", event_id).execute()
        self._step(event_id, "Caregiver pressed “I'm on it”")

    def resolve(self, event_id) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.table("events").update({"status": "resolved", "resolved_at": now}).eq("id", event_id).execute()
        self._step(event_id, "Caregiver marked it resolved")

    def _step(self, event_id, text: str) -> None:
        import uuid
        self.db.table("event_steps").insert({"id": str(uuid.uuid4()), "event_id": event_id,
                                             "t": datetime.now(timezone.utc).isoformat(), "text": text}).execute()
