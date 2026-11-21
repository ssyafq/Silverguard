"""send_test_event.py: send one message to the ingest service from your PC, like the watch would.

WHAT THIS FILE DOES
    Lets you test the server (local or on Cloud Run) without any watch. Each run sends one
    activity / fall / heartbeat message and prints the server's reply:
        200 {'ok': True, 'duplicate': False}   stored
        401 {'detail': 'bad device key'}       wrong --key or --device
        422 {...}                              the message didn't make sense
        0   {'error': ...}                     server not reachable

    python streaming/tools/send_test_event.py --url http://localhost:8080 --key dev activity moving
    python streaming/tools/send_test_event.py --url https://<service>.run.app --key <key> fall
    python streaming/tools/send_test_event.py ... heartbeat --state still

The URL and key can also come from the environment: STREAM_URL, STREAM_DEVICE_KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_client import IngestClient  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("STREAM_URL", "http://localhost:8080"),
                   help="service base URL (with or without /ingest)")
    p.add_argument("--key", default=os.environ.get("STREAM_DEVICE_KEY", ""))
    p.add_argument("--device", default="watch-01")
    sub = p.add_subparsers(dest="type", required=True)
    a = sub.add_parser("activity"); a.add_argument("state", choices=["still", "moving"])
    f = sub.add_parser("fall"); f.add_argument("--score", type=float, default=0.93)
    h = sub.add_parser("heartbeat"); h.add_argument("--state", choices=["still", "moving"])
    args = p.parse_args()

    c = IngestClient(args.url, args.key, args.device)
    if args.type == "activity":
        res = c.send("activity", state=args.state, detector="pc_test")
    elif args.type == "fall":
        res = c.send("fall", confidence=args.score, peak_g=3.4, turn_deg=47.0, detector="pc_test")
    else:
        res = c.send("heartbeat", state=args.state)
    print(*res)


if __name__ == "__main__":
    main()
