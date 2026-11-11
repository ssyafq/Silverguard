# SilverGuard — Project Context

> Read this file at the start of every session. It is the source of truth for what SilverGuard is, how it is built, and where it stands. If anything in a prompt conflicts with this file, ask before acting. Update the **Status** and **Session log** sections at the end of each session. The team-facing summary of the same picture is `docs/SilverGuard_Progress_Update.docx`.

---

## 1. What we are building

**SilverGuard** is a final-semester Major Project (MP) at Temasek Polytechnic (Biomedical Engineering diploma), running Oct 2026 – Feb 2027. It is a team project.

**Overall aim:** a wrist-worn safety watch for older adults. The watch (ESP32-C3 + MPU-6050, with its own **speaker and mic**) detects likely falls, **talks to the wearer** to check they are okay through a cloud voice assistant, and sends a *simulated* caregiver alert if they ask for help or don't reply.

**Why voice:** many elderly users may not read or write, so the check-in must be spoken, not text.

**This is a prototype, not a medical device.** It is not a certified fall detector, and the report must say so. The demo never contacts real emergency services. Alerts go to a teammate's phone (Telegram).

---

## 2. Team and ownership

| Objective | Scope | Owner |
|---|---|---|
| **Obj 1** · Watch hardware | ESP32-C3 + MPU-6050, I2S mic + speaker amp, power, housing, Wi-Fi link to the server | Groupmate(s) |
| **Obj 2** · Event detection + dashboard | Fall + still/moving models (on the watch), Supabase data, Streamlit caregiver dashboard, "can't see" / dropout handling | **Syafiq (me)** |
| **Obj 3** · Voice check-in + rules engine | Railway server: speech-to-text → Claude → text-to-speech, English/Mandarin, check-in dialogue, rules that decide alerts, Telegram | Groupmate(s) |
| **Obj 4** · Evaluation | Own recordings, detection metrics, false alarms/hour, fall → alert time, dialogue errors, usability, privacy check | **Syafiq (me)** |

Everyone helps each other, but **changes to Obj 1 or Obj 3 code should be coordinated with the owners first.** If I ask for work in their area, flag it.

---

## 3. Architecture

Four parts, plus a Telegram alert:

```
WATCH (Obj 1 + 2)              SERVER on Railway (Obj 3)               ONLINE (Obj 2)
─────────────────────          ─────────────────────────────           ─────────────────────
MPU-6050 @ 50 Hz               receive event + audio clip              Supabase (Postgres)
   │                              │                                     events, check-ins,
ESP32-C3 runs the models:        speech-to-text (cloud)                 status, activity
 fall (4 stages) + still/moving   │                                          ▲
   │ fall detected                Claude Haiku 5.5 → ok / help / none        │
   ▼                              │                                     Streamlit dashboard
speaker: "Are you okay?"         fixed rules decide alert ──────────►   (caregiver, private)
mic: records reply ── Wi-Fi ──►   │
   ◄── spoken answer ─────────   text-to-speech back to the watch
                                  │
                                  └── help / no reply ──► Telegram bot ──► caregiver's phone
```

- **The server is Railway** (we already have a subscription). "Base station" in folder names means this server. There is no laptop or home hub in the design.
- **The watch does the detection itself.** Firmware v0.4 runs the fall model and the still/moving model in C on the ESP32-C3, and needs no internet for that. The server only handles the voice part and the alerts.
- **Two kinds of model, not two LLMs:** the watch runs small classifiers (random forests). The voice side is a cascaded pipeline (STT → LLM → TTS). Don't call the classifier an LLM.
- **Escalation is rule-based, never LLM-decided.** Claude only sorts the reply. The rules decide whether to alert.
- Voice stays **cascaded** (not speech-to-speech) so transcripts can be logged, each stage tested on its own, and there's a fallback if one stage fails.
- **Offline backup:** with no Wi-Fi, the watch plays a pre-recorded "Are you okay?" and uses a button to answer. With no LLM, the server uses the keyword backup.
- The voice code was first tested **on a laptop** as an early check of the pipeline. Those parts (`basestation/voice/`, `sync/`, `alerts/`) get reused on the Railway server.

