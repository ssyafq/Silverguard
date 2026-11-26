# What's next

Where the activity work stands, and the plan for the next part: **making the fall detector as solid as the new
activity model**, then putting everything on the watch.

The same rules as this folder apply throughout: split by person, choose settings on training people only, keep a
test set nobody tunes on, check on a second dataset, compare people one by one with a 95% range, train on the GPU
only, and check the C code against Python before anything goes on the watch.

---

## 1. Where we are (end of November 2026)

**Activity (still / moving, 4 levels): done on datasets, not yet on the watch**
- New model: XGBoost + live HMM, 30 s windows, 16 features, exported to C (163 kB), C = Python on 100% of test windows
- Better than the watch's current model in real daily life (kappa 0.739 vs 0.626) and on older adults by the
  research definition (0.932 vs 0.862). The old model is better when standing should count as still (section 2,
  decision 1)
- Axis setup + calibration tools ready (`watch_alignment.py`, Stage 7), recording guide written
  (`tests/HOW_TO_RECORD.md`), test script ready (`test_watch_recording.py`)
- Full story: `OLD_VS_NEW_MODEL.md`

**Fall detection: working on the prototype, but not yet tested with the same rigour**
- On the watch: 2.5 g spike → 30-tree forest → wrist turned > 20° → falling motion before the spike
  (`notebooks/fall model/`, `context.md` section 5)
- FallAllD (held-out people): 94% recall on the watch's forest, about 86 false alarms per hour of FallAllD's
  daily activities before the confirmation stages
- WEDA-FALL (another smartwatch): falls still caught (322–340/350) but 4–8x more false alarms: domain shift
- **No real falls of older people** in any dataset, and **no false-alarm rate for a real day** yet

---

## 2. Decisions the team needs to make first

1. **What does "moving" mean for SilverGuard?** Activity levels (standing = light, the new model) or "on their feet"
   (standing = still, the old model)? Or both: the new model for activity minutes every 30 s, the old one for quick
   still/moving every 0.5 s. This decides which model the inactivity alert uses
2. **What false-alarm rate is acceptable?** For example "at most 1 false fall alert per week of normal wear" and
   "at least 95% of staged falls caught". Write it down **before** building the next fall model, so the result
   can't be argued afterwards
3. **Which watch is the product?** ESP32-C3 + MPU-6050, or the Waveshare ESP32-S3 + QMI8658? Each needs its own
   recordings, calibration and axis setup

---

## 3. Finish the activity model on the watch (short)

- [x] Folder renamed to `notebooks/still-moving-DL`
- [ ] **Recording session 1** (`tests/HOW_TO_RECORD.md`): calibration + 3–5 min per activity
- [ ] Run `test_watch_recording.py --orientation labels`, save `watch_setup.json`
- [ ] **Session 2** with `--setup watch_setup.json`: the honest score to report
- [ ] **Long normal wear** (3+ h each from 3–5 people) → `--orientation search` to confirm the axis setup without
      labels
- [ ] Put the calibration (per-axis offset + gain, replacing the single `acc_scale`) and the axis setup
      (`to_model_frame`) into the firmware
- [ ] Add `export/sg_xgb*.h` to the firmware (Stage 8, section 5). Measure `sg_features` time with `micros()` on
      the ESP32-C3, and flash use with the partition in use
- [ ] Decide with the team whether it replaces or runs next to `har_model.h`
- [ ] The **inactivity alert** rule ("no movement for N minutes while awake") on top of the chosen model
- [ ] Re-check the old Block 6 result: its richer features lost 1 point on GOTOV, but GOTOV's axes were turned
      (Stage 6). With the axes lined up, that loss may disappear

---

## 4. The fall detector: the main next project

### 4a. Re-test the current fall pipeline honestly (no new model yet)

The current numbers come from one split of FallAllD and an external test on WEDA-FALL. Before improving anything,
measure it properly, the way the activity model was measured:

- [ ] **Cross-validation by person** on FallAllD (every person tested once), with recall and false alarms per hour
      per fold, and 95% ranges from resampling people
- [ ] **Each stage's contribution:** spike only → + forest → + wrist turn → + falling motion. How many falls each
      stage loses, how many false alarms each removes
- [ ] **The full on-device pipeline** (`confirm_stage_eval.py` already simulates it) on FallAllD, WEDA-FALL and
      UMAFall (UMAFall's wrist is only about 20 Hz: check whether that's usable)
- [ ] **False alarms in a real day.** FallAllD's "daily activities" are short scripted clips. Run the spike + forest
      + wrist-turn stages over **Capture-24** (151 real days, wrist, no gyro) and GOTOV to count **false alarms per
      day** of normal life. The gyro-based stage can't run there (no gyro), so report what's possible and say so.
      This is the number that decides whether an older person keeps wearing the watch

### 4b. Things the activity work showed that matter for falls

