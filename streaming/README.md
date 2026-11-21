# SilverGuard streaming: watch → Cloud Run → Firestore

> **What this file is:** the starting point for everything in `streaming/`. It covers why streaming exists, how the parts fit together, what each file does, and how to set it up, test it and deploy it.
> Every file in this folder also starts with its own "what this file does" note.

Everything to do with sending the watch's live data online lives in this folder. Nothing outside `streaming/` is changed by it.

**Status (2026-11-21):**
- The server is written and tested locally.
- The ESP32 code compiles for the ESP32-C3.
- The USB bridge, dashboard page and terminal viewer are written.
- 32 tests pass.
- **Not yet done:** deployed to Google Cloud, or run on the real watch.

---

## 1. Context

- **Decision (see `context.md`, 2026-11-20):** the server moves to **Google Cloud Run** and the data moves to **Firestore**, replacing Railway and Supabase.
- **What is streamed:**
  - The watch's activity state (`still` / `moving`, from the on-watch HAR model), **only when it differs from the previous state**.
  - Confirmed falls.
  - A heartbeat once a minute.
- **Why only changes:** HAR runs every 0.5 s, which is 172,800 results a day. Sending only changes means tens to a few hundred writes a day, well inside Firestore's free tier (20k writes and 50k reads a day).
- **Why a heartbeat:** with change-only sending, a quiet stream looks the same as a dead watch. The heartbeat tells "still for an hour" apart from "watch off or out of Wi-Fi".

## 2. Architecture

```
 ┌───────────── watch (ESP32-C3) ─────────────┐      ┌──── or, for now: watch on USB ────┐
 │ firmware/watch_streaming/                  │      │ firmware/FinalPrototype (as is)   │
 │  HAR every 0.5 s → debounce 3 s            │      │  prints "Activity: Moving" ...    │
 │  → queue change / fall / heartbeat         │      │          │ USB cable              │
 │  network task → HTTPS POST                 │      │  bridge/usb_to_cloud.py (PC)      │
 └──────────────────┬─────────────────────────┘      │  same debounce → same POST        │
                    │                                └─────────────────┬─────────────────┘
                    └────────────── POST /ingest ──────────────────────┘
                         JSON body + header X-Device-Key
                                     ▼
              ┌─────── Cloud Run: server/main.py (FastAPI) ───────┐
              │ check key → check data → add server time          │
              │ doc id = boot_id-seq → a retry is never doubled   │
              └──────────────────────┬────────────────────────────┘
                                     │ Admin SDK (service account)
                                     ▼
              ┌──────────────────── Firestore ────────────────────┐
              │ devices/watch-01                    live state     │
              │ devices/watch-01/transitions/{id}   change history │
              │ events/{id}                         falls          │
              └──────────────────────┬────────────────────────────┘
                                     │ read (dashboard/firestore_source.py)
                     ┌───────────────┴────────────────┐
             dashboard/live_page.py           tools/live_status.py
             (browser, Streamlit)             (terminal)
```

### Key design choices

| Choice | Why |
|---|---|
| Watch → **Cloud Run** → Firestore, never watch → Firestore | No Google credential on the watch. Firestore rules deny all client access (`firestore.rules`); only the server and the readers use a service account. |
| **Debounce** (a new class must hold 3 s) | HAR flickers near the still/moving boundary. Without a debounce, "status changed" can fire many times a minute. |
| **Separate network task** on the watch | An HTTPS POST takes about 0.5–2 s. In `loop()` it would stall sampling and fall detection. The ESP32-C3 has **one core**, so the task runs at the same priority as `loop()` and they share the CPU by time slices. |
| **Queue + retry in order** | Wi-Fi drops. Messages wait (32 on the watch, 500 in the USB bridge) and go out in order. Heartbeats are skipped while anything is waiting. If the queue is still full, the oldest is dropped. |
| **`boot_id` + `seq` as the document id** | If the reply is lost, the sender retries. The server's `create()` fails on an existing id, so nothing is counted twice. |
| **Server timestamp** (`received_at`) | The watch has no real-time clock. `uptime_ms` is kept for ordering and debugging. |
| **One key per device** (`DEVICE_KEYS="watch-01=…"`) | One stolen key can't write as another watch. Keys live in Secret Manager, not in code. |
| **Change history instead of `activity_days`** | `context.md` planned one doc per day with 1,440 minute slots. The change history holds the same information with far fewer writes. ⚠ **Team to agree**: this changes the §4 data contract. |

