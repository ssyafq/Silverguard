"""Record one labelled trial from the SilverGuard logger and save it as CSV.

usage:  python record.py COM5 fall_forward_01            (20 s)
        python record.py COM5 walking_01 --seconds 60
"""
import argparse
import datetime
import re
import time
from pathlib import Path

import numpy as np
import serial   # pip install pyserial

DEFAULT_ACC_LSB_PER_G = 2048.0     # +-16 g   (used if the header does not say)
DEFAULT_GYRO_LSB_PER_DPS = 16.4    # +-2000 dps


def open_port(port):
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 1
    ser.dtr = False                 # don't toggle DTR/RTS on open: on some boards that resets the ESP32
    ser.rts = False
    ser.open()
    return ser


def record(ser, seconds):
    """Return (header lines, data rows, model lines). Duration counts from the first data line."""
    ser.reset_input_buffer()
    ser.write(b'h')                 # ask the board to print its header again (boot lines are usually missed)
    header, rows, events, bad, t_end = [], [], [], 0, None
    while t_end is None or time.time() < t_end:
        line = ser.readline().decode('ascii', errors='replace').strip()
        if not line:
            if t_end is None and not header:
                print('waiting for data... (is the board plugged in and running the logger?)')
            continue
        if line.startswith('# result,') or line.startswith('# EVENT'):
            if t_end is not None:   # model output during the recording -> its own file
                events.append(line[2:])
            continue
        if line.startswith('#'):
            header.append(line)
            continue
        parts = line.split(',')
        if len(parts) != 8:
            bad += 1
            continue
        try:
            rows.append([int(p) for p in parts])
        except ValueError:
            bad += 1
            continue
        if t_end is None:
            t_end = time.time() + seconds
            print(f'recording {seconds} s ...')
    if bad:
        header.append(f'# record.py: skipped {bad} malformed lines')
    return header, np.array(rows, dtype=np.int64), events


def scale_from_header(header, key, default):
    for line in header:
        m = re.match(rf'#\s*{key}:\s*([0-9.]+)', line)
        if m:
            return float(m.group(1))
    return default


def main():
    ap = argparse.ArgumentParser(description='Record one SilverGuard trial.')
    ap.add_argument('port', help='serial port, e.g. COM5 or /dev/ttyACM0')
    ap.add_argument('label', help='trial name, e.g. fall_forward_01')
    ap.add_argument('--seconds', type=float, default=20, help='duration (default 20)')
    ap.add_argument('--outdir', default=str(Path(__file__).parent / 'recordings'))
    args = ap.parse_args()

    with open_port(args.port) as ser:
        header, data, events = record(ser, args.seconds)
    if len(data) < 2:
        raise SystemExit('no data received')

    acc_lsb = scale_from_header(header, 'accel_lsb_per_g', DEFAULT_ACC_LSB_PER_G)
    gyr_lsb = scale_from_header(header, 'gyro_lsb_per_dps', DEFAULT_GYRO_LSB_PER_DPS)
    idx, us = data[:, 0], data[:, 1]
    acc_g = data[:, 2:5] / acc_lsb
    gyr_dps = data[:, 5:8] / gyr_lsb

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'{args.label}_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv'
    with open(path, 'w', newline='') as f:
        f.write(f'# label: {args.label}\n')
        f.write(f'# converted with accel_lsb_per_g={acc_lsb}, gyro_lsb_per_dps={gyr_lsb}\n')
        for h in header:
            f.write(h + '\n')
        f.write('sample_index,micros,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps\n')
        for i in range(len(data)):
            a, g = acc_g[i], gyr_dps[i]
            f.write(f'{idx[i]},{us[i]},{a[0]:.5f},{a[1]:.5f},{a[2]:.5f},{g[0]:.3f},{g[1]:.3f},{g[2]:.3f}\n')

    if events:   # device model output: 'result,sample_index,har,peak_g,fall_score,fall' and 'EVENT FALL,sample_index,score,...'
        ev_path = path.with_name(path.stem + '_events.csv')
        ev_path.write_text('\n'.join(events) + '\n')
        print(f'saved {ev_path}')

    dt = np.diff(us) % 2**32                      # micros() wraps every ~71.6 min; modulo undoes it
    steps = np.diff(idx)
    print(f'saved {path}')
    print(f'samples:               {len(data)}')
    print(f'effective rate:        {(len(data) - 1) / (dt.sum() / 1e6):.2f} Hz')
    print(f'gaps in sample_index:  {int((steps != 1).sum())} ({int((steps - 1).clip(min=0).sum())} samples missing)')
    print(f'max |a|:               {np.linalg.norm(acc_g, axis=1).max():.2f} g')
    print(f'max |gyro|:            {np.linalg.norm(gyr_dps, axis=1).max():.1f} dps')
    n_res = sum(e.startswith('result,') for e in events)
    n_fall = sum(e.startswith('EVENT FALL') for e in events)
    if n_res:
        moving = sum(e.split(',')[2] == '1' for e in events if e.startswith('result,'))
        print(f'device model results:  {n_res} (moving in {moving}), fall events: {n_fall}')


if __name__ == '__main__':
    main()
