# SilverGuard Progress Log

The running record of **what exists in the project, block by block**, and **every change** made to it.
**Update this file whenever any file changes:** add an entry to the Change log (Part C, newest first) and update the status of the block you touched in Part B.

- `context.md` = design and decisions (the *why*).
- `progress.md` = what has actually been built, file by file, and when it changed (the *what*).

Last full review: **2026-11-17** (last change: 2026-11-26)

---

## Part A. Overview by block

| # | Block | Owner | Status | Main location |
|---|---|---|---|---|
| 1 | Watch firmware: logger + on-device models (v0.4) | Obj 2 | Done, works on the prototype | `firmware/silverguard_logger/` |
| 2 | Watch firmware: early activity test sketches | Obj 2 | Done, superseded by block 1 | `firmware/tests/` |
| 3 | Watch hardware: speaker + vibration motor sketch | Obj 1 (groupmate) | Experimental | `Melv-Codes/` |
| 4 | Fall model: datasets, training, export to C | Obj 2 | Done (v0.4) | `notebooks/fall model/` |
| 5 | Still/moving model: UCI-HAR, Capture-24, HAR70+ | Obj 2 | Done; simple threshold kept | `notebooks/Model based of UCI DataSet/` |
| 6 | Still/moving model: stronger-feature experiments | Obj 2 | Done; failed the pre-set test | `notebooks/Developing stronger movement classifier/` |
| 7 | Voice check-in pipeline (ears, brain, mouth) | Obj 3 (built for testing) | Done on a laptop | `basestation/voice/` |
| 8 | Watch ↔ laptop USB bridge (watch speaker + mic → Telegram) | Obj 2/3 | Done, main program | `basestation/voice/watch_bridge.py` |
| 9 | Cloud sync (Supabase → moving to Firestore) | Obj 2 | Supabase code done; to be ported to Firestore (decided 2026-11-20) | `basestation/sync/` |
| 10 | Telegram caregiver alerts | Obj 3 | Done, bot set up in `.env` | `basestation/alerts/` |
| 11 | Caregiver dashboard (Streamlit) | Obj 2 | Done on sample data | `basestation/dashboard/` |
| 12 | Configuration, secrets, environment | All | Done | `basestation/config.yaml`, `.env.example`, `.streamlit/`, `requirements.txt` |
| 13 | Tests | Obj 2/4 | 97 tests passing (as of 2026-11-10) | `tests/` |
| 14 | Evaluation (Obj 4) | Obj 4 | Not started (placeholder only) | `evaluation/` |
| 15 | Placeholders: detection, ingest, rules | n/a | Empty folders | `basestation/detection/`, `ingest/`, `rules/` |
| 16 | Data folders | n/a | Empty in git (data is git-ignored) | `data/` |
| 17 | Documentation | Obj 2 | Done | `context.md`, `docs/`, READMEs, `progress.md` |
| 18 | Live streaming: watch → Cloud Run → Firestore | Obj 2 | **Live on Google Cloud** (project `silverguard-3cde2`), tested end-to-end from a PC; not yet run on the watch | `streaming/` |
| 19 | Activity model v2: Capture-24 forest / CNN / XGBoost + HMM, cross-validation, GOTOV + HAR70+ tests, axis alignment, C export | Obj 2 | **Done on datasets; exported to C; not yet on the watch** | `notebooks/still-moving-DL/` |

---

## Part B. Every block in detail

### Block 1. Watch firmware: logger + on-device models (v0.4)
**Folder:** `firmware/silverguard_logger/` · **Hardware:** ESP32-C3 + MPU-6050 at 50 Hz (SDA GPIO 3, SCL GPIO 4, address 0x68)

| File | What it does |
|---|---|
| `silverguard_logger.ino` (478 lines) | The main sketch. Reads the MPU-6050 at 50 Hz (±16 g = 2048 LSB/g, ±2000 dps = 16.4 LSB/dps). Every 0.5 s it runs the still/moving forest and the fall pipeline. |
| `fall_features.h` | Window features written in C to match the Python training code exactly: 14 fall features (accel/gyro magnitude mean, std, min, max, range, jerk, energy, tail std) and 5 HAR features. Uses population std, like numpy. |
| `fall_model.h` | Generated. Fall trigger 2.5 g, forest threshold 0.266667 (fraction of trees voting fall), 125-sample (2.5 s) window, 14 features. |
| `fall_model_trees.h` (11,897 lines) | Generated. The 30-tree fall random forest as C code. |
| `har_model.h` | Generated with emlearn. Still/moving forest on 5 magnitude features. |
| `test_vectors.h` | Generated. 3 real FallAllD windows ("fall", "daily activity", "still") plus the answers Python expects, for the boot self-test. |
| `record.py` | Laptop script. Records one labelled trial over USB (`python record.py COM5 fall_forward_01 --seconds 20`). Saves raw CSV and an `_events.csv` file, and prints sample count, real sample rate, gaps, peak \|a\|, peak \|gyro\| and the number of fall events. |
| `README.md` | Wiring, Arduino IDE settings, boot checks, how to read the output, how to record, limits for the report. |

**Sketch sections, in order:**
1. **Switches:** `RAW_STREAM` (0 = readable demo lines, 1 = raw CSV for `record.py`), `HALT_ON_FALL` (1 = stop and repeat the alert until `r` is typed), `SELF_TEST` (1 = check C maths against Python at boot).
2. **Settings:** pins, sample rate, confirmation thresholds.
3. **MPU-6050 registers:** written, then read back and checked (5 `# reg ... OK` lines at boot).
4. **I2C helpers:** `writeReg`, `checkReg`, `readSample`.
5. **`printHeader`:** `#` header lines that `record.py` saves at the top of each file.
6. **`calibrateGyro`:** keep still for about 2 s at boot; the reading is taken as gyro bias. Prints a WARNING if the watch moved.
7. **`selfTest`:** runs `test_vectors.h` through the C code and prints PASS/FAIL per window.
8. **`runModels`** (every 0.5 s): HAR forest on the last 2.56 s; fall check on the last 2.5 s.
9. **`angleDeg` + `confirmPending`:** stage 3 and 3b confirmation, 2.5 s after the spike.
10. **`setup` / `loop`:** 50 Hz sampling, `# status:` line every 10 s (shows `falls=` and `max_model_us=`).

