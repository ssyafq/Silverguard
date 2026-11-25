# espwatch: record IMU data as CSV over Wi-Fi

Records the QMI8658 accelerometer and gyroscope on the Waveshare ESP32-S3-Touch-AMOLED-2.06 watch at 50 Hz. Samples are sent to the laptop over Wi-Fi, or over USB if Wi-Fi is not set up. The laptop saves them and cuts overlapping windows the same way as the training data.

| `--mode` | Used by | Window | Hop | Overlap | Training source |
|---|---|---|---|---|---|
| `har` (default) | Still/moving | 128 samples (2.56 s) | 64 | 50% | `notebooks/Model based of UCI DataSet/features.py` |
| `fall` | Fall model | 125 samples (2.5 s) | 62 | ~50% | `notebooks/fall model/fad_utils.py` |

The sensor settings are the same as the watch firmware: ±16 g, ±2048 dps, ODR ~62.5 Hz, LPF on, read on a fixed 20 ms schedule. Accel is in g. Gyro is in deg/s, with the bias measured at boot taken off.

## Setup (once)

1. Copy `wifi_config.example.h` to `wifi_config.h` and put in your hotspot name and password. The hotspot must be **2.4 GHz**. `wifi_config.h` is git-ignored.
2. Open `espwatch.ino` in the Arduino IDE. Board: **ESP32S3 Dev Module**, with **USB CDC On Boot: Enabled**. Upload.
3. On the first run, open the Serial Monitor to see the watch's IP address. It prints `# waiting for laptop: python capture_csv.py --host 192.168.x.x`.

## Record

Keep the watch still for 2 s after reset, while it measures the gyro bias. Then, on the laptop (connected to the same hotspot):

```
python capture_csv.py --host espwatch.local --out walk --label 1 --seconds 120
```

If `espwatch.local` does not resolve, use the IP instead (`--host 192.168.x.x`). Some phone hotspots give a different IP each time. For USB, use `--port COM5` in place of `--host`.

This produces two files:
- `walk_raw.csv`: one row per sample (`sample_index, t_ms, label, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps`).
- `walk_windows.csv`: the overlapping windows, with `window_id, sample_in_window` added in front. Each sample appears in two windows.

The watch can run on battery during recording. Wi-Fi dropouts are handled:
- The watch buffers up to 60 s of samples, and the script reconnects by itself, so a short dropout loses nothing.
- If the link is down for more than 60 s, the oldest samples are dropped. The gap shows in `sample_index`, and a window is never built across a gap.

Commands sent from the laptop: `0`–`9` sets the label, `p` pauses or resumes, `h` reprints the header. Every 10 s a `# status` line shows the backlog, missed slots and read errors.
