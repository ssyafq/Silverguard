# SilverGuard — Project Context

> Read this file at the start of every session. It is the source of truth for what SilverGuard is, how it is built, and where it stands. If anything in a prompt conflicts with this file, ask before acting. Update the **Status** and **Session log** sections at the end of each session.

---

## 1. What we are building

**SilverGuard** is a final-semester Major Project (MP) at Temasek Polytechnic (Biomedical Engineering diploma), running Oct 2026 – Feb 2027. It is a team project.

**Overall aim:** a single-sensor gerontechnology prototype that detects abnormal mobility events in older adults using a wrist-worn ESP32 + MPU-6050, then checks in with them through a voice assistant (hosted LLM), and sends a *simulated* caregiver alert if they need help or don't reply.

**Why voice:** many elderly users may not read or write, so the check-in must be spoken, not text.

**This is a prototype, not a medical device.** It is not a certified fall detector, and the report must say so. The demo never contacts real emergency services — alerts go to a teammate's phone (Telegram/SMS).

---

## 2. Team and ownership

| Objective | Scope | Owner |
|---|---|---|
| **Obj 1** · Sensor unit | ESP32 + MPU-6050 wearable, filtering, calibration, power, housing, USB → Wi-Fi link | Groupmate(s) |
| **Obj 2** · Event detection + dashboard | Detection algorithm (activities, transitions, staged high-impact events), Python dashboard with timestamps, quality flags, reconnection and error handling | **Syafiq (me)** |
| **Obj 3** · Voice check-in + rules engine | Hosted LLM + speech interface (English/Mandarin code-switching), check-in dialogue, rules engine that controls simulated alerts | Groupmate(s) |
| **Obj 4** · Evaluation | Controlled simulations, labelled test sets, detection metrics, false alerts, latency, dialogue errors, usability trials, privacy check | **Syafiq (me)** |

Everyone helps each other, but **changes to Obj 1 or Obj 3 code should be coordinated with the owners first.** If I ask for work in their area, flag it.

---

## 3. Architecture

Three places the system lives:

```
ON THE WRIST (Obj 1)          BASE STATION in the home (Obj 2 + 3)                  OUTSIDE
─────────────────────         ───────────────────────────────────────────────       ────────────────
MPU-6050 (accel+gyro)         Ingest + log  →  Windows  →  Detector                 Hosted LLM API
      │ I²C                   (CSV/session)    (2.5 s,     (state + fall rule)      (needs internet)
ESP32 (samples @ 50 Hz)                         50% overlap)      │
      │                                                           ▼
USB serial → later Wi-Fi ──►  ═════════ Event stream  { state, confidence, t } ═════
                                     │                          │
                                 Dashboard                 Rules engine ──► Voice check-in ◄──► Speaker + mic
                              (live plot + events)        (when to check in)  (STT → LLM → TTS)
                                     ▲                          │
                                     │ logs                     └── no reply / help ──►  Caregiver alert
                              Evaluation (Obj 4)                                         (Telegram/SMS, demo)
                         replays logged sessions
```

- **Base station** = a laptop for the demo (Raspberry Pi only if time allows).
- **ESP32 does no thinking in the MVP** — it just streams. Porting the detector onto the ESP32 is a later step.
- **Two separate models, not two LLMs:** the sensor side is a small activity classifier / event detector. The voice side is a cascaded pipeline (STT → LLM → TTS). Don't call the classifier an LLM.
- **Escalation is rule-based, never LLM-decided.** The LLM only interprets the reply; the rules engine decides whether to alert.
- Voice pipeline stays **cascaded** (not speech-to-speech) so transcripts can be logged, each stage tested on its own, and there's a fallback if one stage fails.

---

## 4. Interface contracts (agree early, change only with the whole team)