**The 4-stage fall pipeline on the watch:**
1. **Spike:** peak \|a\| > 2.5 g in the last 2.5 s. Otherwise the forest does not run (`fall check: -`).
2. **Forest:** 30-tree random forest score ≥ 0.27.
3. **Wrist turned:** gravity direction 1.5–0.5 s before the spike vs 1.0–2.5 s after it must differ by more than `CONFIRM_MIN_TURN_DEG` = 20°. This rejects table hits and claps.
4. **Falling motion (3b):** the candidate is rejected if, in the 1 s before the spike, \|a\| stayed above 0.85 g **and** rotation stayed below 150 dps. This rejects putting the watch down.
- After a candidate, new candidates are ignored for 5 s. If the data stream breaks during confirmation, it alarms anyway.
- Output: `ALERT ... FALL DETECTEDDD!!!` (demo mode) or `# EVENT FALL,...` / `# EVENT REJECTED,...` (recording mode).

**Results:** works on the prototype; table hits and put-downs no longer trigger it. FallAllD held-out: 94% of fall windows, all 73 fall recordings, about 86 false alarms/h. Stage 3 on WEDA-FALL: table hits 54→28, older adults' false alarms 49→26, alarmed falls 342→338. Stage 3b loses 1 of 809 alarmed falls.
**Open:** put-down thresholds (0.85 g, 150 dps) are guesses and must be tuned on our own recordings.

---

### Block 2. Watch firmware: early activity test sketches
**Folder:** `firmware/tests/` · superseded by Block 1, kept for reference.

| File | What it does |
|---|---|
| `silverguard_activity.ino` (247 lines) | First on-device still/moving test. 128-sample (2.56 s) window, result every 25 samples (0.5 s), `mag_std` threshold of 0.05 (placeholder). Two columns: raw MOVING/still, plus a debounced WALKING/stationary (moving for 8 s → walking, still for 3 s → stationary). Calibrates accel offsets at startup (sensor flat and still). |
| `har_model.h` | emlearn export of the UCI still/moving forest. |
| `test_with_har_RF_model/test_with_har_RF_model.ino` (254 lines) | Same as above plus a third column: the random forest (`har_model.h`) next to the threshold. |
| `test_with_har_RF_model/har_model.h` | Copy of the forest header for this sketch. |

**Related recordings:** `tests/esp32_recordings/` (see Block 13).

---

### Block 3. Watch hardware: speaker + vibration motor sketch (groupmate, Obj 1)
**File:** `Melv-Codes/Speaker_Gyro_Vib_copy_20261113095344/Speaker_Gyro_Vib_copy_20261113095344.ino` (392 lines) · committed 2026-11-13 by Siblemmm

- **Board:** ESP32-WROOM-32 (not the C3). MPU-6050 on SDA 21 / SCL 22. Vibration motor on GPIO 15. I2S speaker amp on BCLK 26, LRC 25, DOUT 27.
- **Bluetooth A2DP sink:** the board shows up as "ESP32 Bluetooth Speaker", so a phone can stream audio to it.
- **Logic:** samples the MPU-6050 at 50 Hz and computes dynamic acceleration \|\|a\| − 1 g\|. When it rises above **1.0 g** (rising edge), it turns the motor on at full power and unmutes the speaker (volume 100) for **10 s**, then turns both off. It prints readings at 10 Hz.
- **Functions:** `motorOn`, `motorOff`, `speakerOn`, `speakerOff`, `writeReg`, `readSensor`, `setup`, `loop`.
- **Status:** a hardware test of speaker + motor + IMU together. Not yet connected to the fall model (uses a simple 1 g threshold). Different board and pins from Block 1, so it needs to be merged.

---

### Block 4. Fall model: datasets, training, export to C
**Folder:** `notebooks/fall model/`