- [ ] **Calibration changes the 2.5 g trigger.** The watch reads 1.23 g at rest. If that's an offset on one axis
      (Stage 7, section 4), the single scale factor makes impacts read too high or too low depending on how the
      wrist is turned. Check how many FallAllD falls would be missed, or extra false alarms raised, if the
      trigger is off by ±0.2 g, and apply the per-axis calibration before the fall stages
- [ ] **Axes:** the fall forest uses only the size of the acceleration and gyro, and the wrist-turn stage uses the
      angle between two gravity directions. Both are the same however the sensor is turned, so the fall pipeline
      is safe from axis mix-ups. **Keep it that way**: if new fall features use x, y, z directly, they will need
      the Stage 7 axis setup
- [ ] **Domain shift:** WEDA-FALL's 4–8x more false alarms looks like the GOTOV problem. Check calibration
      offsets and the sensor's range (WEDA clips at 4 g) before blaming the model
- [ ] **What happens after a fall:** a real fall is usually followed by lying still. The new activity model + live
      HMM can say "sedentary/sleep" for the 30 s after a spike: test whether "spike + forest + still afterwards"
      cuts false alarms without missing falls (a person who gets up at once is a fall you may still want to
      catch, so measure both)

### 4c. Better models (on the GPU, chosen on training people only)

Try, in this order, keeping a fixed FallAllD test set and WEDA-FALL as the external check:

- [ ] **XGBoost** on the current 14 features (it beat the forest on activity, with a smaller model)
- [ ] **New features:** a free-fall dip before the impact (|a| well below 1 g), the size of the turn, how still the
      wrist is 1–2 s after, impact sharpness. Check each adds something, one group at a time
- [ ] **A small 1D CNN** on 2.5–5 s of raw magnitude (and gyro), under 50k numbers, trained on the 3060
      (Keras on the PyTorch backend, as in `cv_cnn.ipynb`). On activity the CNN lost to the trees, but impacts are
      short, sharp shapes, which is where CNNs can do well
- [ ] **Threshold choice:** pick the decision threshold for the recall you agreed (section 2), from training
      people only, and report the false alarms that come with it
- [ ] Rule for keeping a new model, decided in advance: **at least as many falls caught, and fewer false alarms,
      on both FallAllD's test people and WEDA-FALL, with 95% ranges.** Otherwise keep the current one

### 4d. Our own watch's fall recordings

The datasets' falls were staged by younger people onto mattresses with other devices. Testing on our own watch
matters most.

- [ ] **Ethics / supervisor approval** before recording falls
- [ ] **A fall-recording protocol** (like `tests/HOW_TO_RECORD.md`): mattress, a spotter, warm-up, types of fall
      (forward, backward, sideways, from sitting, slipping), several of each, `record.py` names like `fall_forward_01`
- [ ] **The hard non-falls:** sitting down hard, dropping onto a sofa, clapping, hitting a table, putting the watch
      down, taking it off, waving. These are where false alarms come from
- [ ] Score the on-device pipeline on them, the same way `test_watch_recording.py` scores activity
- [ ] **Look for real-world older-adult falls:** for example the FARSEEING repository (real falls recorded in daily
      life, available on request; check sensor position and access terms)

### 4e. Back onto the watch

- [ ] Export the chosen fall model to C, with a self-test and a C-vs-Python check over thousands of windows
      (as Stage 8 did)
- [ ] **Shared budget:** the activity model (163 kB) and the fall model share flash with Wi-Fi, TLS and audio.
      Check the whole firmware fits the partition you use
- [ ] **Timing:** the fall check runs every 0.5 s, the activity model every 30 s. Neither may delay the 50 Hz
      sampling
- [ ] Re-test the voice check-in and alert path end to end with the new detector

---

## 5. Putting it together

- [ ] **One state machine on the watch:** fall detected → voice check-in → alert. Activity level every 30 s.
      Inactivity alert from the chosen still/moving rule
- [ ] **Streaming** (`streaming/`): send the 30 s activity level as well as still/moving changes, and the confirmed
      falls
- [ ] **Dashboard:** activity minutes per day from the 4 levels; "can't see" when data stops
- [ ] **Evaluation (Obj 4):** a final report table: activity (Capture-24, GOTOV, own recordings), falls (FallAllD,
      WEDA-FALL, own recordings), false alarms per day, battery and timing

---

## 6. Housekeeping

- [x] Folder renamed to `still-moving-DL`; the environment can be rebuilt from `requirements-dl.txt`
- [ ] The large prediction files (`results/*_predictions.npz`, `results/cv_cnn_fold?.npz`) are not in git: re-run
      the notebooks to remake them. The processed data (`processed/`) is never in git
- [ ] `.venv-tf` (TensorFlow, PyTorch with CUDA, XGBoost) is local only. After the rename, use
      `.venv-tf\Scripts\python.exe -m pip` / `-m jupyter`, because the `.exe` shortcuts remember the old path
- [ ] Keep `progress.md` up to date with every change