---

## 4. Interface contracts (agree early, change only with the whole team)

### Watch → server: fall event (Wi-Fi). **Draft, to agree with Obj 1 + Obj 3.**
```json
{
  "t": "2026-12-10T14:03:22+08:00",
  "device_id": "watch-01",
  "state": "fall",
  "confidence": 0.91,
  "detector": "fall_rf30_v0.4"
}
```
`state`: `still | moving | fall` (label set may be revised, see §5). The audio clip format (sample rate, encoding, how it's sent) is **not yet agreed**.

### Raw sensor log (for recordings over USB serial), one line per sample
```
t_ms,ax,ay,az,gx,gy,gz
10420,0.02,-0.98,0.11,1.4,-0.7,0.3
# accel in g, gyro in deg/s, 50 Hz
```

### Rules engine ↔ voice (server internal, logged for Obj 4)
```
→ { "action": "check_in", "reason": "fall", "attempt": 1 }
← { "reply": "none", "transcript": "", "latency_s": 30.0 }
# reply: ok | help | none
```
Implemented by `basestation/voice/checkin.py` (`run_check_in`).

### Server → Supabase
Tables in `basestation/sync/schema.sql`: `events`, `checkins`, `event_steps`, `status` (heartbeat), `activity_minutes`. The dashboard only reads these.

---

## 5. Detection design (Obj 2)

**On the watch, every half second.** Only if all four stages say yes is a fall raised:
1. **Spike?** Acceleration magnitude above 2.5 g.
2. **Model agrees?** 30-tree random forest says fall.
3. **Wrist turned?** More than 20° after the spike.
4. **Fell before?** The arm dropped or spun before the knock.

Stages 3–4 were added after testing on the prototype, where hitting the table and putting the watch down used to set it off.

**Fall model results** (held-out people, FallAllD wrist):

| Method | Recall | False alarms/h | Falls caught |
|---|---|---|---|
| Rule: spike > 2.5 g | 100% | ~313 | 73/73 |
| Rule: spike > 3 g | 93% | ~214 | 68/73 |
| Logistic regression | 99% | ~48 | 73/73 |
| Random forest | 97% | ~30 | 73/73 |
| Random forest, 30 trees (on watch) | 94% | ~86 | 73/73 |

- Gyroscope helps a little (~10–17% fewer false alarms).
- WEDA-FALL (different smartwatch): 322–340/350 falls caught, but false alarms 4–8× higher. That's **domain shift**, so testing on our own watch matters most.
- Stages 3–4 on WEDA-FALL: table hits 54→28/73, clapping 63→39/74, older adults' daily activities 49→26/157, real falls 342→338/350.

**Still / moving model:**
- UCI HAR, labels merged into still vs moving. On HAR70+ (age 70+): 93% vs a one-rule baseline at 86%.
- Wrist data (Capture-24): model, second model and one-rule baseline all score about **0.74 balanced accuracy**, a tie.
- Richer features (10 s windows): +6 points on Capture-24 but −1 on GOTOV. That fails the pre-set test, so **the simple threshold is kept for now**.
- Inactivity alert ("no movement for N minutes during waking hours") is a rule on top. Not built yet.

**Priority: high fall recall.** A missed fall is worse than a false alert, but false alarms per hour must still be measured and kept tolerable.

**Known hard problems:**
- No dataset has older adults really falling. All falls were staged by younger people onto mattresses.
- Hands move even when the person sits still, which makes still/moving hard on the wrist.
- Sleep vs inactivity both look like stillness. Time of day and posture will likely matter more than the model.
- Seizure detection is a stretch goal / future work.

---

## 6. Dashboard (Obj 2)

**Streamlit app on Streamlit Community Cloud** (private, password-protected), reading from **Supabase**. Built from `basestation/dashboard/Caregiver dashboard@1x.png`.

- Shows the alert banner, today's activity, event log, event detail (what the voice check-in did), and system health. Works on a phone.
- **Red "Can't see" state** when the heartbeat or watch data stops. A dead device must never look like "all fine".
- Acknowledge / resolve buttons. No "emergency" wording.
- Runs on **sample data** until the server writes real rows (sidebar toggle).
- Setup steps: `basestation/dashboard/README.md`.

---

## 7. Voice check-in (Obj 3, groupmates own this)

Flow after a fall: watch speaker asks *"Are you okay?"* (English, then Mandarin) → watch mic records → server: speech-to-text → Claude sorts into `ok / help / none` → one retry if nothing heard → `help` or `none` → Telegram alert (~45 s total, starting value).

- **LLM:** Claude Haiku 5.5 (`claude-haiku-5-5`, effort low, 8 s timeout, structured output). If unsure, it picks `help`. 41/41 test replies correct (English, Mandarin, Singlish, mixed). About US$0.20 per 1,000 check-ins.
- **Keyword backup** (`keywords.py`) when the LLM can't be reached.
- **Speech-to-text on the server: not yet chosen.** Needs a cloud service that handles English + Mandarin + mixed. faster-whisper was used in the laptop test.
- **Text-to-speech:** edge-tts voices (en-SG-LunaNeural, zh-CN-XiaoxiaoNeural) in the laptop test. Fixed lines can be pre-recorded on the watch.
- Mic only listens during a check-in, never always-on (privacy).
- The Claude API key lives only on the server (Railway variables / `.env`), **never** in the dashboard.

---

## 8. Evaluation (Obj 4)

Measure:
- Detection: confusion matrix, fall recall, false alarms per hour of normal activity, on **our own recordings**.
- End-to-end time (impact → question → alert).
- Dialogue errors: misheard replies, wrong decisions, failed English/Mandarin code-switching, on scripted prompts.
- Usability: short wear-and-respond sessions + a questionnaire such as the System Usability Scale.
- Privacy check: what is stored, where, how long. Audio goes to a cloud speech service, so cover that. Write the "not a certified fall detector" statement.

Rules:
- **Split data by person/session, never by random window.** Random-window splits leak and inflate results.
- Hold out sessions for testing only. Never tune on them.
- All falls are **simulated by healthy teammates onto a mattress**. State this as a limitation.
- Ask the supervisor early whether trials with older adults need consent forms or ethics approval.

---

## 9. Roadmap (order matters more than exact dates)

| Phase | What | Who | Done when |
|---|---|---|---|
| A. Own data | Record with our watch: staged falls onto a mattress, table hits, put-downs, walking, sitting, lying | Obj 2 + all | A labelled set to test both models on |
| B. Watch audio + Wi-Fi | I2S mic + speaker amp; send events and audio over Wi-Fi | Obj 1 | The watch plays a sound and sends a recording to a test server |
| C. Railway server | Watch audio → STT → Claude → TTS → back to watch; reuse voice brain, backup, alert code | Obj 3 + Obj 2 | A fake watch (file or phone) gets a spoken answer back |
| D. Real data on dashboard | Server writes events, check-ins, heartbeat, activity to Supabase | Obj 2 | A staged fall shows on the dashboard within seconds |
| E. Offline backup | No Wi-Fi: pre-recorded prompt on the watch + answer button | Obj 1 + Obj 3 | Check-in still works with Wi-Fi off |
| F. Evaluation | Recall, false alarms/h, fall → alert time, misheard replies, usability, privacy | Obj 4 | Numbers for every objective + repeatable demo |

Final deadline: report, poster and video by ~7 Feb 2027 (presentation the week of 1 Feb, assumed).

**Nobody waits for hardware:** detection works from recordings, the server works with a fake watch.

---

## 10. Tech stack and conventions

- **Watch:** Arduino/C++ on ESP32-C3. Models exported to C (`firmware/silverguard_logger/`), boot self-test against Python outputs.
- **Server:** Python on Railway. **Dashboard:** Streamlit + Plotly. **Data:** Supabase. **Alerts:** Telegram Bot API.
- Notebooks are for exploration only. **Reusable code goes in `.py` modules.**
- Gyro and accel units are not interconvertible. Keep them as separate channels (g and deg/s).
- Thresholds, window sizes and timings are **starting values** in `basestation/config.yaml`, not hard-coded.
- Secrets only in `.env` (git-ignored) / Railway variables / Streamlit secrets. Never in `.env.example` or git.
- Python 3.11 venv; `requirements.txt` at the root. Run tests with `pytest`.

**Repo layout (current):**
```
├── context.md
├── docs/                 # team-facing documents (progress update)
├── firmware/             # watch code + models in C
├── basestation/          # = the server side (name kept from early on)
│   ├── voice/            # STT → Claude → TTS, keyword backup, check-in
│   ├── sync/             # Supabase schema + uploader
│   ├── alerts/           # Telegram
│   ├── dashboard/        # Streamlit app + setup README
│   ├── detection/ ingest/ rules/   # placeholders
│   └── config.yaml
├── evaluation/           # Obj 4 metrics (to fill)
├── data/raw/, data/labels/   # recordings (personal data not committed)
├── notebooks/            # model exploration
└── tests/
```

---

## 11. How to work with me (for Claude Code)

- I'm a BME diploma student learning ML and systems as I go. **Explain the "why"** behind design choices, briefly, in short paragraphs.
- **Give honest, direct feedback.** If my idea is wrong or a plan has a flaw, say so plainly and explain why.
- Before large changes, state what you're about to do and which files you'll touch.
- Don't silently change the interface contracts in §4. They affect teammates.
- If a request falls in Obj 1 or Obj 3, remind me to check with the owner.
- When something here is out of date, tell me and propose the edit.

---

## 12. Status (update every session)

**As of 11 Nov 2026:**

| Piece | Status | Notes |
|---|---|---|
| Fall detector on the watch | Done | Works on the prototype; tuned for table hits and put-downs |
| Still / moving on the watch | Working | Runs, but not yet better than a simple rule |
| Own watch recordings | Not started | Needed to test and improve both models |
| Voice brain (Claude) | Done | 41/41 test replies; keyword backup |
| Speak / listen loop | Working | Proven on a laptop; moving to server + watch |
| Speaker + mic on the watch | Not started | Obj 1 |
| Railway server | Not started | Reuses voice, backup, sync and alert code |
| Supabase | Working | Tables designed; project to create |
| Telegram alerts | Working | Code ready; bot to create |
| Caregiver dashboard | Done | Live on sample data; real data once the server sends it |

**Next two weeks:** record own data; agree the watch ↔ server message format; pick a cloud STT; order/wire the I2S mic + amp; create Supabase project + Telegram bot; start the Railway server with a fake watch; ask the supervisor about ethics approval.

**Open decisions:**
- [x] LLM: Claude Haiku 5.5 (keyword backup offline)
- [x] Dashboard: Streamlit Community Cloud + Supabase + Telegram (2026-11-10)
- [x] Server: Railway (2026-11-11)
- [ ] Cloud speech-to-text service (English + Mandarin + mixed)
- [ ] Watch ↔ server message + audio format and transport (HTTP / WebSocket / MQTT)
- [ ] Final label set (still/moving/fall; inactivity rule)
- [ ] Whether trials need consent/ethics approval

---

## 13. Session log

Add one or two lines per session: date, what was done, what's next.

- **2026-10-24** — Created this context file and the repo skeleton, `basestation/config.yaml`, `.gitignore`.
- **2026-11-06** — Dataset EDA of Capture-24 and GOTOV vs UCI-HAR (`notebooks/Developing stronger movement classifier/`). Neither has a gyroscope; GOTOV's real rate is 85.7 Hz and is kept as a fair test.
- **2026-11-10** — Voice check-in pipeline built and tested on a laptop (`basestation/voice/`: faster-whisper, Claude Haiku 5.5 + keyword backup, edge-tts + pyttsx3). Caregiver dashboard built from the PNG (Streamlit), Supabase uploader with offline outbox, Telegram alerts. 97 tests pass (LLM 41/41). Pushed to the private GitHub repo.
- **2026-11-11** — Wrote `docs/SilverGuard_Progress_Update.docx` for the team. Rewrote this file to match: watch with its own speaker + mic, Railway server, detection on the watch (firmware v0.4), roadmap phases A–F. Next: own recordings, agree the watch ↔ server format, choose cloud STT, start the Railway server.
