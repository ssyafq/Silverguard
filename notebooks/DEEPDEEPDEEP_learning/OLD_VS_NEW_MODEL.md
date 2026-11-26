# The old activity model vs the new one: what changed, and why the new one scores better

This explains, from scratch, the two still/moving models for the SilverGuard watch: the **old** one that runs on the
watch today, and the **new** one built in this folder. It goes through every step that improved the scores, with
the real numbers, and it's honest about where the old one is still better.

Every number here comes from a notebook in this folder, which checks its own claims (see the table at the end).

---

## 1. The short version

| | Old model (on the watch today) | New model (this folder) |
|---|---|---|
| Learned from | **UCI-HAR**: 30 young adults, a phone **on the waist**, a lab | **Capture-24**: 151 adults, a sensor **on the wrist**, a normal day, including sleep |
| Looks at | the last **2.56 s** | the last **30 s** |
| Uses | 5 numbers: the **size** of the acceleration (mean, spread, min, max, range) | 16 numbers: size, **wrist angle**, **rhythm** (how regular, how fast) |
| Model | random forest, 10 trees | XGBoost, 1,200 small trees |
| Then | nothing | an **HMM** that uses what happened before |
| Answers | still / moving, every 0.5 s | sleep / sedentary / light / moderate-vigorous, every 30 s |
| Size on the watch | a few kB | 163 kB |

**Head-to-head, still vs moving (Stage 9; kappa: 0 = guessing, 1 = perfect):**

| Test | Old | New |
|---|---|---|
| Real daily life (Capture-24, 30 people, standing counts as moving) | 0.626 | **0.739** (better for 28 of 30 people) |
| Older adults 60–81 (GOTOV, 35 people, standing counts as moving) | 0.862 | **0.932** (better for 33 of 35) |
| Older adults (GOTOV), **standing counts as still** | **0.957** | 0.876 (old better for 29 of 35) |

So the new model is clearly better at **"is this person active?"**, and the old one is better at **"is this person
on their feet or not?"**, because they learned different definitions of moving (section 7).

---

## 2. How to read the scores

- **Accuracy:** the share of answers that are right. Misleading on its own: 77% of the day is "still", so a model
  that always says "still" gets 77% accuracy
- **Kappa:** accuracy with **lucky guessing taken out**. 0 = no better than guessing, 1 = perfect. Roughly: 0.6–0.8 is
  good, above 0.8 is very good. This is the main score used here
- **Macro-F1:** how good the model is on **each** class, averaged with equal weight, so the rare active class counts
  as much as sleep
- **"Better for 28 of 30 people"** and a **95% range**: each person is scored separately. If the 95% range of the
  difference stays above 0, the gap is real and not luck

---

## 3. How the old model works

1. Every 0.5 s, take the last 128 samples (2.56 s) of acceleration
2. For each sample, work out the **size** of the acceleration, ignoring direction: √(x² + y² + z²). At rest this is
   1 g (gravity)
3. From those 128 sizes, compute 5 numbers: **mean, spread (std), minimum, maximum, range**
4. 10 decision trees vote: still or moving

It learned from **UCI-HAR**: 30 volunteers aged 19–48 doing scripted activities in a lab with a phone **on their
waist**. There, still = sitting, standing, lying; moving = walking, stairs.

**Its strengths**
- Very small and fast; answers every 0.5 s
- It only uses the **size** of the acceleration, so it **doesn't care how the sensor is turned**. No axis setup needed
- "Standing = still" fits a question like "is this person on their feet?"

**Its weaknesses**
- **Waist, not wrist.** On the waist, sitting is nearly motionless. On a wrist, hands move all the time (typing,
  eating, gesturing), so "how much is it moving?" alone can't tell sitting-with-busy-hands from light activity
- **Lab, not life.** Scripted activities are much cleaner than a real day
- **5 numbers are the limit.** Earlier work (`notebooks/Model based of UCI DataSet/next_steps.ipynb`, `context.md`)
  found that on held-out Capture-24 wrist data, the UCI model, a forest retrained on Capture-24 with the same
  features, and a single threshold on the spread all scored about the same (around 0.74–0.77). A better model or
  more data didn't help, because these 5 numbers don't contain enough information. That finding is why the new
  model's main change is its **features** (section 5c)
