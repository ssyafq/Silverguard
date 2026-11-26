# How to record watch data to test the activity model

This guide explains how to record movement data with the SilverGuard watch, so we can check how well the
activity models work **on our own watch**. Follow it step by step. The way you record matters as much as the model:
a badly labelled or badly worn recording gives meaningless scores.

The analysis script is `notebooks/still-moving-DL/test_watch_recording.py`. Background:
`notebooks/still-moving-DL/explaination-orientation.md` (why the watch's axes matter) and
`notebooks/still-moving-DL/OLD_VS_NEW_MODEL.md` (what the two models are).

---

## 1. What you need

| Item | Notes |
|---|---|
| The watch | **ESP32-C3 + MPU-6050** (firmware `firmware/FinalPrototype/`), or the **Waveshare ESP32-S3** watch (`espwatch/`). Use the same physical watch for a whole session |
| Laptop + USB cable | For the ESP32-C3 watch. The Waveshare watch can also record over Wi-Fi |
| Python with `pyserial` and `numpy` | To run the recorder (`pip install pyserial numpy`) |
| A table and a book or box | For the calibration positions |
| A clock or phone timer | For timing each activity |
| The log sheet (section 8) | One row per recording |

---

## 2. One-time setup

### ESP32-C3 + MPU-6050 watch (`firmware/FinalPrototype/`)

1. **Switch the firmware to recording mode.** Open `firmware/FinalPrototype/ESP32-SpeakerUSBVer.ino` and change
   ```c
   #define RAW_STREAM   0
   ```
   to
   ```c
   #define RAW_STREAM   1
   ```
   With `0` the watch prints readable "Activity:" lines for the live demo, and **`record.py` receives no data**.
   Set it back to `0` when you go back to demos.
2. **Flash it** with the Arduino IDE (board: ESP32-C3), as described in `firmware/FinalPrototype/WIRING.md`.
3. **Find the port.** Arduino IDE → Tools → Port (for example `COM5`).
4. **Test run (10 s):**
   ```
   cd firmware/FinalPrototype
   python record.py COM5 test --seconds 10
   ```
   It should print `effective rate: 49.9–50.1 Hz` and `gaps in sample_index: 0` (or very few).
   Files are saved in `firmware/FinalPrototype/recordings/`. Delete the test file afterwards.

**Every time the watch powers on, it calibrates for 2 seconds: leave it lying still on the table until the
start-up messages finish.** If it moves, it prints a warning and uses the last saved calibration.

### Waveshare watch (`espwatch/`)

```
cd espwatch
python capture_csv.py --host espwatch.local --out walk_brisk_01 --seconds 300     # Wi-Fi
python capture_csv.py --port COM5 --out walk_brisk_01 --seconds 300              # USB
```
This saves `walk_brisk_01_raw.csv` (use this one) and `walk_brisk_01_windows.csv` (ignore it for this test).
**The name you give `--out` is the label**, so follow the naming rule in section 4.

> The Waveshare watch has a different sensor (QMI8658). It needs its **own** calibration and axis setup: never reuse
> the ESP32-C3 watch's setup for it, or the other way round.

---

## 3. How to wear the watch

The model is sensitive to how the sensor sits on the arm (see `explaination-orientation.md`). So:

- **Wrist:** your **dominant** wrist (the hand you write with), like most of the training data. Use **the same wrist
  every time**, and write it on the log sheet. Changing wrists changes the axis setup
- **Position:** like a normal watch: face on the back of the wrist, just above the wrist bone
- **The same way round every time:** strap buckle on the same side, the same end of the watch towards your hand.
  If the watch has a mark or a USB port, note which way it points (for example "USB towards the fingers")
- **Snug, not tight:** it shouldn't slide or spin around the wrist. A loose watch adds fake movement
- **Same watch unit for a whole session.** Two watches of the same design can still differ in calibration

---

## 4. Naming each recording

**The file name is the label.** The script reads the activity from the trial name you give `record.py` (or `--out`
for the Waveshare). Use one of these words, plus a number:

| Say in the name | What you do | What the model should call it |
|---|---|---|
| `sitting_desk` | sit at a desk: typing, phone, writing | sedentary (still) |
| `sitting_sofa` | sit on a sofa: TV, reading | sedentary (still) |
| `lying` | lie down awake: on your back, then your side | sedentary (still); may show as sleep |
| `standing` | stand mostly in one place: talking, waiting | **light** by the training data's rule, still by the plain rule (both are scored) |
| `chores` | household jobs: dishes, wiping tables, tidying | light (moving) |
| `cooking` | preparing food | light (moving) |
| `walk_slow` | a slow, relaxed walk | light (moving) |
| `walk_brisk` | walking as if you're a bit late | moderate-vigorous (moving) |
| `stairs` | up and down stairs, continuously | moderate-vigorous (moving) |
| `cycling` | cycling | moderate-vigorous (moving) |
| `sleep` | actually sleeping (only for overnight recordings) | sleep (still) |

Examples: `sitting_desk_01`, `walk_brisk_02`, `P03_chores_01`.

- **Never use a person's real name** in a file name. Use codes: `P01`, `P02`, ...
- `walk_slow` must be written exactly like that (otherwise "walk" alone is read as brisk walking)
- A name the script doesn't recognise is kept but **not scored**

---

## 5. The recording session (about 1 hour)

### Step 0: power on (30 s)

Plug in or reset the watch **lying still on the table**, and wait until the start-up messages finish.

### Step 1: calibration (2 minutes). Do this every session, first

This measures the sensor's errors (our watch reads 1.23 g lying still instead of 1.00 g) and which axis goes
through the face.

```
python record.py COM5 calibration --seconds 120
```

Then put the watch **still** in each of these 6 positions for **10 seconds each**, counting slowly. Prop it
against a book so it doesn't wobble, and keep your hands off it while counting:

| # | Position | How |
|---|---|---|
| 1 | **Face up** | lying flat on the table, screen up. **Always first**: the script uses it to find the face axis |
| 2 | Face down | flat, screen down |
| 3 | Standing on its top edge | the 12 o'clock edge on the table |
| 4 | Standing on its bottom edge | the 6 o'clock edge on the table |
| 5 | Standing on its left edge | the 9 o'clock edge on the table |
| 6 | Standing on its right edge | the 3 o'clock edge on the table |

Move it gently between positions. If the 120 s runs out before you finish, record again.

### Step 2: the activities (about 45 minutes)

One recording per activity, **3–5 minutes each**. Suggested order:

| # | Command | Duration | Tips |
|---|---|---|---|
| 1 | `python record.py COM5 sitting_desk_01 --seconds 300` | 5 min | type, scroll the phone, write: normal desk work |
| 2 | `python record.py COM5 sitting_sofa_01 --seconds 300` | 5 min | sit back, watch something, hands resting |
| 3 | `python record.py COM5 standing_01 --seconds 180` | 3 min | stand and talk, small hand gestures are fine |
| 4 | `python record.py COM5 chores_01 --seconds 300` | 5 min | wash dishes, wipe the table, tidy shelves |
| 5 | `python record.py COM5 walk_slow_01 --seconds 300` | 5 min | relaxed pace, arms natural |
| 6 | `python record.py COM5 walk_brisk_01 --seconds 300` | 5 min | "late for a bus" pace, arms swinging |
| 7 | `python record.py COM5 lying_01 --seconds 300` | 5 min | awake; half on your back, half on your side |
| 8 | (optional) `stairs_01`, `cycling_01` | 2–5 min | only if you can keep going without stopping |

**Rules while recording**
- **Start the activity, then start the recording** (or start both within a couple of seconds). The first and last
  few seconds of a file are the least clean
- **Do only that activity for the whole file.** No checking the laptop in the middle of `walk_brisk`. If you have to
  stop, **delete the file and record it again**
- **Each activity must last at least 30 s.** The model judges 30-second blocks, so anything shorter is never
  scored. 3–5 minutes gives 6–10 blocks, enough to see a pattern
- **Behave naturally.** Don't hold your arm stiff because you're being recorded
- **Watch the output** at the end: rate about 50 Hz, few or no gaps. If there are many gaps, record again
- **On the ESP32-C3 watch, the USB cable limits walking.** Walk in a loop near the laptop with a long cable, or carry
  the laptop. The Waveshare watch over Wi-Fi is easier for walking

### Step 3: long normal wear (for the axis setup). Separate days

To work out the watch's axis setup **without labels** (the reliable way), the script needs **3+ hours of ordinary
daytime wear from each of 3–5 people**, all with the same watch design and on the same wrist.

- **No script:** just wear it during a normal day (work, meals, walking around)
- Name the files e.g. `P01_daytime_01`. They don't need activity labels (they won't be scored)
- **USB on the ESP32-C3 watch:** a multi-hour recording ties the person to the laptop, which isn't a normal day. Use
  the Waveshare watch over Wi-Fi if possible, or the Wi-Fi recorder once it works on the ESP32-C3