| File | What it does |
|---|---|
| `choosing dataset.ipynb` | Loads and audits WEDA-FALL, UMAFall and FallAllD (file counts, shape, missing values, relevance to a 50 Hz wrist sensor). Result: **FallAllD chosen** for training. WEDA-FALL: 969 recordings, wrist, about 50 Hz but irregular. UMAFall: 746 recordings, wrist only about 20 Hz, 86,233 missing values. FallAllD: 6,605 recordings, 238 Hz, no missing values. |
| `dataset explained.ipynb` | Plain-words description of the three datasets (devices, people, file layout, limits such as WEDA's 4 g clipping and no older-adult falls). |
| `will gyro reading help.ipynb` | Finds the FallAllD gyro unit by physics (LSM9DS1 chip; **0.070 deg/s per count** is the only scale that matches the accelerometer's turn). Explores the gyro and tests an "accel AND gyro" rule. |
| `modeltrainingFallAllD.ipynb` | Fall classifier on FallAllD wrist device D2: counts→g (÷4096), 238→50 Hz (`resample_poly`), 2.5 s windows, **split by person**, features, models (rules, logistic regression, random forest), scored by recall and false alarms/h. |
| `testing FallAllD model on WedaFall.ipynb` | Two-stage model (2.5 g trigger → forest), with vs without gyro (ablation), trained on FallAllD with accel clipped at 4 g, tested on WEDA-FALL. Result: gyro gives about 10–17% fewer false alarms; WEDA catches 322–340/350 falls but false alarms are 4–8× higher (domain shift). |
| `fallmodelbymyself.ipynb` | Small scratch notebook (4 cells). |
| `fad_utils.py` (167 lines) | Shared helpers so every fall notebook prepares data the same way: `read_dat`, `gyro_path`, `remove_gyro_bias`, `load_device`, `make_windows`, `clip_accel`, `load_weda`, `windows_from_list`, `make_features`, `feature_sets`. |
| `export_models_to_c.py` (173 lines) | Trains the 30-tree on-device fall forest, picks the threshold using the **tree-vote fraction** (what emlearn computes in C), and writes `fall_model.h`, `har_model.h` and `test_vectors.h` into `firmware/silverguard_logger/`. |
| `confirm_stage_eval.py` (114 lines) | Simulates the exact on-device pipeline on FallAllD and WEDA-FALL to justify stage 3 (20° wrist turn) and stage 3b (falling motion). |

**Fall model results** (held-out people, FallAllD wrist):

| Method | Recall | False alarms/h | Falls caught |
|---|---|---|---|
| Rule: spike > 2.5 g | 100% | ~313 | 73/73 |
| Rule: spike > 3 g | 93% | ~214 | 68/73 |
| Logistic regression | 99% | ~48 | 73/73 |
| Random forest | 97% | ~30 | 73/73 |
| Random forest, 30 trees (on watch) | 94% | ~86 | 73/73 |

**Summary notebook:** `notebooks/update_since_wed_4nov.ipynb`, a no-code write-up of the fall work since 4 Nov (datasets → model → WEDA test → prototype tuning).

---

### Block 5. Still/moving model: UCI-HAR, Capture-24, HAR70+
**Folder:** `notebooks/Model based of UCI DataSet/`

| File | What it does |
|---|---|
| `ClassicalML.ipynb` | UCI-HAR (30 people aged 19–48, phone at the waist, 50 Hz, 2.56 s windows). EDA, then logistic regression, random forest and SVM compared and tuned. Keeps only the first half of each 50%-overlap window to avoid leakage. |
| `features.py` (30 lines) | Shared feature code: `magnitude()`, `mag_features()` → 5 features (`mag_mean`, `mag_std`, `mag_min`, `mag_max`, `mag_range`). WIN = 128, HOP = 64. |
| `rf_mag_uci.joblib` | Random forest on the 5 magnitude features, trained on UCI (labels merged to still vs moving). Exported to `har_model.h`. |
| `test_har70plus.ipynb` | Runs the UCI model unchanged on HAR70+ (age 70+, back sensor). **93% accuracy** vs 86% for a one-rule `mag_std` baseline. Includes 6 leakage/sanity checks (baseline, per-subject spread, preprocessing match, label mapping, ...). |
| `capture24_windows.ipynb` | Capture-24 raw wrist files → per-participant feature parquet (100→50 Hz with a 20 Hz Butterworth low-pass first, 128-sample windows that never cross a gap, single-label windows only). Part 2 trains and compares models. |
| `capture24_test_participants.csv` | The fixed list of held-out Capture-24 participants. |
| `rf_mag_capture24.joblib` | Random forest trained on Capture-24. |
| `har_model.h` | emlearn export of the UCI forest. |
| `next_steps.ipynb` | Checklist written 6 Nov 2026: record own sessions, set the `mag_std` threshold from them, tune debounce, try richer features, test on GOTOV, on-device event logic. |

**Result:** on the wrist (Capture-24), the UCI model, the Capture-24 model and the one-rule baseline all score about **0.74 balanced accuracy**, a tie. Decision: the simple `mag_std` threshold for now.

**Update 2026-11-26:** superseded by Block 19. With 30 s windows, 16 features, XGBoost and an HMM, the new model beats this one in daily life (still vs moving kappa 0.739 vs 0.626 on 30 held-out Capture-24 people, run through the firmware's own `har_model.h`). The old model stays better when standing should count as still (see `notebooks/still-moving-DL/OLD_VS_NEW_MODEL.md`).

---

### Block 6. Still/moving model: stronger-feature experiments
**Folder:** `notebooks/Developing stronger movement classifier/`

| File | What it does |
|---|---|
| `dataset_eda_capture24_gotov_vs_uci.ipynb` | EDA of Capture-24 (151 people, about 2,560 labelled hours, daily life) and GOTOV (35 people, median age 64.6, about 24 h, scripted) vs UCI-HAR. Neither has a wrist gyroscope, so only 348 of UCI's 561 features can be recomputed. Standing on the wrist moves about 14× more than on UCI's waist. GOTOV's real rate is 85.7 Hz. |
| `eda_features.py` (270 lines) | Builds one window table from all three datasets with the same maths; caches per participant in `data/processed/eda`. Functions: `window_table`, `psd_rows`, `build_uci`, `build_capture24_participant`, `build_gotov`, ... |
| `feature_combinations.ipynb` | Tests whether any accel-only feature combination beats the `mag_std` threshold. Rule set in advance: gain ≥ 3 points on held-out Capture-24 **and** on GOTOV. |
| `feature_sets.py` (800 lines) | The experiment code: feature groups, window lengths, `run_cv` (GroupKFold on 140 training people), `lock_candidates` (saves the chosen set before any held-out data loads), `run_final`, `bootstrap_gain`, block-level false alerts, ESP32 cost estimate, `self_test`. |

**Result:** the locked candidate (20 trees, depth 8, 10 s windows, 16 features) gained **+6.0 points** on 11 held-out Capture-24 people (0.740 → 0.800) but **−1.0** on GOTOV (0.925 → 0.915). It **fails** the pre-set rule, so the threshold is kept. Estimated ESP32 cost: 1.8% CPU, 10.7 kB RAM, 116 kB flash.

**Note 2026-11-26:** Block 19 found GOTOV's wrist sensor is turned half a turn around its x axis compared with Capture-24's. With the axes lined up, still-vs-moving kappa on GOTOV rose from 0.895 to 0.990. The −1.0 on GOTOV here may be that axis problem rather than the features: worth re-checking.

---

### Block 7. Voice check-in pipeline (laptop test, to be reused on Railway)
**Folder:** `basestation/voice/`

| File | What it does |
|---|---|
| `settings.py` (33 lines) | Loads `config.yaml` (`voice_config()`, `dashboard_config()`) and `.env`. Paths: `AUDIO_CACHE_DIR`, `LOG_PATH` = `data/raw/voice_checkins.csv`. |
| `listen.py` (72 lines) | **Ears.** Records 7 s from the mic at 16 kHz (`record`), skips silence below RMS 0.005 (`is_silent`), transcribes with **faster-whisper** `small` on the CPU (`transcribe`), with an initial prompt mixing English and Chinese. Audio is never saved. |
| `understand.py` (122 lines) | **Brain.** Sends the transcript to **Claude Haiku 5.5** (`claude-haiku-5-5`, effort low, 8 s timeout) with structured output (`Reply` pydantic model), so the answer can only be `ok`, `help` or `none`. If unsure it picks `help`. Falls back to keywords on any failure. Returns a `Classification` (label, used_fallback, llm time). |
| `keywords.py` (81 lines) | **Offline backup brain.** Traditional→simplified Chinese, then checks help words **before** ok words (so "not okay" and "好痛" become help). Weak words ("no", "不是") count as help. Nothing recognised → `none`. |
| `speak.py` (118 lines) | **Mouth.** edge-tts voices `en-SG-LunaNeural` and `zh-CN-XiaoxiaoNeural`, MP3s cached by hash in `audio_cache/` so fixed lines play instantly and offline. Backup: pyttsx3 (Windows offline voice). Functions: `prepare`, `speak`, `say_line`, `prepare_all_lines`. |
| `checkin.py` (79 lines) | One check-in: ask → listen → understand. `run_check_in(reason, attempt, typed)` returns `{"reply", "transcript", "latency_s"}` (the §4 contract). Logs one CSV row per check-in (transcript only, never audio). |
| `demo.py` (117 lines) | Runs the whole flow from the keyboard. `handle_event()` writes the event to the cloud, runs up to 2 attempts (1 retry) inside a 45 s budget, then: `ok` → false alarm, no alert; `help`/`none` → Telegram alert, then spoken "contacting your caregiver". If the check-in itself crashes (e.g. watch mic error) it still alerts (`alert_checkin_failed`). `--typed` lets you type the reply instead of speaking. This retry/alert logic is a **stand-in** for the real rules engine (Obj 3). |
| `audio_cache/` | Generated MP3s (git-ignored). |

**Spoken lines** (in `config.yaml`, scripted, not LLM-made): question, ok, alert, in English and Mandarin.
**Results:** 41/41 test replies classified correctly (English, Mandarin, Singlish, mixed). About US$0.20 per 1,000 check-ins.
**Open:** cloud speech-to-text for the Railway server is not chosen yet.

---

### Block 8. Watch ↔ laptop USB bridge (main program: fall → watch speaker + mic → Telegram)
**Files:** `basestation/voice/watch_bridge.py` (152 lines), `basestation/voice/esp32_audio.py` (copy of `Melv-Codes/ESP32-SpeakerUSBVer/esp32_audio.py`)

- Run: `python -m basestation.voice.watch_bridge` (auto-finds the port) or `--port COM5`; `--manual` = press Enter to test a check-in without a fall; `--quiet` hides the watch lines. (`--laptop-audio` exists only for the old v0.4 firmware; the prototype uses the watch's own audio.)
- The check-in uses the **watch's own MAX98357A speaker and INMP441 mic** over USB serial (Melv's protocol: `P` play chunk → `K` ack, `L` record → `M` + PCM), by swapping `speak._play_file`, `speak._speak_offline` and `listen.record`.
- Startup prints Watch / Telegram alerts / Online dashboard ON/OFF.
- On `FALL DETECTED` or `# EVENT FALL` it calls `demo.handle_event("fall", detector="ESP32-SpeakerUSBVer v0.5 (USB)")` (help / no reply / failed check-in → Telegram alert), then clears the buffer and sends `r` so the watch resumes.
- **Safety:** if the watch is unplugged (`SerialException`), a Telegram message tells the caregiver that falls are not being detected.
- Firmware: `Melv-Codes/ESP32-SpeakerUSBVer/ESP32-SpeakerUSBVer.ino`. Arduino Serial Monitor must be closed.
- `Melv-Codes/` is the groupmate's folder and is **not edited** by us; his `usb_voice_bridge.py` stays as his reference version.

---

### Block 9. Cloud sync to Supabase
**Folder:** `basestation/sync/`

| File | What it does |
|---|---|
| `schema.sql` (64 lines) | Run once in the Supabase SQL editor; safe to re-run. 5 tables: **`events`** (id, t, type, confidence, detector, status open/acknowledged/resolved/false_alarm, duration_min, snippet jsonb, alerted_at, acknowledged_at, resolved_at), **`checkins`** (event_id, attempt, transcript, reply ok/help/none, used_fallback, latency_s, llm_s), **`event_steps`** (timeline lines), **`status`** (single row: base_station_seen_at, watch_last_data_at, battery_pct), **`activity_minutes`** (minute, label sleeping/resting/light/active, enmo_mg). 3 indexes. Row-level security on with no policies, so the public key can read nothing; only `service_role` has access. |
| `uploader.py` (171 lines) | `Cloud` class. Every change is first appended to a local **outbox** (`data/raw/cloud_outbox.jsonl`) so the check-in never waits for the internet. A background thread uploads lines in order and keeps them through restarts while offline. Heartbeat every 30 s. Methods: `insert`, `update`, `step`, `pending`, `flush`, `heartbeat`, `close`. With no keys in `.env` it runs locally only. |

**Status:** code and tests done. **Supabase project not created yet.**

---

### Block 10. Telegram caregiver alerts
**File:** `basestation/alerts/telegram.py` (87 lines)

- `send_alert(text)` via the Telegram Bot API (5 s timeout), `is_configured()`, `find_chat_ids()`.
- CLI: `python -m basestation.alerts.telegram --find-chat-id` / `--test`.
- Sent straight from the laptop/server, so alerts work even if the dashboard is asleep.
- Setup steps (BotFather → token → chat id → `.env`) are at the top of the file.

**Status:** code done. **Bot not created yet.**

---

### Block 11. Caregiver dashboard (Streamlit)
**Folder:** `basestation/dashboard/` · built from `Caregiver dashboard@1x.png`

| File | What it does |
|---|---|
| `app.py` (460 lines) | The page. Password gate, header, **connection banners** (red "Can't see" when the heartbeat or watch data is stale for more than 120 s), **event banner** with acknowledge/resolve buttons and an optional Call button, **summary cards**, **activity timeline** (Plotly), **event log**, **event detail** (impact snippet chart + what the voice check-in did, step by step), **system health**. Refreshes every 10 s. Sidebar: "Use sample data" toggle and demo switches for "base station offline" / "watch disconnected". |
| `metrics.py` (236 lines) | Plain Python (no Streamlit), so it is testable and reusable for Obj 4: time formatting, `connection_state`, `event_title`, `what_they_said`, `outcome`, `active_alert`, `week_counts`, `activity_runs`, `right_now`, `active_24h`, `sleep_last_night`, `health`. Labels: sleeping/resting/light/active. |
| `data.py` (118 lines) | Two interchangeable sources with the same shape: `SampleSource` (built-in data, can simulate offline states) and `SupabaseSource` (reads the 5 tables; acknowledge/resolve write back and add a timeline step). |
| `sample_data.py` (164 lines) | Made-up data that mirrors the PNG: a full day plan, night wake-ups, ENMO levels per label, events, check-ins, steps and impact snippets, built around "now". Shown with a "Sample data" badge. |
| `style.css` (105 lines) | Custom styling to match the design. |
| `requirements.txt` | Cloud-only install list (streamlit, plotly, pandas, pyyaml, supabase). |
| `README.md` | Steps to go online: run locally → create Supabase → Telegram bot → deploy to Streamlit Community Cloud (private). Table of where each secret lives. Privacy notes. |
| `Caregiver dashboard@1x.png` | The design the page was built from. |

**Status:** done and working on sample data. Real data appears once the server writes rows.

---

### Block 12. Configuration, secrets, environment

| File | What it holds |
|---|---|
| `basestation/config.yaml` | All starting values: sensor (50 Hz, g, deg/s), windows (2.5 s detector, 0.5 overlap, 30 s activity epoch), ENMO cut-points (45/100/400 mg), fall (2.5 g, 10 s stillness check), inactivity (N not decided), voice (45 s budget, 1 retry, 7 s recording, whisper `small`, Claude model/effort/timeout, TTS voices, spoken lines), dashboard (placeholder elder name "Mdm Lim", UTC+8, 120 s stale limits, 10 s refresh, url). |
| `.env.example` | Empty keys: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. |
| `.streamlit/config.toml` | Theme colours from the PNG (primary #17695A), minimal toolbar, usage stats off. |
| `.streamlit/secrets.toml.example` | Supabase URL/key, `dashboard_password`, `elder_phone`. |
| `.gitignore` | Keeps out `data/raw/*`, `.env`, `.venv`, caches, audio cache, open datasets, `data/processed/`, `secrets.toml`. |
| `requirements.txt` | Python 3.11. Shared (pyyaml, python-dotenv, pydantic, numpy, pytest), voice (anthropic, faster-whisper, sounddevice, soundfile, edge-tts, pyttsx3), online (supabase, requests, **pyserial** (new), streamlit, plotly, pandas). |

---

### Block 13. Tests
**Folder:** `tests/` · run with `pytest` · **97 tests passing** as of 2026-11-10 (LLM 41/41)

| File | What it covers |
|---|---|
| `test_understand.py` | Keyword backup on every phrase, silence skips the LLM, LLM failure falls back to keywords, live LLM on every phrase. |
| `voice_phrases.csv` | 41 test replies (text, expected label, language, hard flag): English, Mandarin, Singlish, mixed. |
| `test_sync.py` | Outbox keeps rows while offline and uploads them in order, a damaged outbox line is skipped, no keys = local only, demo `help` reply alerts and uploads, demo `ok` reply = false alarm with no alert. |
| `test_dashboard.py` | Newest event is an open fall, outcome labels, week counts, health numbers, activity runs don't overlap, sleep card, a dead base station is never shown as OK, formatting, Supabase rows parse, page renders and the acknowledge button works, page shows the "Can't see" state. |
| `esp32_recordings/record_session.py` | Logs the early activity sketch (Block 2) over serial (COM5) and lets you type labels as you go. |
| `esp32_recordings/analyse.py` | Groups results into still/moving runs and lines them up with the typed labels. |
| `esp32_recordings/log.txt`, `labels.csv` | One short sample log (sensor still, std about 0.002 g); labels file is empty. |
| `HOW_TO_RECORD.md` | **New 2026-11-26.** Step-by-step guide for recording watch data to test the activity models: firmware switch (`RAW_STREAM 1`), how to wear the watch, file naming (the name is the label), the 6-position calibration, 3–5 min per activity, long normal wear for the axis setup, a log sheet, running `test_watch_recording.py`, troubleshooting, safety and privacy. |

---

### Block 14. Evaluation (Obj 4)
**Folder:** `evaluation/`, only `__init__.py`. **Not started.**
To do: confusion matrix, fall recall and false alarms/h on our own recordings; fall → alert time; dialogue errors; usability (SUS); privacy check. Split by person/session only.

---

### Block 15. Placeholders
`basestation/detection/`, `basestation/ingest/`, `basestation/rules/`: only empty `__init__.py`. Detection runs on the watch (Block 1). The rules engine is currently the stand-in in `demo.py` (Block 7) and belongs to Obj 3.

---

### Block 16. Data
- `data/raw/`, `data/labels/`: empty in git (`.gitkeep`). Holds recordings, `voice_checkins.csv` and `cloud_outbox.jsonl` locally; git-ignored for privacy.
- `data/open datasets/`: FallAllD, WEDA-FALL, UMAFall, UCI-HAR, Capture-24, GOTOV, HAR70+ (local only, git-ignored).
- **Our own watch recordings: not started.** This is the top priority (roadmap phase A).

---

### Block 17. Documentation

| File | What it is |
|---|---|
| `context.md` | Source of truth: aim, team/ownership, architecture, interface contracts, detection, dashboard, voice, evaluation, roadmap A–F, conventions, status, session log. |
| `docs/SilverGuard_Progress_Update.docx` | Team-facing progress update (written 2026-11-11). **Uncommitted.** |
| `progress.md` | This file. |
| `firmware/silverguard_logger/README.md`, `basestation/dashboard/README.md` | How-to guides (see Blocks 1 and 11). |

### Block 18. Live streaming: watch → Cloud Run → Firestore
- **Status:** deployed and working (2026-11-21). Cloud Run service `silverguard-ingest`, region `asia-southeast1`, project `silverguard-3cde2`, Firestore `(default)` database. Test messages sent from a PC were stored in Firestore and shown by `tools/live_status.py`. Not yet run on an ESP32.
- **Find the URL / key:** `gcloud run services describe silverguard-ingest --region asia-southeast1 --format="value(status.url)"` and `gcloud secrets versions access latest --secret=silverguard-device-keys` (the part after `watch-01=`). Never commit the key.
- **Where:** `streaming/`. Everything streaming-related is in this folder. `streaming/README.md` explains the architecture and has a table of every file. Each file starts with a "what this file does" note.
- **What it does:** sends the still/moving state only when it changes (3 s debounce), confirmed falls straight away, and a heartbeat every 60 s, over HTTPS to a Cloud Run service (`server/main.py`), which writes to Firestore.
- **Ways to send:** `firmware/watch_streaming/` (full FinalPrototype firmware + Wi-Fi, 4 `STREAMING:` lines), `firmware/stream_demo/` (test sketch, no sensor), or `bridge/usb_to_cloud.py` (USB watch → PC → cloud, the stopgap).
- **Ways to read:** `dashboard/firestore_source.py` (shared read functions), `dashboard/live_page.py` (Streamlit), `tools/live_status.py` (terminal).
- **Next:**
  1. Make a new device key (`deploy.ps1 -NewKey`): the current one appeared in a screenshot.
  2. Test Wi-Fi with `stream_demo` (2.4 GHz network).
  3. Flash `watch_streaming` (Huge APP) and check sampling has no extra gaps.
  4. Telegram fall alert from Cloud Run.
  5. Port `basestation/dashboard/` to Firestore.
  6. Team agrees on the change history replacing `activity_days`.

---

### Block 19. Activity model v2 (still / moving and 4 activity levels)
**Folder:** `notebooks/still-moving-DL/` (first committed as `notebooks/DEEPDEEPDEEP_learning/`) · **Status:** done on datasets, exported to C, not yet on the watch

Built to the spec "small 1D CNN vs random forest on Capture-24, fairly compared", then extended. Every notebook has a beginner's "How this works" section, and the later ones check their own written conclusions against the numbers.

| File | What it does |
|---|---|
| `deeplearn.ipynb` | Stage 0. Capture-24 (151 people) → 30 s windows at 50 Hz (20 Hz low-pass, then every 2nd sample), Walmsley 4 classes (sleep, sedentary, light, moderate-vigorous), windows kept only if one label covers 90%. 301,164 windows (2,510 h). |
| `common.py` | Shared code: the person split (30 fixed test people), the 5 cross-validation folds, the 16 features, the HMM (learned from labels, Viterbi), the scores, a paired bootstrap. |
| `stage1_rf_hmm.ipynb` | Random forest on 16 features + HMM. Test: 4-class kappa 0.701 → 0.814 with the HMM. |
| `stage2_cnn_hmm.ipynb` | 1D CNN (36,580 numbers) on the raw signal + the same HMM. 0.659 → 0.768. |
| `stage3_xgb_hmm.ipynb` | XGBoost (GPU) + the same HMM. 0.696 → 0.818. |
| `cv_trees.ipynb`, `cv_cnn.ipynb`, `cv_compare.ipynb` | 5-fold cross-validation on all 151 people (GPU only; Keras on PyTorch for the CNN). XGBoost + HMM 4-class 0.822, still vs moving 0.812; the HMM adds +0.11 to +0.12 for every model. |
| `stage4_summary.ipynb` | Summary and verdict: XGBoost + HMM recommended. |
| `stage5_gotov.ipynb` | GOTOV (35 people aged 60–81, different sensor, never trained on): the models carry over. |
| `stage6_har70_walking.ipynb` | HAR70+ (ages 70–95): cadence median 104 steps/min; 25% of walking in bouts under 30 s. Found that GOTOV's "walking called light" was mostly a turned sensor, not age. |
| `stage7_axis_alignment.ipynb`, `watch_alignment.py`, `explaination-orientation.md` | The 48 possible axis setups, what a wrong one costs (up to −0.20 kappa), a tool that finds the setup without labels, per-axis calibration (sphere fit), the watch procedure and firmware code. |
| `stage8_export_xgboost.ipynb`, `export/` | Smaller XGBoost for the watch (depth 3, 300 rounds, 163 kB), live HMM (uses only the past), final model trained on all 151 people, exported to C (`sg_xgb.h`, `sg_xgb_model.h`, `sg_xgb_test_vectors.h`). Compiled on the laptop: same answer as Python for 100% of 8,558 windows. |
| `stage9_old_vs_new.ipynb` | Old watch model (`har_model.h`, run through the firmware's own C code) vs the new one. Capture-24: kappa 0.626 vs 0.739. GOTOV: 0.862 vs 0.932 with standing = moving, 0.957 vs 0.876 with standing = still. |
| `test_watch_recording.py`, `rf_predict.py` | Tests both models on the watch's own recordings (`record.py` or Waveshare `capture_csv.py` files): calibration, axis setup, scores, firmware constants. `rf_predict.py` runs the Stage 1 forest in anaconda (older scikit-learn). |
| `OLD_VS_NEW_MODEL.md`, `Whats-Next.md` | The detailed old-vs-new explanation; the next steps (fall detector re-test and improvement, watch recordings, integration). |
| `results/` | Models (`xgb_watch_all151.json` + `hmm_all151.npz` are the exported final model), result tables. Large prediction files are git-ignored; the notebooks remake them. |

**Environment:** `.venv-tf` (local only; rebuild it from `requirements-dl.txt`): TensorFlow 2.21 + Keras 3.15, PyTorch 2.11 with CUDA, XGBoost 3.2 with CUDA, Zig C compiler. Training on the RTX 3060 only.

**Next:** see `Whats-Next.md`: record on the watch (`tests/HOW_TO_RECORD.md`), axis setup + calibration in the firmware, add the C model, decide what "moving" means, then the fall detector.

---

## Part C. Change log (newest first)

### 2026-11-26: Activity folder renamed to `notebooks/still-moving-DL/`, merged into `main`
- `notebooks/DEEPDEEPDEEP_learning/` → `notebooks/still-moving-DL/` (committed as a rename). References updated in `tests/HOW_TO_RECORD.md`, `export/sg_xgb.h`, `Whats-Next.md` and this file.
- `notebooks/still-moving-DL/requirements-dl.txt`: **new**. The Python packages and versions for the folder's environment (plus PyTorch with CUDA), so teammates can rebuild it.
- Branch `activity-model-v2` merged into `main`.

### 2026-11-26: Activity model v2 (Block 19) + recording guide
- `notebooks/DEEPDEEPDEEP_learning/`: **new folder** (built 2026-11-25 to 26). Stages 0–9, cross-validation, `common.py`, `watch_alignment.py`, `test_watch_recording.py`, `rf_predict.py`, `export/` (C model), `results/`, and the docs `explaination-orientation.md`, `OLD_VS_NEW_MODEL.md`, `Whats-Next.md`. See Block 19.
- `notebooks/DEEPDEEPDEEP_learning/.gitignore`: keeps out `processed/` (the 5.4 GB of windows), `.venv-tf/`, the 136 MB forest, large prediction files, compiled test programs.
- `tests/HOW_TO_RECORD.md`: **new**. How to record watch data for testing the activity models.
- Blocks 5 and 6: notes added (superseded by Block 19; Block 6's GOTOV loss may be the turned-sensor problem).
- Not changed: firmware. The new model's firmware code is in `export/` and Stage 7/8 notebooks, ready to add.

### 2026-11-24: `firmware/espwatch_prototype/`: the FinalPrototype on the Waveshare watch, over Wi-Fi
- `firmware/espwatch_prototype/espwatch_prototype.ino`: new sketch, built from `Melv-Codes/esp_watch` (QMI8658, models, AMOLED screen, touch, BOOT button).
  - Adds the FinalPrototype's voice protocol (`P` play → `K`, `L` record → `M` + PCM, `r` resume, `b` speaker test, `m` mic test), running on the watch's ES8311 codec. That codec setup follows Espressif's es8311 sequence, with pins from Waveshare's `08_ES8311` example.
  - The link is a TCP server on port 3333 (`espwatch.local`). Every text line goes to both USB and the laptop.
  - The Railway HTTP sender has been removed, because the laptop sends the alerts. Messages that contained `K`/`M` during a check-in were reworded so they can't be mistaken for audio replies.
  - Not yet flashed or tested on hardware.
  - Compile fix: `Serial.setTxTimeoutMs(0)` now only builds when USB CDC On Boot is Enabled. With it Disabled, `Serial` is a plain hardware serial port that doesn't have this function.
- `firmware/espwatch_prototype/{fall_features,fall_model,fall_model_trees,har_model,test_vectors}.h`: copied unchanged (identical to FinalPrototype).
- `firmware/espwatch_prototype/wifi_config.example.h` and `README.md`: new files.
- `basestation/voice/esp32_audio.py`: new `WifiLink` (a TCP socket with the pyserial calls it needs). `get_serial("tcp:<host>")` now connects over Wi-Fi. USB behaviour is unchanged.
- `basestation/voice/watch_bridge.py`: new `--host` option for the Wi-Fi watch, with Wi-Fi-specific messages and a Wi-Fi detector name.
- Tested against a simulated watch (lines, play 16000 samples, record 1 s, resume, disconnect). All 98 tests still pass.
- `.gitignore`: added `firmware/espwatch_prototype/wifi_config.h`.

### 2026-11-24: `espwatch/` now records over Wi-Fi; windows cut on the laptop
- `espwatch/espwatch.ino`: rewritten. A FreeRTOS task samples at 50 Hz into a 60 s buffer. `loop()` sends one CSV row per sample over TCP (port 3333, `espwatch.local`) when `wifi_config.h` exists, and over USB otherwise. If the laptop reconnects within 60 s, nothing is lost. Windowing has moved off the watch.
- `espwatch/capture_csv.py`: rewritten. Connects with `--host` (Wi-Fi, reconnects by itself) or `--port` (USB). Writes `<out>_raw.csv` plus `<out>_windows.csv`, using `--mode har` (128/64) or `--mode fall` (125/62). Window starts were checked to match the training `np.arange(0, n-win+1, hop)`, and a window is never built across a gap. Also tested against a fake watch with a gap and a disconnect.
- `espwatch/wifi_config.example.h`: new file, a template for the hotspot SSID and password.
- `.gitignore`: added `espwatch/wifi_config.h`.

### 2026-11-24: `espwatch/` IMU window recorder (CSV) for the Waveshare watch
- `espwatch/espwatch.ino`: new sketch. Reads the QMI8658 at 50 Hz with the same settings as the watch firmware (±16 g, ±2048 dps, ODR ~62.5 Hz, LPF on, gyro bias removed at boot). Prints overlapping windows as CSV, cut the same way as training: HAR mode 128 samples with a hop of 64 (default), fall mode 125 samples with a hop of 62. A gap restarts the window. Serial commands: `0`–`9` sets the label, `p` pauses, `h` reprints the header.
- `espwatch/capture_csv.py`: new script. Saves the serial CSV to a file (pyserial) and skips the `#` lines.
- `espwatch/README.md`: new file. Explains how to run it and what the columns mean.

### 2026-11-24: First real ESP32 → Wi-Fi → Cloud Run → Firestore message
- `stream_demo` flashed to the ESP32-C3 (COM7). It connected (signal −56 dBm) and its first "still" change (`01ae3595-0`) plus heartbeats reached Firestore.
- **Problem found:** the first Wi-Fi was a phone hotspot on **5 GHz + WPA3 only**, which the ESP32-C3 cannot use (2.4 GHz only). The board kept retrying with nothing sent. Fixed by switching the hotspot to 2.4 GHz.
- `streaming/firmware/*/silverguard_stream.h` (both copies):
  - while Wi-Fi is down, prints the reason every 10 s (network not found / wrong password / still trying);
  - "Wi-Fi up" line shows the signal strength;
  - transmit power set to 8.5 dBm (`STREAM_WIFI_TX_POWER`), the usual fix for ESP32-C3 SuperMini antennas.
- `watch_streaming/stream_config.h` created from the working demo config (git-ignored).
- Next: try `m` / `s` / `f` in the demo, then flash `watch_streaming`.

### 2026-11-21: Streaming deployed to Google Cloud and tested end-to-end
- Ran `streaming/server/deploy.ps1 -Project silverguard-3cde2`. Cloud Run service `silverguard-ingest` (asia-southeast1), Firestore database, device key in Secret Manager (`silverguard-device-keys`).
- Test: `tools/send_test_event.py` → Cloud Run returned `200` → both messages found in Firestore (`devices/watch-01`, 2 docs in `transitions/`) → shown by `tools/live_status.py` (OFFLINE after 2.5 min with no heartbeat, as designed).
- `streaming/README.md`: status updated to deployed.
- Note: leaving `live_status.py` running every 5 s all day costs about 52k reads, over the 50k/day free tier. Stop it with Ctrl+C, or use `--every 30`.

### 2026-11-21: Small firmware fixes committed
- `firmware/ESP32(Gyro+model)/silverguard_logger.ino`: pin comments corrected to GPIO 1 / GPIO 0 (the values were already right).
- `firmware/FinalPrototype/README` → `README.md`: renamed so GitHub shows it formatted.

### 2026-11-21: `streaming/server/deploy.ps1` fixed: stopped on a success message
- gcloud writes progress messages to the error stream. In Windows PowerShell 5.1, with `$ErrorActionPreference = "Stop"` and the `gcloud.ps1` wrapper, "Updated IAM policy for secret" (a success) stopped the script at step 4, before the deploy.
- The script now calls `gcloud.cmd` directly, doesn't stop on messages, and checks each call's exit code (`Step`). Also enables the Artifact Registry API (needed by `--source` deploys) and prints the URL plus how to read the key at the end.

### 2026-11-21: `streaming/` folder rebuilt and completed (Block 18)
- The first version (created 2026-11-20, never committed) was lost: two `git reset` runs after it was made left only `__pycache__` files. Rebuilt from the session, then extended.
- Every file now starts with a "what this file does" explanation (the copied model headers got a one-line note).
- **Rebuilt:** `README.md`, `.gitignore`, `firestore.rules`, `server/main.py`, `server/requirements.txt`, `server/Dockerfile`, `server/.dockerignore`, `firmware/stream_demo/` (`silverguard_stream.h`, `stream_config.example.h`, `stream_demo.ino`), `tools/send_test_event.py`, `tools/live_status.py`, `tests/test_ingest.py`.
- **New:**
  - `requirements.txt` (PC side).
  - `server/.env.example`, `server/deploy.ps1` (one-command Google Cloud setup + deploy).
  - `firmware/watch_streaming/` (copy of `firmware/FinalPrototype/` + 4 `STREAMING:` lines + model headers).
  - `bridge/usb_to_cloud.py` (USB stopgap with the same debounce and a retry queue).
  - `dashboard/firestore_source.py`, `dashboard/live_page.py`.
  - `tools/ingest_client.py` (shared Python sender).
  - `tests/test_bridge.py`, `tests/test_firestore_source.py`.
- 32 tests pass. Local server smoke test passes (200 stored, 401 wrong key, 0 server unreachable). `firmware/FinalPrototype/` itself is not changed. Both sketches compile for ESP32-C3 (Huge APP partition): `stream_demo` 1.15 MB, `watch_streaming` 1.30 MB (99% of the default partition, so Huge APP is required).

### 2026-11-21: Firmware folders renamed + FinalPrototype README
- `firmware/silverguard_logger/` → `firmware/ESP32(Gyro+model)/`: renamed (gyro + on-device models, no speaker). `silverguard_logger.ino`: I2C pins changed to SDA GPIO 1, SCL GPIO 0 (was 3 / 4).
- `firmware/FinalPrototype/`: **new**. Copy of `Melv-Codes/ESP32-SpeakerUSBVer/` (sketch, models, bridge scripts, wiring), the version to flash for the demo.
- `firmware/FinalPrototype/README`: **new**. How to run it: flash `ESP32-SpeakerUSBVer.ino`, check the pin connections, run `python -m basestation.voice.watch_bridge`, and set up `.venv` if libraries are missing.

### 2026-11-20: Speaker watch: calibration retries + saved in flash
- `Melv-Codes/ESP32-SpeakerUSBVer/ESP32-SpeakerUSBVer.ino`: after a replug, the watch was moved during its 2 s boot calibration. The calibration was rejected and it fell back to no correction (1.23 g at rest → "Moving"). Calibration now retries up to 5 times. A good result (accel scale + gyro bias) is saved with `Preferences` (namespace `sg_calib`). If every try fails, the saved one is used.

### 2026-11-20: Speaker watch: accel calibration + I2C read retry
- `Melv-Codes/ESP32-SpeakerUSBVer/ESP32-SpeakerUSBVer.ino`: this MPU-6050 reads ~1.23 g lying still. The still/moving model was trained on 1.00 g (its splits sit at 0.99–1.02 g average, 1.24–1.29 g peak), so it flipped to "Moving" untouched. At boot, the gyro calibration now also measures resting |a| and rescales the accelerometer so rest reads 1.00 g (`acc_scale`, printed as `# accel_rest_g`). A failed I2C read is now retried up to 2 times before the 2.56 s window is wiped, which cuts the random pauses.

### 2026-11-20: Decision: Railway + Supabase → Google Cloud Run + Firestore
- `context.md`: team agreed to replace Railway with **Google Cloud Run** (server) and Supabase with **Firestore / Firebase** (data). Updated architecture, §4 data contract (planned Firestore layout: subcollections, one activity document per day to stay under read quota), dashboard, secrets, roadmap, status, open decisions and session log. Marked as *migration in progress*: no code changed yet.
- `progress.md`: Block 9 status updated.

### 2026-11-20: Main program moved out of Melv-Codes
- `Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py`: put back exactly as the groupmate wrote it (commit `d5b8f19`). `Melv-Codes/` is his folder and is not edited by us.
- `basestation/voice/watch_bridge.py`: rewritten (96 → 152 lines) as the **main program**: watch's own speaker + mic, `--manual`, Telegram ON/OFF at startup, Telegram message if the watch disconnects.
- `basestation/voice/esp32_audio.py`: **new**, copy of Melv's USB audio protocol so our code doesn't import from his folder.

### 2026-11-18: Telegram alerts on Melv's watch speaker + mic bridge (main program)
- `Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py`: prints Telegram alerts ON/OFF at startup; if the watch is unplugged (`SerialException`) it sends the caregiver a Telegram message that falls are not being detected, instead of crashing silently.
- `basestation/voice/demo.py`: `handle_event()` catches a crashed check-in (e.g. watch mic error over USB) and still alerts (outcome `alert_checkin_failed`). `alert()` now sends Telegram **before** speaking the alert line.
- `tests/test_sync.py`: added `test_demo_still_alerts_when_watch_audio_fails`. 98 tests pass.
- `Melv-code/Speaker_Gyro_Vib_copy_20261113095344.ino`: deleted by the user (old file, superseded by `Melv-Codes/`).

### 2026-11-17: Full project review written into progress.md
- `progress.md`: rewritten with an overview table (Part A), a detailed section for each of the 17 blocks (Part B) and this change log (Part C).

### 2026-11-17: progress.md created
- `progress.md`: created as the running changelog.

### Uncommitted work found on 2026-11-17 (date of the change not recorded)
- `basestation/voice/watch_bridge.py`: **new**. Real watch over USB → laptop voice check-in (Block 8).
- `basestation/voice/demo.py`: `handle_event()` now takes `detector` and `source` arguments (default `"typed demo"` / `"typed in the demo"`), so the watch bridge can label events as coming from the watch. Pressing Enter at the `event [fall]>` prompt now simulates a fall.
- `requirements.txt`: added `pyserial` (used by `record.py` and `watch_bridge.py`).
- `docs/SilverGuard_Progress_Update.docx`: **new**. Team progress update (file dated 2026-11-10/11).

### 2026-11-13: Speaker + gyro + vibration sketch (commit `5d03301`, Siblemmm)
- `Melv-Codes/Speaker_Gyro_Vib_copy_20261113095344/Speaker_Gyro_Vib_copy_20261113095344.ino`: **new**, 392 lines (Block 3).

### 2026-11-11: context.md rewrite (commit `ea0bf93`)
- `context.md`: rewritten (+151 / −125 lines). The watch has its own speaker and mic, the server is Railway, detection runs on the watch (firmware v0.4), roadmap phases A–F.

### 2026-11-10: First full commit (commit `06cee31`, 82 files, +40,541 lines)
- Voice check-in pipeline: `basestation/voice/` (Block 7).
- Supabase schema and uploader with offline outbox: `basestation/sync/` (Block 9).
- Telegram alerts: `basestation/alerts/telegram.py` (Block 10).
- Caregiver dashboard: `basestation/dashboard/` (Block 11).
- Config and secrets templates: `config.yaml`, `.env.example`, `.streamlit/`, `.gitignore`, `requirements.txt` (Block 12).
- Tests: 97 passing (Block 13).
- Firmware v0.4 with fall + HAR models and self-test: `firmware/silverguard_logger/` (Block 1); early sketches: `firmware/tests/` (Block 2).
- All notebooks and helper modules for the fall and still/moving models (Blocks 4–6).
- Placeholders: `evaluation/`, `basestation/detection|ingest|rules/` (Blocks 14–15).

### Before the first commit (from `context.md` session log)
- **2026-11-06:** Dataset EDA of Capture-24 and GOTOV vs UCI-HAR. Neither has a gyroscope; GOTOV's real rate is 85.7 Hz.
- **2026-11-04 onward:** Fall work: dataset audit → FallAllD model → WEDA-FALL test → prototype tuning (stages 3 and 3b). See `notebooks/update_since_wed_4nov.ipynb`.
- **2026-10-24:** `context.md`, repo skeleton, `basestation/config.yaml`, `.gitignore` created.