- **2.56 s is short.** It can't see rhythm over several steps, or what the wrist was doing a minute ago
- **No memory.** Each 2.56 s is judged alone, so the answer can flicker

---

## 4. How the new model works, step by step

```
50 Hz wrist acceleration (x, y, z)
   → calibrate and line up the axes (Stage 7)
   → cut into 30 s windows (1,500 samples)
   → 16 features per window
   → XGBoost: 4 probabilities (sleep, sedentary, light, moderate-vigorous)
   → live HMM: combine with what came before
   → one answer every 30 s (and still/moving = sleep+sedentary vs light+moderate-vigorous)
```

**The 16 features** (the same ones a person would use to describe the movement):

| Question | Features |
|---|---|
| How much is the wrist moving? | spread, min, max, range of the acceleration's size; spread of each axis |
| Which way is the wrist pointing? | mean of each axis, pitch, roll (gravity gives the angle) |
| Is there a rhythm, like steps? | the strongest rhythm between 0.5 and 8 Hz; how much movement is at 0.5–3 Hz (walking) and 3–8 Hz |

**XGBoost:** many small decision trees built one after another, each one correcting the mistakes the earlier ones
still make. The final version uses 1,200 trees, 3 questions deep each.

**The HMM:** people don't switch activity every 30 seconds. The HMM knows how often people really switch (learned
from the training data: after "sleep", the next 30 s is sleep again 99.9% of the time), and uses that to overrule
answers that don't fit. On the watch it runs **live**: each answer uses only what came before (section 5f).

---

## 5. How we got the new model's numbers, one step at a time

### 5a. Training data that looks like the watch's real life

| | UCI-HAR (old) | Capture-24 (new) |
|---|---|---|
| People | 30, aged 19–48 | **151**, aged 18 to 53+ |
| Sensor position | waist | **wrist**, like the watch |
| Setting | lab, scripted | **a normal day**, labelled from a body camera and sleep diary |
| Hours | about 3.7 | about **2,500** labelled hours |
| Includes sleep | no | **yes** |

