# SilverGuard logger + on-device models

ESP32-C3 + MPU-6050 at 50 Hz. The board:
- streams raw accel/gyro counts over USB serial (`record.py` saves labelled CSV trials), and
- runs two small random forests every 0.5 s: **HAR** (still / moving) and the **fall model** (2.5 g trigger, then a forest decides).

| File | What it is |
|---|---|
| `silverguard_logger.ino` | the sketch |
| `fall_features.h` | window features, same maths as the Python training code |
| `fall_model.h`, `fall_model_trees.h` | fall forest + threshold (generated) |
| `har_model.h` | still/moving forest (generated) |
| `test_vectors.h` | real windows + Python's answers for the boot self-test (generated) |
| `record.py` | records one labelled trial on the laptop |

Generated files come from `notebooks/fall model/export_models_to_c.py`. Re-run it after retraining; never edit them by hand.

## Wiring

| MPU-6050 | ESP32-C3 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 3 |
| SCL | GPIO 4 |
| AD0 | GND (address 0x68), or leave unconnected if the breakout pulls it low |

Pins are set at the top of the sketch (`SDA_PIN`, `SCL_PIN`).

## Arduino IDE settings
- **Board:** `ESP32C3 Dev Module` (or your exact C3 board)
- **USB CDC On Boot: Enabled** (required, otherwise `Serial` goes to the UART pins, not the USB cable)
- **Port:** the board's COM port
- Serial Monitor: 115200 baud (the baud value doesn't matter over native USB, but keep it the same)
- No extra libraries needed (only `Wire`, which comes with the ESP32 core)

## Check it works
**Keep the watch still for the first ~4 s after reset** (gyro calibration). Open the Serial Monitor after uploading and press reset. You should see:
- `# WHO_AM_I: 0x68` (another value means a clone chip or a wiring problem; it keeps running anyway)
- five `# reg ... OK` lines (these prove the range, filter and rate settings stuck)
- `# self-test ... PASS` for 3 windows, then `# self-test: all PASS`. A FAIL means the device maths does not match Python: don't trust the models.
- `# gyro_bias_dps: ...` (a few deg/s is normal; a `WARNING` means it moved during calibration, so reset and keep still)
- then CSV lines `sample_index,micros,ax,ay,az,gx,gy,gz`, a `# result,...` line every 0.5 s, and a `# status:` line every 10 s

Lying flat and still, one accel axis should read about **2048** (= 1 g) and the gyro should read close to 0.

## Reading the model output
**Live demo mode (default, `RAW_STREAM 0`):** one readable line every 0.5 s:
```
[    12.5 s]  Activity: Moving  | peak  1.31 g | fall check: -
[    14.0 s]  Activity: Moving  | peak  3.02 g | fall check: score 0.07 (alarm at 0.27) -> not a fall
ALERT ALERT ALERT ALERT ALERT ALERT ALERT FALL DETECTEDDD!!!
```
`fall check: -` means the spike stayed under 2.5 g, so the fall forest did not need to run.

**Stage 3, the wrist-turn check.** When the forest says "possible fall", the board waits 2.5 s and compares the gravity direction before the spike (1.5-0.5 s before) with after it (1.0-2.5 s after). A real fall leaves the wrist in a different position; hitting a table or clapping does not. Below `CONFIRM_MIN_TURN_DEG` (20°) the candidate is rejected:
```
[    20.0 s]  Activity: Moving  | peak  4.80 g | fall check: score 0.83 (alarm at 0.27) -> possible fall, checking wrist position for 2.5 s...
             -> NOT a fall: wrist only turned 6 deg (needs > 20). Probably a hit / clap / tap.
```
Evidence for the 20° setting: `notebooks/fall model/confirm_stage_eval.py`. On WEDA-FALL it cut "hit table" alarms from 54 to 28 recordings and older adults' false alarms from 49 to 26, while keeping 338 of 342 alarmed falls. It does **not** stop every false alarm: a hit with a big arm swing can still pass. A larger value means fewer false alarms but more missed falls. If the data stream breaks during the check, it alarms anyway (missing a fall is worse).

**Stage 3b, the falling-motion check.** It also looks at the 1 s *before* the spike. In a real fall the wrist drops (|a| dips well below 1 g) and spins fast. Setting the device down on a table does neither. A candidate is rejected if |a| stayed above `NO_DIP_ABOVE_G` (0.85 g) **and** rotation stayed below `SLOW_TURN_BELOW_DPS` (150 dps):
```
             -> NOT a fall: no falling motion before the knock (lowest 0.93 g, needs < 0.85; fastest turn 60 dps, needs > 150). Probably put down.
```
On FallAllD + WEDA this rule drops only 1 of 809 alarmed falls. Neither dataset has "put the device down" recordings, so the two values are starting points: every accepted or rejected candidate prints its turn, lowest g and fastest turn, so you can see on your prototype how a put-down compares, and tune.
On a fall (`HALT_ON_FALL 1`) the board **stops sampling** and repeats the ALERT line every second until you type `r` in the Serial Monitor (resume) or press reset.

**Recording mode (`RAW_STREAM 1`, needed for `record.py`):** raw CSV lines plus machine-readable lines:
- `# result,sample_index,har,peak_g,fall_score,fall` every 0.5 s:
  - `har`: 0 = still, 1 = moving (last 2.56 s)
  - `peak_g`: biggest acceleration in the last 2.5 s
  - `fall_score`: fraction of the 30 trees voting "fall"; `-1` means the 2.5 g trigger did not fire, so the forest did not run
  - `fall`: 1 if the score reached the threshold (printed in the header)
- `# EVENT FALL,impact_sample_index,score,peak_g=..,turn_deg=..` when stage 3 confirms a fall, and `# EVENT REJECTED,...` when it rejects one. `fall` in the result line marks a *candidate*. After a candidate, new ones are ignored for 5 s, because one spike appears in several overlapping windows.
- `# status:` now also shows `falls=` and `max_model_us=` (the slowest model run; it must stay well below 20,000 µs, otherwise samples get dropped).

Switches at the top of the sketch: `RAW_STREAM` (0 = readable demo output, 1 = raw CSV for `record.py`), `HALT_ON_FALL` (0 = keep running after an alert, useful while recording trials), `SELF_TEST` (0 = skip the boot check).

Quick prototype test: move the watch around (HAR flips to 1), tap the table firmly (should not raise an event), then do a staged fall onto a mattress (one `# EVENT FALL`).

## Record a trial
Set `RAW_STREAM 1` (and usually `HALT_ON_FALL 0`, so a fall doesn't stop the recording) and upload. Close the Serial Monitor first (only one program can use the port).

```
pip install pyserial numpy
python record.py COM5 fall_forward_01               # 20 s
python record.py COM5 walking_01 --seconds 60
```
Each run saves `recordings/<label>_<YYYYmmdd_HHMMSS>.csv` (raw data) and `..._events.csv` (the device's `result` / `EVENT` lines during the trial, with sample_index so they line up with the raw data). It prints the sample count, the real sample rate, gaps, peak |a| and |gyro|, and how many fall events the device raised.

To load a recording in Python: `pd.read_csv(path, comment='#')`.

Tips: start `record.py`, wait for "recording ...", then do the movement. Use one label per trial (`fall_forward_01`, `fall_forward_02`, ...), and note on paper who wore it and on which wrist.

## Limits (say these in the report)
- Both models were trained on other people's hardware: fall = FallAllD wrist logger (young volunteers, falls onto mattresses), HAR = UCI phone at the waist. Recordings from this prototype are the real test.
- On held-out FallAllD people the fall model caught 94% of fall windows (all 73 fall recordings), with ~86 false alarms per hour of staged daily activity. Expect more false alarms on new hardware.
- Not a certified fall detector.
