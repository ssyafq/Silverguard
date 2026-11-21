"""main.py: the SilverGuard ingest service that runs on Google Cloud Run.

WHAT THIS FILE DOES
    It is the "front door" between the watch and the database. The watch POSTs small JSON
    events here over HTTPS (activity changed / fall / heartbeat). This file checks the device
    key, checks the data makes sense, stamps the server time and writes to Firestore.
    The watch never holds a Firestore credential. See streaming/README.md for the full picture.

Endpoints
    POST /ingest   one event from a watch (header X-Device-Key)
    GET  /health   liveness check, no auth
    GET  /docs     auto-generated test page (FastAPI), handy for learning / testing

What it writes in Firestore
    devices/{device_id}                         live state: state, since, last_seen, ...
    devices/{device_id}/transitions/{boot-seq}  one document per activity change
    events/{boot-seq}                           one document per confirmed fall (status "open")

Environment
    DEVICE_KEYS    "watch-01=<key>[,watch-02=<key>...]" (from Secret Manager on Cloud Run)
    STORE          "firestore" (default) or "memory" (local testing, nothing is saved)
    GOOGLE_CLOUD_PROJECT  set automatically on Cloud Run; needed locally for Firestore

Run locally (no Google account needed), from streaming/server:
    PowerShell:  $env:STORE="memory"; $env:DEVICE_KEYS="watch-01=dev"; uvicorn main:app --port 8080
    bash:        STORE=memory DEVICE_KEYS=watch-01=dev uvicorn main:app --port 8080
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

log = logging.getLogger("silverguard.ingest")
logging.basicConfig(level=logging.INFO)

ACTIVITY_STATES = ("still", "moving")   # HAR classes on the watch (ACTIVITY_NAME in the firmware)


# ---------------------------------------------------------------- request model
class Event(BaseModel):
    """The JSON body the watch sends. FastAPI rejects anything that doesn't fit (HTTP 422)."""
    device_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["activity", "fall", "heartbeat"]
    boot_id: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9]+$")  # random per power-on
    seq: int = Field(ge=0)                       # counts up from 0 at every boot
    uptime_ms: int = Field(ge=0)
    state: Optional[str] = None                  # activity: still | moving; heartbeat: current state
    confidence: Optional[float] = None           # fall: forest score
    peak_g: Optional[float] = None               # fall: impact peak
    turn_deg: Optional[float] = None             # fall: wrist turn after impact (-1 = not measured)
    detector: Optional[str] = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _check_state(self):
        if self.type == "activity" and self.state not in ACTIVITY_STATES:
            raise ValueError(f"activity events need state in {ACTIVITY_STATES}")
        if self.state is not None and self.state not in ACTIVITY_STATES:
            raise ValueError(f"unknown state {self.state!r}")
        return self

    @property
    def doc_id(self) -> str:
        """Same event retried = same id, so a retry after a lost reply never duplicates."""
        return f"{self.boot_id}-{self.seq}"


# ---------------------------------------------------------------- storage
class MemoryStore:
    """In-memory stand-in for Firestore: local runs and tests. Forgets everything on restart."""

    def __init__(self):
        self.devices: dict[str, dict] = {}
        self.transitions: dict[str, dict[str, dict]] = {}
        self.events: dict[str, dict] = {}
        self._lock = threading.Lock()

    def touch(self, device_id: str, fields: dict) -> None:
        with self._lock:
            self.devices.setdefault(device_id, {}).update(fields)

    def add_transition(self, device_id: str, doc_id: str, data: dict) -> bool:
        with self._lock:
            docs = self.transitions.setdefault(device_id, {})
            if doc_id in docs:
                return False
            docs[doc_id] = data
            return True

    def add_event(self, doc_id: str, data: dict) -> bool:
        with self._lock:
            if doc_id in self.events:
                return False
            self.events[doc_id] = data
            return True


class FirestoreStore:
    """The real database: devices/{id}, devices/{id}/transitions/{boot-seq}, events/{boot-seq}."""

    def __init__(self):
        from google.cloud import firestore   # imported here so tests don't need the package
        from google.api_core.exceptions import AlreadyExists

        self._db = firestore.Client()
        self._exists = AlreadyExists

    def touch(self, device_id: str, fields: dict) -> None:
        # merge=True: update only these fields, keep the rest of the document
        self._db.collection("devices").document(device_id).set(fields, merge=True)

    def _create(self, ref, data: dict) -> bool:
        try:
            ref.create(data)                  # fails if the id exists = a retried event
            return True
        except self._exists:
            return False

    def add_transition(self, device_id: str, doc_id: str, data: dict) -> bool:
        ref = (self._db.collection("devices").document(device_id)
               .collection("transitions").document(doc_id))
        return self._create(ref, data)

    def add_event(self, doc_id: str, data: dict) -> bool:
        return self._create(self._db.collection("events").document(doc_id), data)


def make_store():
    kind = os.environ.get("STORE", "firestore").lower()
    return MemoryStore() if kind == "memory" else FirestoreStore()


def parse_device_keys(raw: str) -> dict[str, str]:
    """'watch-01=abc,watch-02=def' -> {'watch-01': 'abc', 'watch-02': 'def'}"""
    keys = {}
    for part in raw.split(","):
        if "=" in part:
            dev, key = part.split("=", 1)
            if dev.strip() and key.strip():
                keys[dev.strip()] = key.strip()
    return keys


# ---------------------------------------------------------------- app
def create_app(store=None, device_keys: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI(title="SilverGuard ingest")
    app.state.store = store if store is not None else make_store()
    app.state.keys = (device_keys if device_keys is not None
                      else parse_device_keys(os.environ.get("DEVICE_KEYS", "")))
    if not app.state.keys:
        log.warning("DEVICE_KEYS is empty: every /ingest request will be refused")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/ingest")
    def ingest(ev: Event, x_device_key: str = Header(default="")):
        expected = app.state.keys.get(ev.device_id)
        if not expected or not hmac.compare_digest(expected, x_device_key):
            raise HTTPException(status_code=401, detail="bad device key")

        st = app.state.store
        now = datetime.now(timezone.utc)
        base = {"boot_id": ev.boot_id, "seq": ev.seq, "uptime_ms": ev.uptime_ms,
                "received_at": now}
        device = {"last_seen": now, "boot_id": ev.boot_id, "last_seq": ev.seq}

        new = True
        if ev.type == "activity":
            new = st.add_transition(ev.device_id, ev.doc_id,
                                    {**base, "state": ev.state, "detector": ev.detector})
            if new:   # a duplicate must not move "since" forward
                device.update(state=ev.state, since=now)
        elif ev.type == "fall":
            new = st.add_event(ev.doc_id, {
                **base, "device_id": ev.device_id, "t": now, "type": "fall",
                "confidence": ev.confidence, "detector": ev.detector,
                "peak_g": ev.peak_g, "turn_deg": ev.turn_deg, "status": "open",
            })
            if new:
                device.update(last_fall_at=now)
                log.warning("FALL from %s (score %s)", ev.device_id, ev.confidence)
                # TODO(follow-up): Telegram alert from here once the voice check-in moves off the laptop.
        elif ev.type == "heartbeat" and ev.state:
            device["heartbeat_state"] = ev.state   # lets the dashboard spot a lost activity change

        st.touch(ev.device_id, device)
        return {"ok": True, "duplicate": not new}

    return app


app = create_app() if os.environ.get("SILVERGUARD_NO_AUTOAPP") != "1" else None
