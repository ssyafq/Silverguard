"""live_status.py: watch the stream arrive in Firestore, in the terminal.

WHAT THIS FILE DOES
    Every few seconds it reads the database and prints whether the watch is online, its
    current state (still / moving) and since when, and the last 10 changes in the past hour.
    Use it to check end-to-end that the watch (or stream_demo, or send_test_event.py) really
    reaches Firestore, without opening the Google console or the dashboard.

Needs: pip install google-cloud-firestore, and a Google login for the project:
    gcloud auth application-default login       (your own Google account), or
    $env:GOOGLE_APPLICATION_CREDENTIALS="<service-account.json>"

    python streaming/tools/live_status.py --project <gcp-project-id> [--device watch-01] [--every 5]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import firestore_source as fs  # noqa: E402


def fmt(t) -> str:
    return t.astimezone().strftime("%H:%M:%S") if t else "-"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--device", default="watch-01")
    p.add_argument("--every", type=float, default=5.0, help="seconds between refreshes")
    args = p.parse_args()

    db = fs.client(args.project)
    while True:
        d = fs.live(db, args.device)
        now = datetime.now(timezone.utc)
        print(f"\n[{fmt(now)}] {args.device}: {'ONLINE' if d['online'] else 'OFFLINE'}"
              f" | state {d['state'] or '?'} since {fmt(d.get('since'))}"
              f" | last seen {fmt(d.get('last_seen'))} | last fall {fmt(d.get('last_fall_at'))}")
        if d["state_mismatch"]:
            print(f"    (heartbeat says {d['heartbeat_state']}: a change message was lost)")
        for when, state in fs.transitions(db, args.device, now - timedelta(hours=1))[-10:][::-1]:
            print(f"    {fmt(when)}  -> {state}")
        time.sleep(args.every)


if __name__ == "__main__":
    main()
