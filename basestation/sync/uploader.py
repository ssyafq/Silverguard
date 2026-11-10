"""Sends events, check-ins, timeline steps and heartbeats from the laptop to Supabase.

How it stays reliable without slowing the check-in down:
  1. Every change is first written to a local file, the "outbox" (data/raw/cloud_outbox.jsonl).
     Writing a line is instant, so the voice check-in never waits for the internet.
  2. A background thread uploads the outbox lines in order. If the internet is down they just
     wait in the file (even across restarts) and go up when it is back.
  3. Every 30 s a heartbeat tells the dashboard "the base station is alive". If heartbeats
     stop, the dashboard turns red ("Can't see ...").

If SUPABASE_URL / SUPABASE_SECRET_KEY are not in .env, everything here quietly does nothing.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTBOX = PROJECT_ROOT / "data" / "raw" / "cloud_outbox.jsonl"     # data/raw is git-ignored
load_dotenv(PROJECT_ROOT / ".env")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid.uuid4())


class Cloud:
    HEARTBEAT_S = 30

    def __init__(self, outbox: Path = OUTBOX, client=None, start_thread: bool = True):
        self.url = os.environ.get("SUPABASE_URL", "").strip()
        self.key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        self.enabled = bool(client or (self.url and self.key))
        self.outbox = outbox
        self._client = client                       # tests can pass a fake client
        self._file_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_error: str | None = None
        self.watch_last_data_at: str | None = None  # set by the sensor ingest (Obj 2, later)
        self.battery_pct: float | None = None       # set once the watch reports battery (Obj 1)
        if self.enabled and start_thread:
            threading.Thread(target=self._run, name="cloud-uploader", daemon=True).start()

    # ── public: all instant, never touch the network ──
    def insert(self, table: str, row: dict) -> None:
        # "upsert" so that re-sending the same row after a lost reply can't create a duplicate
        self._enqueue({"op": "upsert", "table": table, "row": row})

    def update(self, table: str, row_id: str, fields: dict) -> None:
        self._enqueue({"op": "update", "table": table, "id": row_id, "row": fields})

    def step(self, event_id: str, text: str) -> None:
        """Add one line to an event's timeline (shown in the dashboard's Event detail)."""
        self.insert("event_steps", {"id": new_id(), "event_id": event_id, "t": now_iso(), "text": text})

    def pending(self) -> int:
        with self._file_lock:
            if not self.outbox.exists():
                return 0
            return sum(1 for line in self.outbox.read_text(encoding="utf-8").splitlines() if line.strip())

    def close(self, timeout_s: float = 8) -> None:
        """Give queued rows a few seconds to upload before the program exits."""
        if not self.enabled:
            return
        end = time.monotonic() + timeout_s
        while self.pending() and time.monotonic() < end:
            self._wake.set()
            time.sleep(0.3)
        self._stop.set()
        self._wake.set()
        if self.pending():
            print(f"[cloud] {self.pending()} rows still waiting in {self.outbox.name}; they upload next time.")

    # ── internals ──
    def _enqueue(self, op: dict) -> None:
        if not self.enabled:
            return
        line = json.dumps(op, ensure_ascii=False, default=str)
        with self._file_lock:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        self._wake.set()

    def _db(self):
        if self._client is None:
            from supabase import ClientOptions, create_client
            self._client = create_client(self.url, self.key, options=ClientOptions(
                postgrest_client_timeout=8, postgrest_client_retry=False))
        return self._client

    def _execute(self, op: dict) -> None:
        table = self._db().table(op["table"])
        if op["op"] == "upsert":
            table.upsert(op["row"]).execute()
        elif op["op"] == "update":
            table.update(op["row"]).eq("id", op["id"]).execute()

    def flush(self) -> int:
        """Upload queued lines in order; stop at the first failure (it stays queued).
        Returns how many lines were removed from the outbox."""
        with self._file_lock:
            if not self.outbox.exists():
                return 0
            lines = self.outbox.read_text(encoding="utf-8").splitlines()
        done = 0
        for line in lines:
            if line.strip():
                try:
                    op = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[cloud] skipping a damaged outbox line: {line[:80]!r}")
                    done += 1
                    continue
                try:
                    self._execute(op)
                except Exception as e:
                    self._report(e)
                    break
            done += 1
        if done:
            with self._file_lock:            # new lines may have been added meanwhile — keep them
                rest = self.outbox.read_text(encoding="utf-8").splitlines()[done:]
                self.outbox.write_text("".join(line + "\n" for line in rest), encoding="utf-8")
            if self._last_error and done == len(lines):
                print("[cloud] back online, all queued rows uploaded")
                self._last_error = None
        return done

    def heartbeat(self) -> bool:
        row = {"id": 1, "base_station_seen_at": now_iso()}
        if self.watch_last_data_at:
            row["watch_last_data_at"] = self.watch_last_data_at
        if self.battery_pct is not None:
            row["battery_pct"] = self.battery_pct
        try:
            self._db().table("status").upsert(row).execute()
            return True
        except Exception as e:
            self._report(e)
            return False

    def _report(self, e: Exception) -> None:
        msg = f"{type(e).__name__}: {e}"[:200]
        if msg != self._last_error:          # say it once, not every retry
            print(f"[cloud] upload failed, will retry: {msg}")
            self._last_error = msg

    def _run(self) -> None:
        next_beat = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_beat:
                self.heartbeat()
                next_beat = time.monotonic() + self.HEARTBEAT_S
            self.flush()
            self._wake.wait(timeout=5)
            self._wake.clear()
