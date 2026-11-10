"""Shared FallAllD helpers, so every fall notebook loads, converts and windows the data the same way.

Units after loading: accel in g, gyro in deg/s, both resampled to 50 Hz.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import resample_poly

FAD_ROOT = Path('../../data/open datasets/FallAllD__zip/FallAllD')
FS_IN, FS_OUT = 238, 50          # FallAllD rate -> SilverGuard rate
ACC_COUNTS_PER_G = 4096          # +/-8 g setting; resting |a| measured ~4,061 counts
GYRO_DPS_PER_COUNT = 0.070       # +/-2000 dps setting; verified from the data in fallalld_gyro_eda_and_rule.ipynb
WIN, HOP = 125, 62               # 2.5 s windows, ~50% overlap
WRIST_DEVICE = 2                 # believed to be the wrist (not confirmed by the folder)

_PAT = re.compile(r'^S(\d+)_D(\d)_A(\d+)_T(\d+)_A\.dat$')


def read_dat(p):
    """One FallAllD .dat file -> int16 array (rows, 3)."""
    return pd.read_csv(p, header=None, dtype='int16').to_numpy()


def gyro_path(accel_path):
    p = Path(accel_path)
    return p.with_name(p.name[:-len('A.dat')] + 'G.dat')


def remove_gyro_bias(acc_g, gyr_dps, fs=FS_OUT):
    """Subtract the gyro reading during still moments (a still sensor should read 0 deg/s)."""
    mag = np.linalg.norm(acc_g, axis=1)
    still = pd.Series(mag).rolling(fs, center=True, min_periods=1).std().to_numpy() < 0.015
    if still.sum() >= fs // 2:
        return gyr_dps - gyr_dps[still].mean(axis=0)
    return gyr_dps


def load_device(dev=WRIST_DEVICE, root=FAD_ROOT, fs_out=FS_OUT):
    """Load every recording of one device.

    returns: trials (DataFrame), ACC (n, samples, 3) in g, GYR (n, samples, 3) in deg/s (bias removed)
    """
    meta, acc, gyr = [], [], []
    for p in sorted(Path(root).glob(f'S??_D{dev}_A???_T*_A.dat')):
        s, d, a, t = map(int, _PAT.match(p.name).groups())
        A = resample_poly(read_dat(p).astype('float32') / ACC_COUNTS_PER_G, fs_out, FS_IN, axis=0)
        G = resample_poly(read_dat(gyro_path(p)).astype('float32') * GYRO_DPS_PER_COUNT, fs_out, FS_IN, axis=0)
        G = remove_gyro_bias(A, G, fs_out)
        meta.append({'subject': s, 'activity': a, 'trial': t, 'is_fall': a >= 100})
        acc.append(A.astype('float32'))
        gyr.append(G.astype('float32'))
    return pd.DataFrame(meta), np.stack(acc), np.stack(gyr)


def make_windows(ACC, GYR, trials, win=WIN, hop=HOP):
    """Cut recordings into windows and label them.

    A window is a fall (1) if it is from a fall recording AND contains the impact
    (the biggest accel spike of that recording). Everything else is 0.
    """
    starts = np.arange(0, ACC.shape[1] - win + 1, hop)
    impact = np.linalg.norm(ACC, axis=2).argmax(axis=1)

    def cut(arr):
        return np.stack([arr[:, s:s + win] for s in starts], axis=1)

    has_impact = (impact[:, None] >= starts[None, :]) & (impact[:, None] < starts[None, :] + win)
    yW = has_impact & trials.is_fall.to_numpy()[:, None]
    n_rec, n_win = yW.shape
    win_meta = pd.DataFrame({
        'rec': np.repeat(np.arange(n_rec), n_win),
        'subject': np.repeat(trials.subject.to_numpy(), n_win),
        'activity': np.repeat(trials.activity.to_numpy(), n_win),
        'start': np.tile(starts, n_rec),
        'y': yW.ravel().astype(int),
    })
    return win_meta, cut(ACC).reshape(-1, win, 3), cut(GYR).reshape(-1, win, 3), starts, impact


def clip_accel(acc_g, limit_g=4.0):
    """Cut accel off at +/-limit_g per axis, like a sensor with a smaller range (WEDA-FALL clips at 4 g)."""
    return np.clip(acc_g, -limit_g, limit_g)


# ---------------------------------------------------------------- WEDA-FALL
WEDA_ROOT = Path('../../data/open datasets/WEDA-FALL/dataset')
WEDA_GYRO_DPS_PER_UNIT = 180 / np.pi    # WEDA gyro is rad/s (verified in two_stage_model_and_weda_test.ipynb)


def _to_uniform_grid(df, fs):
    """Bursty timestamps -> one sample every 1/fs s (sort, drop repeated times, linear interpolation)."""
    df = df.sort_values(df.columns[0]).drop_duplicates(df.columns[0])
    t = df.iloc[:, 0].to_numpy()
    grid = np.arange(0, t.max(), 1 / fs)
    return np.stack([np.interp(grid, t, df.iloc[:, i].to_numpy()) for i in (1, 2, 3)], axis=1)


def load_weda(root=WEDA_ROOT, fs=FS_OUT):
    """All WEDA-FALL 50Hz recordings on a uniform grid. accel in g, gyro in deg/s (bias removed).

    returns: trials (DataFrame with impact sample for falls), list of accel arrays, list of gyro arrays
    """
    stamps = pd.read_csv(Path(root) / 'fall_timestamps.csv', encoding='utf-8-sig')
    stamps.columns = ['key', 'fall_start_s', 'fall_end_s']
    stamps = stamps.set_index('key')
    meta, accs, gyrs = [], [], []
    for p in sorted((Path(root) / '50Hz').glob('*/U??_R??_accel.csv')):      # exact match: skips *_vertical_accel.csv
        act, stem = p.parent.name, p.name[:-len('_accel.csv')]
        A = _to_uniform_grid(pd.read_csv(p), fs) / 9.81
        G = _to_uniform_grid(pd.read_csv(p.with_name(stem + '_gyro.csv')), fs) * WEDA_GYRO_DPS_PER_UNIT
        n = min(len(A), len(G))
        A, G = A[:n].astype('float32'), remove_gyro_bias(A[:n], G[:n], fs).astype('float32')
        user = int(stem[1:3])
        rec = {'key': f'{act}/{stem}', 'activity': act, 'subject': user, 'group': 'older' if user >= 21 else 'young',
               'is_fall': act.startswith('F'), 'n': n, 'impact': -1}
        if rec['is_fall']:
            s, e = stamps.loc[rec['key'], ['fall_start_s', 'fall_end_s']]
            lo, hi = int(s * fs), min(int(e * fs) + 1, n)
            rec['impact'] = lo + int(np.linalg.norm(A[lo:hi], axis=1).argmax())   # biggest spike inside the annotated fall
        meta.append(rec); accs.append(A); gyrs.append(G)
    return pd.DataFrame(meta), accs, gyrs


def windows_from_list(accs, gyrs, trials, win=WIN, hop=HOP):
    """Like make_windows, for recordings of different lengths. trials needs 'impact' (-1 = not a fall)."""
    rows, wa, wg = [], [], []
    for r, (A, G) in enumerate(zip(accs, gyrs)):
        for s in range(0, len(A) - win + 1, hop):
            imp = trials.impact.iat[r]
            rows.append({'rec': r, 'start': s, 'y': int(imp >= 0 and s <= imp < s + win)})
            wa.append(A[s:s + win]); wg.append(G[s:s + win])
    wm = pd.DataFrame(rows)
    for c in ('subject', 'activity', 'group'):
        if c in trials:
            wm[c] = trials[c].to_numpy()[wm.rec]
    return wm, np.stack(wa), np.stack(wg)


def make_features(acc, gyr, fs=FS_OUT):
    """Same 44 features as fallalld_wrist_fall_classifier.ipynb (accel in g, gyro in deg/s)."""
    am, gm = np.linalg.norm(acc, axis=2), np.linalg.norm(gyr, axis=2)
    sig = {'ax': acc[..., 0], 'ay': acc[..., 1], 'az': acc[..., 2], 'am': am,
           'gx': gyr[..., 0], 'gy': gyr[..., 1], 'gz': gyr[..., 2], 'gm': gm}
    f = {}
    for k, v in sig.items():
        f[f'{k}_mean'], f[f'{k}_std'] = v.mean(1), v.std(1)
        f[f'{k}_min'], f[f'{k}_max'] = v.min(1), v.max(1)
        f[f'{k}_range'] = v.max(1) - v.min(1)
    f['am_jerk_max'] = np.abs(np.diff(am, axis=1)).max(1) * fs
    f['am_energy'] = (am ** 2).mean(1)
    f['am_tail_std'] = am[:, -fs:].std(1)
    f['gm_jerk_max'] = np.abs(np.diff(gm, axis=1)).max(1) * fs
    return pd.DataFrame(f)


# feature sets for the with / without gyro comparison (names produced by make_features)
def feature_sets(columns):
    cols = list(columns)
    return {
        'accel only (all axes)':       [c for c in cols if c[:2] in ('ax', 'ay', 'az', 'am')],
        'accel only (magnitude)':      [c for c in cols if c.startswith('am')],
        'accel + gyro (magnitude)':    [c for c in cols if c[:2] in ('am', 'gm')],
        'accel + gyro (all axes)':     cols,
    }