## 3. Files

| File | What it does |
|---|---|
| `README.md` | This file. |
| `requirements.txt` | Python packages for the PC side (tools, bridge, dashboard page, tests). |
| `.gitignore` | Keeps `stream_config.h` (Wi-Fi password, key) and `server/.env` out of git. |
| `firestore.rules` | Database rules: no direct access for anyone. |
| **server/** | *runs on Google Cloud Run* |
| `server/main.py` | The ingest service: `POST /ingest`, `GET /health`. Checks, timestamps and writes to Firestore. |
| `server/requirements.txt` | Packages the container installs. |
| `server/Dockerfile` | Recipe Cloud Run uses to build the container. |
| `server/.dockerignore` | Files not uploaded into the container. |
| `server/.env.example` | Settings for running `main.py` on your PC. |
| `server/deploy.ps1` | One-command Google Cloud setup and deploy (Windows PowerShell). |
| **firmware/** | *runs on the ESP32* |
| `firmware/stream_demo/stream_demo.ino` | Test sketch: fake still/moving/fall from the Serial Monitor. No sensor needed. |
| `firmware/*/silverguard_stream.h` | The watch's streaming library: Wi-Fi, debounce, queue, network task, heartbeat. The same file is in both sketch folders. |
| `firmware/*/stream_config.example.h` | Template for Wi-Fi, URL and device key. Copy it to `stream_config.h`. |
| `firmware/watch_streaming/watch_streaming.ino` | The full watch firmware (`FinalPrototype`) plus 4 `STREAMING:` lines. |
| `firmware/watch_streaming/*_model*.h`, `fall_features.h`, `test_vectors.h` | Copies of the model files the firmware needs, unchanged. |
| **bridge/** | *runs on the PC* |
| `bridge/usb_to_cloud.py` | Streams a USB-connected watch online until it has Wi-Fi. |
| **dashboard/** | *reads the data* |
| `dashboard/firestore_source.py` | Functions to read the live state, history and falls from Firestore. |
| `dashboard/live_page.py` | Streamlit page showing the live stream. |
| **tools/** | |
| `tools/ingest_client.py` | Python version of the watch's sender. Shared by the two tools below. |
| `tools/send_test_event.py` | Send one test message from the command line. |
| `tools/live_status.py` | Print the live state and recent changes from Firestore in the terminal. |
| **tests/** | `python -m pytest streaming/tests -q` |
| `tests/test_ingest.py` | Server: storing, duplicates, keys, bad data (13 tests). |
| `tests/test_bridge.py` | USB line parsing, debounce, retry queue, client (15 tests). |
| `tests/test_firestore_source.py` | Online check, time-in-state maths (4 tests). |

## 4. Message format (watch → server)

```json
{ "device_id": "watch-01", "type": "activity", "boot_id": "3fa9c2d1", "seq": 17,
  "uptime_ms": 523011, "state": "moving", "detector": "har_v0.5" }
```

| field | types | meaning |
|---|---|---|
| `type` | all | `activity` \| `fall` \| `heartbeat` |
| `boot_id` | all | 8 hex chars, random at each power-on |
| `seq` | all | 0, 1, 2… since boot |
| `uptime_ms` | all | `millis()` on the watch |
| `state` | activity (required), heartbeat, fall | `still` \| `moving` |
| `confidence`, `peak_g`, `turn_deg` | fall | forest score, impact peak, wrist turn (−1 = not measured) |
| `detector` | activity, fall | model version |

Replies:
- `200 {"ok":true,"duplicate":false}`: stored.
- `401`: wrong key or unknown device.
- `422`: bad data.

Senders drop a message on 401/422 because retrying won't help. Everything else is retried with backoff from 1 s to 30 s.

## 5. Firestore layout

| Path | Fields | Written when |
|---|---|---|
| `devices/watch-01` | `state`, `since`, `last_seen`, `boot_id`, `last_seq`, `heartbeat_state`, `last_fall_at` | every message (merge) |
| `devices/watch-01/transitions/{boot-seq}` | `state`, `received_at`, `uptime_ms`, `detector` | each activity change |
| `events/{boot-seq}` | `type: "fall"`, `device_id`, `t`, `confidence`, `peak_g`, `turn_deg`, `detector`, `status: "open"` | each confirmed fall |

`heartbeat_state` is the watch's own view of its state, sent with each heartbeat. If it differs from `state`, a change message was lost.

## 6. Step by step

### A. Try it on your PC (no Google account, about 10 min)

```powershell
.venv\Scripts\python -m pip install -r streaming/requirements.txt
cd streaming/server
$env:STORE="memory"; $env:DEVICE_KEYS="watch-01=dev"; ..\..\.venv\Scripts\python -m uvicorn main:app --port 8080
```

In a second terminal:
```powershell
.venv\Scripts\python streaming/tools/send_test_event.py --url http://localhost:8080 --key dev activity moving
```

Open http://localhost:8080/docs to send requests by clicking.

### B. Deploy to Google Cloud (one time)

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), run `gcloud auth login`, and create a project with billing linked. The free tier covers this project.
2. `cd streaming/server` then `.\deploy.ps1 -Project <project-id>`. Note the **device key** and **URL** it prints.
3. Firebase console → Firestore → Rules: paste `firestore.rules` → Publish.
4. Test: `python streaming/tools/send_test_event.py --url <URL> --key <key> activity moving`, then look in the Firestore console.

**Cold starts:** with no instance kept warm, the first request after a quiet period waits a few seconds. Senders retry anyway. For fall alerts in the final system, consider `--min-instances=1` (small monthly cost).

### C. Stream from the watch

- **Now, over USB** (the watch runs `FinalPrototype` unchanged):
  ```
  python streaming/bridge/usb_to_cloud.py --port COM5 --url <URL> --key <key>
  ```
  Close `watch_bridge.py` first, because only one program can use the COM port.
- **Wi-Fi test without the sensor:** flash `firmware/stream_demo`. In the Serial Monitor, type `m` / `s` / `f`.
- **Wi-Fi, real watch:** flash `firmware/watch_streaming`.
  1. Copy `stream_config.example.h` to `stream_config.h` and fill it in.
  2. Set **Tools → Partition Scheme → "Huge APP (3MB No OTA)"**. This is **required**. Checked 2026-11-21: `watch_streaming` is 1.30 MB, which is 99% of the default 1.31 MB partition (no room for any change) and 41% of Huge APP.
  3. After flashing, check that the 10 s status line shows no more sample gaps than before.

  The USB voice check-in with `watch_bridge.py` still works with this firmware. Streaming log lines start with `# stream:` and never contain `FALL DETECTED` or `# EVENT FALL`.

### D. Watch it arrive

```
gcloud auth application-default login                    # once
python streaming/tools/live_status.py --project <project-id>
streamlit run streaming/dashboard/live_page.py -- --project <project-id>
```

The dashboard reads 1 document per refresh for the live state, plus 1 query every 30 s for the 24 h history. The watch counts as offline if `last_seen` is older than 2.5 min (two missed heartbeats).

## 7. Keeping the copies in sync

Arduino needs every file in the sketch's own folder, so some files exist twice:
- `silverguard_stream.h` and `stream_config.example.h` are in both `stream_demo/` and `watch_streaming/`. Edit one and copy it to the other.
- `watch_streaming/` is a copy of `firmware/FinalPrototype/`, dated 2026-11-21. If FinalPrototype changes, copy it again and re-add the 4 lines marked `STREAMING:`.

## 8. Open items

- [ ] Team agrees on the change history replacing `activity_days` (`context.md` §4).
- [ ] Deploy (`deploy.ps1`) and check end-to-end with `stream_demo`.
- [ ] Flash `watch_streaming` and check that sampling has no extra gaps.
- [ ] Pin the server certificate (`STREAM_ROOT_CA`) instead of `setInsecure()`.
- [ ] Telegram alert from Cloud Run on a fall (TODO in `server/main.py`).
- [ ] Port `basestation/dashboard/` from Supabase to `dashboard/firestore_source.py`.
- [ ] Battery: measure the drain with Wi-Fi on.