### Sensor → base station (Obj 1 → Obj 2), one line per sample
```
t_ms,ax,ay,az,gx,gy,gz
10420,0.02,-0.98,0.11,1.4,-0.7,0.3
# accel in g, gyro in deg/s, 50 Hz
```

### Detector → event stream (Obj 2 → Obj 3), JSON on every state change
```json
{
  "t": "2026-12-10T14:03:22+08:00",
  "state": "fall",
  "confidence": 0.91,
  "detector": "threshold_v1"
}
```
`state`: `resting | moving | sleeping | inactive | fall` (exact label set may be revised — see §5)

### Rules engine ↔ voice (Obj 3 internal, logged for Obj 4)
```
→ { "action": "check_in", "reason": "fall", "attempt": 1 }
← { "reply": "none", "transcript": "", "latency_s": 30.0 }
# reply: ok | help | none
```

These are drafts from 23 Oct 2026. If the team has changed them, update this section.

---

## 5. Detection design (Obj 2)

**Two layers + a rule:**

| Layer | Labels | Window |
|---|---|---|
| Activity state (adapted from the Walmsley label set in OxWearables `biobankAccelerometerAnalysis`) | sleeping · resting · light activity · active | ~30 s epochs |
| Event detector (our own) | fall · seizure (**stretch goal**) | ~1–10 s |
| Rule on top | inactivity alert (e.g. sedentary > N min during waking hours) | minutes |

- Merge moderate + vigorous into one "active" class (elderly rarely vigorous).
- **Baseline first:** ENMO cut-points (ENMO = |acc| − 1 g): sedentary < 45 mg, light 45–100 mg, moderate 100–400 mg, vigorous > 400 mg. These were set on research-grade calibrated devices — **calibrate the MPU-6050 first** and treat them as starting values.
- **Then** a small ML classifier, compared against the baseline on the same held-out data.
- Borrow two clean-up steps: HMM/Viterbi smoothing, and spurious-sleep removal (sleep runs < 60 min → sedentary). The latter directly helps sleep vs inactivity.
- The OxWearables repo uses **no gyroscope**. Our gyro will mostly help fall detection (sudden rotation), not activity classes.