Stage 0 (`deeplearn.ipynb`) cut Capture-24 into 301,164 windows of 30 s at 50 Hz (the watch's rate), and kept a
window only if one activity covered at least 90% of it.

### 5b. Longer windows: 30 s instead of 2.56 s

30 seconds holds 25–60 steps of walking, so the **rhythm** is clear, and a busy-handed but seated person looks
different from a walking one. The cost: one answer every 30 s instead of every 0.5 s.

### 5c. Better features: 16 instead of 5

The old model only knew *how much* the wrist moved. The new one also knows **which way it points** (lying, desk,
hanging arm) and **whether the movement repeats like steps**. Stage 1's random forest with these features already
reached kappa 0.701 on 4 classes, from features alone.

### 5d. Class weights for the rare classes

Moderate-vigorous activity is only about 5% of a day. Without help, a model can ignore it and still score well. Each
window was weighted by how rare its class is, so missing a rare active window costs as much as missing a common one.

### 5e. A fair contest between three kinds of model

The same people, the same test set, the same HMM, the same scores (Stages 1–3, 30 held-out people):

| Model | 4 classes, no HMM | 4 classes, with HMM | Still vs moving, with HMM |
|---|---|---|---|
| Random forest (16 features) | 0.701 | 0.814 | 0.778 |
| 1D CNN (raw signal, learns its own features) | 0.659 | 0.768 | 0.758 |
| **XGBoost (16 features)** | 0.696 | **0.818** | **0.790** |
| Always guess "sedentary" | 0.000 | | |

Then **cross-validation** on all 151 people (each person tested once, by a model that never saw them) confirmed it:
XGBoost + HMM 4-class 0.822 and still-vs-moving 0.812. That's a tie with the forest on 4 classes, and **better on
still vs moving** (better for 94 of 151 people). The CNN was behind on 4 classes, and clearly weaker for people aged
53+.

### 5f. The HMM: the single biggest gain

| Model | Without HMM | With HMM | Gain |
|---|---|---|---|
| Random forest | 0.707 | 0.825 | +0.12 |
| CNN | 0.683 | 0.788 | +0.11 |
| XGBoost | 0.707 | 0.822 | +0.12 |

(4-class kappa, cross-validation on 151 people.) The HMM helped **150–151 of 151 people** for every model. Why: a
model judging 30 s alone sometimes says "walking" at 3 am because someone rolled over. The HMM knows that's very
unlikely in the middle of a night's sleep and overrules it.

**On the watch it must run live** (it can't see the rest of the day). The live version costs a little:

| Watch model (Stage 8, 30 test people) | 4 classes | Still vs moving |
|---|---|---|
| No HMM | 0.686 | 0.684 |
| Live HMM (what the watch does) | **0.782** | **0.739** |
| Whole-day HMM (best possible) | 0.810 | 0.767 |

### 5g. Honest testing, so the numbers can be trusted

- **Split by person, never by window.** Two windows from the same person look alike. A person is either in training
  or in testing, never both
- **A fixed test set** of 30 people, chosen before any model was built
- **Settings chosen on training people only**, with validation grouped by person
- **Cross-validation on all 151**, to check the 30-person test wasn't a lucky draw (it wasn't: the scores agree
  within 0.01–0.05)
- **A completely separate dataset (GOTOV)**: 35 people aged 60–81, a different sensor brand, never used for training
  or tuning
- **Person-by-person comparisons with 95% ranges**, so "better" means more than luck

### 5h. Lining up the sensor's axes

The new model uses the wrist's **angle**, so it must know which way the sensor's axes point. On GOTOV, the sensor
was turned half a turn compared with Capture-24's. Fixing that (with a method that uses no labels) took still vs
moving from **0.895 to 0.990** and fixed most of the "walking called light" errors. On Capture-24 people, a wrong
setup costs up to 0.15 kappa (4 classes) or 0.20 (still vs moving). This is why the watch needs a one-time axis
setup and calibration (Stage 7, `explaination-orientation.md`). The old model doesn't need this.

### 5i. Shrinking it to fit the watch, without losing accuracy

The full XGBoost was about 2 MB, too big for the ESP32-C3's 4 MB of flash, which it shares with Wi-Fi, audio and
the fall model. A smaller one (1,200 trees, 3 questions deep) is **163 kB**, and on the test people it ties the big
one (4-class 0.810 vs 0.818 with the whole-day HMM; the difference is within luck). The final version learned from
all 151 people; on GOTOV it scores the same as the tested version.

### 5j. Making sure the watch computes exactly what Python does

The model was exported to C (`export/`). Compiled on a laptop and run on 8,558 real windows, the C code gives **the
same final answer for 100% of windows**, with probabilities within 0.004 of Python's.

---

## 6. The head-to-head in detail (Stage 9)

Both models run **exactly as on the watch**: the old one through the firmware's own C files (checked against the
firmware's own boot self-test), the new one with the live HMM. The old model's 0.5 s answers are combined per 30 s
by majority.

### Real daily life (Capture-24, 30 test people, 58,152 windows)

| | Old | New |
|---|---|---|
| Kappa | 0.626 | **0.739** |
| Accuracy | 0.887 | **0.918** |

What each calls **moving**:

| True activity | Old | New |
|---|---|---|
| Sleep | 1% | 1% |
| Sedentary | 11% | 10% |
| **Light** | **60%** | **75%** |
| Moderate-vigorous | 90% | 96% |

Both are equally good on sleep and sitting. The new model's gain is almost all in **light activity** (chores,
standing, slow walking), the hardest part of a real day. Person by person, the new one is better for 28 of 30
(+0.118, 95% range +0.086 to +0.150).

### Older adults (GOTOV, 35 people aged 60–81)

What each calls **moving**:

| Activity | Old | New |
|---|---|---|
| Lying, sitting | 0–1% | 0–13% |
| **Standing** | **1%** | **80%** |
| Dishwashing | 99% | 83% |
| Stacking shelves, vacuuming | 90–97% | 100% |
| Walking (slow, normal, fast), cycling | 94–100% | 100% |

---

## 7. Why the old model wins one of the tests: what counts as "moving"?

