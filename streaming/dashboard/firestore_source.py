"""firestore_source.py: how anything on the PC side READS the streamed data from Firestore.

WHAT THIS FILE DOES
    The ingest service writes; this file reads. It turns Firestore documents into plain
    Python values that a dashboard or a terminal tool can show:
      live(db, device)            -> current state, since when, online/offline, last fall
      transitions(db, device, t)  -> list of (time, state) changes since time t
      falls(db, device, t)        -> fall events since time t
      time_in_state(...)          -> seconds spent still / moving in a time range
    Used by dashboard/live_page.py (Streamlit) and tools/live_status.py (terminal). The
    existing caregiver dashboard (basestation/dashboard/) should use these when it is ported
    from Supabase to Firestore.

Cost: live() = 1 document read. transitions() = 1 read per change returned.

Connecting: firestore.Client(project=...) uses your Google login
(`gcloud auth application-default login`) or GOOGLE_APPLICATION_CREDENTIALS=<service-account.json>.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

STALE_S = 150   # no message for 2.5 min (two missed heartbeats) = watch offline


def client(project: str | None = None, credentials_info: dict | None = None):
    """A Firestore client. credentials_info = service-account JSON as a dict (e.g. Streamlit secrets)."""
    from google.cloud import firestore
    if credentials_info:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(credentials_info)
        return firestore.Client(project=project or credentials_info.get("project_id"), credentials=creds)
    return firestore.Client(project=project)


def is_online(last_seen: datetime | None, now: datetime | None = None, stale_s: int = STALE_S) -> bool:
    if last_seen is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - last_seen).total_seconds() < stale_s


def live(db, device: str = "watch-01", stale_s: int = STALE_S) -> dict:
    """devices/{device} plus an 'online' flag. Empty values if the watch never sent anything."""
    d = db.collection("devices").document(device).get().to_dict() or {}
    d["online"] = is_online(d.get("last_seen"), stale_s=stale_s)
    d.setdefault("state", None)
    # heartbeat says one state, the last change says another = a change message was lost
    d["state_mismatch"] = bool(d.get("heartbeat_state") and d["state"]
                               and d["heartbeat_state"] != d["state"])
    return d


def transitions(db, device: str = "watch-01", since: datetime | None = None) -> list[tuple[datetime, str]]:
    """Activity changes since `since` (default: 24 h ago), oldest first."""
    from google.cloud import firestore
    since = since or datetime.now(timezone.utc) - timedelta(hours=24)
    docs = (db.collection("devices").document(device).collection("transitions")
            .where(filter=firestore.FieldFilter("received_at", ">=", since))
            .order_by("received_at").stream())
    return [(d.get("received_at"), d.get("state")) for d in docs]


def falls(db, device: str = "watch-01", since: datetime | None = None) -> list[dict]:
    """Fall events since `since` (default: 7 days ago), newest first."""
    from google.cloud import firestore
    since = since or datetime.now(timezone.utc) - timedelta(days=7)
    # Filter + order on the same field only: works without creating a composite index.
    # The device is checked in Python (falls are rare, so this reads very few documents).
    docs = (db.collection("events")
            .where(filter=firestore.FieldFilter("t", ">=", since))
            .order_by("t", direction=firestore.Query.DESCENDING).stream())
    return [{"id": d.id, **d.to_dict()} for d in docs if d.get("device_id") == device]


def time_in_state(changes: list[tuple[datetime, str]], start: datetime, end: datetime,
                  state_before: str | None = None) -> dict[str, float]:
    """Seconds spent in each state between start and end.

    changes      (time, state) oldest first, as from transitions()
    state_before the state at `start` (e.g. the last change before the range); time before the
                 first change counts as "unknown" if this is None.
    Pure function (no database), so it is unit-tested in tests/test_firestore_source.py.
    """
    out: dict[str, float] = {}
    cur, t = state_before or "unknown", start
    for when, state in changes:
        if when <= start:
            cur = state
            continue
        if when >= end:
            break
        out[cur] = out.get(cur, 0.0) + (when - t).total_seconds()
        cur, t = state, when
    out[cur] = out.get(cur, 0.0) + max(0.0, (end - t).total_seconds())
    return out