**Fall logic (starting values, tune on real data):**
1. Acceleration magnitude spike past ~2.5–3 g → mark *possible fall* (don't raise yet).
2. Check for stillness over the next ~1–10 s. Still/lying → publish `fall` with confidence. Gets up normally → discard. This step is what keeps false alarms down.

**Priority: high fall recall.** A missed fall is worse than a false alert, but false alerts per hour must still be measured and kept tolerable.

**Known hard problems:**
- Sleep vs inactivity both look like stillness — time of day and lying posture will likely matter more than the model choice.
- Seizure: one wrist IMU, no wrist seizure data → stretch goal / future work.
- Wrist placement adds incidental hand motion; placement changes the signal itself, so it affects **both** false positives and false negatives. Wrist vs waist can be tested empirically with paired recordings if time allows.

---

## 6. Dashboard (Obj 2)

A **Python program on the laptop**, not a phone app or polished product.

Must:
- Receive ESP32 data (USB serial first, then Wi-Fi) and plot accel + gyro live.
- Show the current detected state and every flagged event, plus what the voice loop did about it (check-in result, alert sent).
- **Timestamp** every reading and **quality-flag** it (e.g. out-of-range value, missing data).
- **Recover from dropouts** (auto-reconnect) and show a clear "can't see the user" warning when data stops. A dead sensor must never look like "everything is fine."
- Log every session to CSV — these logs are evidence for Obj 4.

Planned screens (from the wireframe prompt): overview, event detail, event history, live sensor view, settings. Desktop-first. Framework assumed to be **Streamlit** — not yet confirmed.

---

## 7. Voice check-in (Obj 3 — groupmates own this)

Flow after a detector event: rules engine triggers → speaker asks e.g. *"Are you okay? Say yes if you are fine."* → listen → STT → LLM sorts the reply into `ok / help / none` → one retry if nothing heard → `help` or `none` → caregiver alert (~45 s total, starting value).

- English/Mandarin code-switching is in the brief.
- Mic only listens during a check-in, never always-on (privacy).
- Needs an **offline fallback**: scripted question + keyword check if the hosted LLM is unreachable.
- STT / LLM / TTS services: **not yet chosen.**

---

## 8. Evaluation (Obj 4)

Measure:
- Detection: confusion matrix, fall recall, false alerts per hour of normal activity.
- End-to-end response time (impact → question → alert).
- Dialogue errors: misheard replies, wrong decisions, failed English/Mandarin code-switching, on a set of scripted prompts.
- Usability: short wear-and-respond sessions + a questionnaire such as the System Usability Scale.
- Privacy check: what is stored, where, how long; sensor and audio data not exposed. Write the "not a certified fall detector" statement.

Rules:
- **Split data by subject/session, never by random window** — random-window splits leak and inflate results.
- Hold out sessions for testing only; never tune on them.
- All falls are **simulated by healthy teammates onto a mattress**. State this as a limitation.
- Ask the supervisor early whether trials with older adults need consent forms or ethics approval.

---

## 9. Timeline (rough — order matters more than exact weeks)

| Weeks | Dates | Phase | Done when |
|---|---|---|---|
| 1–2 | 19 Oct – 1 Nov | Agree the plan | Interfaces agreed, parts ordered |
| 3–4 | 2 – 15 Nov | Bring-up | Shaking the sensor moves the dashboard plot; typing "fall" makes the speaker ask "Are you okay?" |
| 5–8 | 16 Nov – 13 Dec | Data + detectors | Detector labels unseen recordings and catches most simulated falls |
| 9–11 | 14 Dec – 3 Jan | Integration | Real simulated fall → voice check-in → phone alert, no human in the loop |
| 12–14 | 4 – 24 Jan | Evaluate + harden | Numbers for every objective, repeatable demo script |
| 15–16 | 25 Jan – 7 Feb | Write up + present | Report, poster, video in; final presentation (week of 1 Feb, assumed) |

**Nobody waits for hardware:** Obj 2 works from recordings, Obj 3 works from fake events.

---

## 10. Tech stack and conventions

- **Base station:** Python. Notebooks (Jupyter/Colab) for exploration only; **reusable code goes in `.py` modules.** The real-time sensor pipeline is never a notebook.
- **ESP32 firmware:** Arduino/C++ likely (more MPU-6050 libraries) — Obj 1's call.
- **Gyro and accel units are not interconvertible** — keep them as separate channels (g and deg/s).
- Thresholds, window sizes and timings in this file are **starting values**, not specs. Put them in a config file, not hard-coded.
- Prefer simple, testable components. Every stage should be runnable on its own with fake or recorded input.

**Proposed repo layout (not yet agreed with the team):**
```
silverguard/
├── context.md
├── firmware/            # Obj 1 — ESP32 code
├── basestation/
│   ├── ingest/          # serial/Wi-Fi reader, CSV logger, quality flags
│   ├── detection/       # windows, features, threshold baseline, ML model, fall rule
│   ├── dashboard/       # Obj 2 UI
│   ├── rules/           # Obj 3 rules engine
│   ├── voice/           # Obj 3 STT → LLM → TTS
│   └── config.yaml      # thresholds, window sizes, timings
├── evaluation/          # Obj 4 — replay logs, metrics, plots
├── data/
│   ├── raw/             # session CSVs (don't commit personal data)
│   └── labels/
├── notebooks/           # exploration only
└── tests/
```

---

## 11. How to work with me (for Claude Code)

- I'm a BME diploma student learning ML and systems as I go. **Explain the "why"** behind design choices, briefly — I want to understand the system, not just get code.
- **Give honest, direct feedback.** If my idea is wrong or a plan has a flaw, say so plainly and explain why. Don't just agree.
- Before large changes, state what you're about to do and which files you'll touch.
- Don't silently change the interface contracts in §4 — they affect teammates.
- If a request falls in Obj 1 or Obj 3, remind me to check with the owner.
- When something here is out of date, tell me and propose the edit.

---

## 12. Status (update every session)

**As of 24 Oct 2026 (Week 1):**
- MP started 21 Oct 2026.
- Parts (ESP32, MPU-6050) not in hand yet — expected around 30 Oct. Spare set recommended.
- Dropped the plan to prototype on the public HAR70+ dataset first; going straight to own recordings once parts arrive.
- **Current focus:** starting on the voice LLM check-in first, since it needs no hardware (coordinate with Obj 3 owners).
- Architecture/workflow page, Obj 2 + 4 Gantt chart and dashboard wireframe prompt already made.

**Open decisions:**
- [ ] STT, LLM and TTS services for the voice loop — *prototype uses faster-whisper (local) / Claude Haiku 5.5 / edge-tts + pyttsx3 backup; needs Obj 3 team agreement*
- [x] Dashboard framework: Streamlit, hosted on Streamlit Community Cloud, data in Supabase, alerts via Telegram bot (2026-11-10)
- [ ] Final activity/event label set
- [ ] Repo layout and who owns which folders
- [ ] Whether trials need consent/ethics approval
- [ ] Wi-Fi transport for the sensor link (e.g. UDP/WebSocket/MQTT)

---

## 13. Session log

Add one or two lines per session: date, what was done, what's next.

- **2026-10-24** — Created this context file. Next: set up repo, start voice check-in prototype with fake events.
- **2026-10-24** — Created repo skeleton per §10 layout, `basestation/config.yaml` with starting values, `.gitignore` (excludes `data/raw/`). Next: `git init` in project folder, agree layout with team.
- **2026-11-06** — Dataset EDA of Capture-24 (all 151) and GOTOV vs UCI-HAR: `notebooks/Developing stronger movement classifier/dataset_eda_capture24_gotov_vs_uci.ipynb`, with shared code in `eda_features.py` and a cache in `data/processed/eda/`. Neither new dataset has a gyroscope; GOTOV's real rate is 85.7 Hz. GOTOV is kept out of feature ranking so it stays a fair test. Next: feature-combination experiment on Capture-24 non-test people.
- **2026-11-10** — Built voice check-in prototype (Plan A "hybrid") in `basestation/voice/`: `speak.py` (edge-tts, cached, pyttsx3 backup), `listen.py` (faster-whisper `small` on CPU, ~3.7 s per reply), `understand.py` (Claude Haiku 5.5, structured ok/help/none, keyword backup in `keywords.py`), `checkin.py` (§4 contract + CSV log to `data/raw/voice_checkins.csv`), `demo.py` (type "fall"; retry + simulated alert). Settings in `config.yaml` → `voice:`. `.venv` uses Python 3.11; `requirements.txt` added. 40 offline tests pass; LLM tests need a key in `.env`. Next: add API key, run demo with real voices, agree choices with Obj 3 owners.
- **2026-11-10** — Caregiver dashboard built from the PNG in `basestation/dashboard/` (Streamlit; `app.py`, `metrics.py`, `data.py`, `sample_data.py`, `style.css`), with design fixes: rules (not LLM) decide alerts, no "emergency" wording, red "Can't see" state, battery "—" until Obj 1 sends it. Laptop → Supabase uploader with offline outbox (`basestation/sync/`), Telegram alerts (`basestation/alerts/telegram.py`), demo wired to both. `git init` done (not committed); datasets/secrets git-ignored. 97 tests pass (LLM: 41/41). Next: create Supabase project + Telegram bot, deploy (see `basestation/dashboard/README.md`). Note: §3 says the ESP32 does no thinking, but firmware v0.4 runs the fall model on the watch, so §3 needs updating.