Capture-24 (the new model's teacher) uses the research standard for physical activity: **standing counts as light
activity**, because standing burns more energy than sitting. UCI-HAR (the old model's teacher) counts standing as
**still**.

So on GOTOV:
- Scored by **Capture-24's rule** (standing = moving): new 0.932, old 0.862
- Scored by the **physical rule** (standing = still): old 0.957, new 0.876

Neither model is "wrong". They answer different questions:

| If SilverGuard wants to know... | Standing should count as | Better model |
|---|---|---|
| "How active was this person today?" (activity minutes, exercise) | moving (light) | **new** |
| "Is this person up and about, or sitting/lying?" (for example, an inactivity alert) | still | old, on GOTOV; the new one can't separate standing from light chores |

**One more caution:** GOTOV's standing is a calm, scripted rest, and its still moments are much quieter than real
life. That flatters a model that only measures how much the wrist moves. In real life (Capture-24) we can only score
by Capture-24's rule, so the physical-rule comparison rests on GOTOV alone.

**A sensible option:** run both. The new model for activity levels (every 30 s), and the old one for quick
still/moving reactions (every 0.5 s).

---

## 8. What "great numbers" does and doesn't mean

**It does mean:**
- On a large real-life dataset, with honest person-level testing, the new model is clearly better than the old one
  at telling still from moving by the research definition, and it also gives 4 activity levels
- It works on older adults (60–81) with a different sensor brand, once the axes are lined up
- The C code on the watch will compute the same answers as Python

**It doesn't mean (yet):**
- **Not tested on the SilverGuard watch.** Every number is from Capture-24 or GOTOV. `test_watch_recording.py` and
  `tests/HOW_TO_RECORD.md` are for that next step
- **Not tested on people over 81**, or on frail walkers (Stage 6 found very old people often walk in bouts under 30 s,
  which the 30 s windows and the HMM can miss)
- **The axis setup and calibration must be done** for our watch first (Stage 7), or the scores will drop
- **It answers every 30 s**, not every 0.5 s
- **Lying awake can look like sleep** on the wrist, for both models

---

## 9. Glossary

| Word | Meaning |
|---|---|
| Window | a fixed stretch of signal the model judges at once (old: 2.56 s, new: 30 s) |
| Feature | a number computed from a window that describes it (for example, how much it moves) |
| Decision tree | a flowchart of yes/no questions about the features |
| Random forest | many trees built independently, which vote |
| XGBoost | many small trees built one after another, each fixing the earlier ones' mistakes |
| CNN | a neural network that learns its own patterns from the raw signal |
| HMM | a step after the model that uses how often people really switch activity to clean up the answers |
| Live HMM | the HMM using only the past, as it must on the watch |
| Kappa | accuracy with lucky guessing removed: 0 = guessing, 1 = perfect |
| Cross-validation | testing every person once, each time with a model that never saw them |
| Calibration | correcting each axis's offset and gain, so the sensor reads exactly 1 g at rest |
| Axis setup | which watch axis is the model's x, y and z, and which way round |
| Light / moderate-vigorous | effort levels: light = standing, chores, slow walking; moderate-vigorous = brisk walking, stairs, cycling |

---

## 10. Where each result comes from

| Topic | Notebook |
|---|---|
| Preparing Capture-24 (30 s windows) | `deeplearn.ipynb` |
| Random forest + HMM | `stage1_rf_hmm.ipynb` |
| 1D CNN + HMM | `stage2_cnn_hmm.ipynb` |
| XGBoost + HMM | `stage3_xgb_hmm.ipynb` |
| Cross-validation on 151 people | `cv_trees.ipynb`, `cv_cnn.ipynb`, `cv_compare.ipynb` |
| Summary and verdict | `stage4_summary.ipynb` |
| Older adults, different sensor (GOTOV) | `stage5_gotov.ipynb` |
| Very old people's walking (HAR70+), and the axis finding | `stage6_har70_walking.ipynb` |
| Axis setup and calibration for the watch | `stage7_axis_alignment.ipynb`, `explaination-orientation.md` |
| Shrinking and exporting to C | `stage8_export_xgboost.ipynb`, `export/` |
| Old vs new, head to head | `stage9_old_vs_new.ipynb` |
| Testing on the watch's own recordings | `test_watch_recording.py`, `tests/HOW_TO_RECORD.md` |