- Take the watch off for showers. Pauses are fine: the script skips gaps

Until you have this, use the quick, **optimistic** option (`--orientation labels`, section 7) and confirm it on a
second session.

---

## 6. After recording: check the files

For each file `record.py` prints:
```
effective rate:        50.00 Hz        <- should be 49.5-50.5
gaps in sample_index:  0               <- a few are OK; many = record again
max |a|:               2.31 g
```
Then:
1. Fill in the log sheet (section 8)
2. Copy the session into its own folder, e.g. `recordings/2026-11-28_P01/`, including the `calibration_*.csv` file.
   `*_events.csv` files (the watch's own model answers) can stay next to their recordings: the script uses them as
   a cross-check

---

## 7. Run the test

From `notebooks/still-moving-DL/`, using its Python environment:

```
cd notebooks/still-moving-DL
.venv-tf\Scripts\python.exe test_watch_recording.py recordings\2026-11-28_P01\*.csv --calibration recordings\2026-11-28_P01\calibration_20261128_100000.csv --orientation labels
```
(One line. Put the session folder wherever you keep it and adjust the paths.)

| Option | When |
|---|---|
| `--orientation labels` | **First session.** Picks the axis setup that scores best on these labels. Quick, but optimistic |
| `--orientation search` (default) | Once you have the hours of normal wear (step 3). No labels needed. The reliable way |
| `--setup watch_test_results\watch_setup.json` | Every later session: reuse a saved setup, so the score is honest |
| `--orientation none` | To see what happens with no axis correction |
| `--model tested` | Use the 121-person tested twin instead of the final model |
| `--no-old` | Skip the old model |

It prints, and saves in `watch_test_results/`:
- the calibration it found and the axis setup, including **C code to paste into the firmware**
- per recording: what the new model and the old model said
- **scores** for the new model (4 classes) and both models (still vs moving, two ways: standing = moving, and
  standing = still)
- `windows.csv`: every 30 s block with both models' answers

**The honest way to test:**
1. Session 1: `--orientation labels` → saves `watch_setup.json`
2. Session 2 (new recordings, same watch and wrist): `--setup watch_test_results/watch_setup.json`. **Session 2's
   scores are the ones to report**, because session 1 chose the setup with its own labels

**When reading the results, remember:**
- The new model counts **standing as light activity** (moving), as its training data does. Both ways are scored
- **Lying awake** can show as sleep: on the wrist they look the same
- **Short bursts** (a 20-second walk) are smoothed away
- The new model answers every 30 s; the old model every 0.5 s (the script combines its answers per 30 s)

---

## 8. Log sheet

Copy this table for each session:

| Date | Person code | Wrist (L/R) | Watch unit | Which way round | File | Activity | Start time | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-11-28 | P01 | R | C3 #1 | USB to fingers | calibration_... | calibration | 10:02 | |
| 2026-11-28 | P01 | R | C3 #1 | USB to fingers | sitting_desk_01_... | sitting_desk | 10:05 | typed emails |
| | | | | | | | | |

---

## 9. Problems and fixes

| Problem | Cause and fix |
|---|---|
| `waiting for data...` forever | `RAW_STREAM` is 0: set it to 1 and re-flash. Or the wrong COM port |
| Rate far from 50 Hz | Laptop USB power saving or a loose cable: try another port, keep the laptop plugged in |
| Many gaps | I2C read errors: check the sensor wiring; record again |
| Script: "not enough different still positions" | The calibration file was too short, or the watch moved. Record `calibration` again, 10 s still in each of the 6 positions |
| Script: "face axis unknown" | The first 10 s of the calibration file weren't face up and still. Record it again, face up first |
| Script: "NO LABEL (not scored)" | The file name has none of the activity words in section 4 |
| Script: "no full 30 s windows" | The recording is under 30 s, or has a gap in every 30 s stretch |
| "only 0.4 h of data" warning | Normal for a first session with `--orientation search`: use `labels` now, collect the long wear later |

---

## 10. Safety and privacy

- **No staged falls in these sessions.** Falls have their own protocol (mattress, supervision)
- **Ask before recording anyone else**, and tell them what's recorded (wrist movement only) and why
- **Codes, not names**, in files and the log sheet. Keep the code-to-name list separate and private
- **Stairs only if safe:** use the handrail, and don't rush for the sake of the recording
