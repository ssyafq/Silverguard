"""Line up a new sensor's axes (and calibration) with Capture-24's, so the models read it correctly.

Used by stage7_axis_alignment.ipynb, and meant to be run on the SilverGuard watch's own recordings.

Two steps, in this order:
1. calibrate:  per-axis offset and gain, so a still sensor reads exactly 1 g whichever way it faces
2. align:      which watch axis is the model's x, y and z, and with which sign (a "signed permutation")

WHY only signed permutations: the sensor chip is mounted square in the watch, so its axes can only be
swapped and/or reversed relative to another device, never tilted by an odd angle.
"""
from itertools import permutations, product

import numpy as np

# ---------- the 48 candidate orientations ----------

def all_orientations():
    """-> list of (name, 3x3 matrix). 24 rotations (det +1) and 24 mirror images (det -1, e.g. the other wrist).

    A matrix R maps a reading in the watch's frame to the model's frame: model = R @ watch.
    """
    out = []
    for perm in permutations(range(3)):
        for signs in product([1, -1], repeat=3):
            R = np.zeros((3, 3))
            for model_axis, (watch_axis, s) in enumerate(zip(perm, signs)):
                R[model_axis, watch_axis] = s
            name = ', '.join(f"{'+' if s > 0 else '-'}{'xyz'[w]}" for w, s in zip(perm, signs))
            out.append((f'model x, y, z = watch {name}', R))
    return out


ORIENTATIONS = all_orientations()
IDENTITY = next(i for i, (_, R) in enumerate(ORIENTATIONS) if np.array_equal(R, np.eye(3)))


# ---------- what a window says about orientation ----------

STRATA = [0, 0.01, 0.05, 0.15, np.inf]     # movement (std of |a|, g): still, a little, moderate, a lot
GRID = None                                  # directions on the sphere, made once


def sphere_grid(n=200):
    """n directions spread evenly over the sphere (a Fibonacci lattice)."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


def window_summary(X):
    """(n, samples, 3) windows in g -> (unit gravity direction (n, 3), movement (n,)).

    WHY the movement level: it doesn't depend on orientation (it uses |a|), so it can group windows
    the same way on any device: still windows (desk, bed) and moving windows (arm swinging) point
    gravity in different, telling directions.
    """
    mean = X.mean(axis=1)
    return mean / np.linalg.norm(mean, axis=1, keepdims=True), np.linalg.norm(X, axis=2).std(axis=1)


def direction_profile(u, movement, kappa=10.0):
    """For each movement level: a smooth histogram of gravity directions over the sphere grid.

    WHY smooth (each window spreads a little weight to nearby grid points): with a few hours of data,
    a hard histogram is noisy.
    """
    global GRID
    if GRID is None:
        GRID = sphere_grid()
    profile, counts = [], []
    for lo, hi in zip(STRATA[:-1], STRATA[1:]):
        v = u[(movement >= lo) & (movement < hi)]
        counts.append(len(v))
        if len(v) == 0:
            profile.append(np.full(len(GRID), 1 / len(GRID)))
            continue
        w = np.exp(kappa * (v @ GRID.T - 1))           # (n, grid), 1 at the window's own direction
        w /= w.sum(axis=1, keepdims=True)
        profile.append(w.mean(axis=0))
    return np.array(profile), np.array(counts)


def profile_distance(p, q, counts, min_windows=20):
    """Hellinger distance per movement level, averaged over levels with enough windows."""
    keep = counts >= min_windows
    if not keep.any():                     # very little data: use every level that has any windows at all
        keep = counts > 0
    h = np.sqrt(0.5 * ((np.sqrt(p) - np.sqrt(q)) ** 2).sum(axis=1))
    return h[keep].mean()


def percentile_distance(u, ref_u):
    """The simpler check used for GOTOV in Stage 6: each axis's spread (10th to 90th percentile) vs the reference."""
    q = [10, 25, 50, 75, 90]
    return sum(abs(np.percentile(u[:, k], q) - np.percentile(ref_u[:, k], q)).sum() for k in range(3))


def rank_orientations(u, movement, reference, method='profile'):
    """Distance from the reference for every candidate orientation. -> array (48,), smaller = better match.

    reference: dict with 'profile' (from direction_profile on the reference data) and 'u' (reference directions).
    """
    out = np.zeros(len(ORIENTATIONS))
    for i, (_, R) in enumerate(ORIENTATIONS):
        v = u @ R.T
        if method == 'profile':
            p, counts = direction_profile(v, movement)
            out[i] = profile_distance(p, reference['profile'], counts)
        else:
            out[i] = percentile_distance(v, reference['u'])
    return out


def z_kept():
    """Indices of the 16 candidates where the model's z comes from the watch's z (either sign).

    WHY: a 10 s check (watch lying face up) shows which watch axis goes through the face. If that is z,
    as on most sensor boards, and Capture-24's z also goes through its device's face, only these 16 remain.
    """
    return [i for i, (_, R) in enumerate(ORIENTATIONS) if abs(R[2, 2]) == 1]


def choose_orientation(u, movement, reference, candidates=None):
    """-> (index of the best candidate, its matrix, all distances). candidates: list of indices (default all 48)."""
    d = rank_orientations(u, movement, reference)
    cands = list(range(len(ORIENTATIONS))) if candidates is None else list(candidates)
    best = min(cands, key=lambda i: d[i])
    return best, ORIENTATIONS[best][1], d


def save_reference(path, u, movement):
    """Save what the watch tool needs from Capture-24 (training people only), so it runs without Capture-24."""
    np.savez(path, profile=direction_profile(u, movement)[0], u=u.astype('float32'), strata=np.array(STRATA))


def load_reference(path):
    with np.load(path) as f:
        assert np.array_equal(f['strata'], np.array(STRATA))
        return {'profile': f['profile'], 'u': f['u']}


def rotate_features(F, R, feature_names):
    """The 16 Stage 1 features of rotated windows, worked out from the original features (no raw data needed).

    Magnitude and frequency features don't change. Axis means rotate, axis spreads swap places, and
    pitch and roll are recomputed from the rotated means.
    """
    i_mean = [feature_names.index(k) for k in ['x_mean', 'y_mean', 'z_mean']]
    i_std = [feature_names.index(k) for k in ['x_std', 'y_std', 'z_std']]
    G = F.copy()
    m = F[:, i_mean] @ R.T
    G[:, i_mean] = m
    G[:, i_std] = F[:, i_std] @ np.abs(R).T
    G[:, feature_names.index('pitch')] = np.degrees(np.arctan2(-m[:, 0], np.sqrt(m[:, 1] ** 2 + m[:, 2] ** 2)))
    G[:, feature_names.index('roll')] = np.degrees(np.arctan2(m[:, 1], m[:, 2]))
    return G


# ---------- calibration ----------

def still_blocks(X, seconds=10, hz=50, max_std=0.013):
    """Mean of every 10 s block where all three axes are still (std under 13 mg, the GGIR rule). -> (n, 3)."""
    n = seconds * hz
    blocks = X[:, :X.shape[1] // n * n].reshape(-1, n, 3) if X.ndim == 3 else X[:len(X) // n * n].reshape(-1, n, 3)
    still = (blocks.std(axis=1) < max_std).all(axis=1)
    return blocks[still].mean(axis=1)


def fit_calibration(points, iterations=100):
    """Per-axis offset and gain so that still readings lie on a sphere of 1 g. -> (offset (3,), gain (3,), report).

    corrected = (raw - offset) * gain. Needs still readings in several different orientations
    (the GGIR rule: each axis must see both above +0.3 g and below -0.3 g), otherwise offset and gain
    can't be told apart and the fit is refused.
    """
    points = np.asarray(points, dtype='float64')
    covered = all((points[:, k] > 0.3).any() and (points[:, k] < -0.3).any() for k in range(3))
    report = {'still blocks': len(points), 'all axes covered': covered,
              'error before (mg)': 1000 * np.abs(np.linalg.norm(points, axis=1) - 1).mean()}
    if not covered or len(points) < 6:              # WHY 6: the 6-position routine gives one still block per position
        report['used'] = False
        return np.zeros(3), np.ones(3), report
    offset, gain = np.zeros(3), np.ones(3)
    for _ in range(iterations):
        c = (points - offset) * gain
        target = c / np.linalg.norm(c, axis=1, keepdims=True)       # the nearest point on the 1 g sphere
        for k in range(3):                                          # straight-line fit: target = a * raw + b
            a, b = np.polyfit(points[:, k], target[:, k], 1)
            gain[k], offset[k] = a, -b / a
    report['error after (mg)'] = 1000 * np.abs(np.linalg.norm((points - offset) * gain, axis=1) - 1).mean()
    report['used'] = True
    return offset, gain, report


# ---------- the watch's own recordings ----------

def read_watch_csv(path):
    """A CSV saved by firmware/FinalPrototype/record.py. -> dict with:

    acc (n, 3) in g, NOT calibrated (record.py divides by 2048 counts per g and nothing else),
    index (n,) the firmware's sample_index (a jump = missing samples), micros (n,),
    label (the trial name record.py was given), firmware_scale (the firmware's single |a| correction, if logged),
    header (all '#' lines).

    Also reads a file of raw counts with no column names (then '# accel_lsb_per_g:' gives the counts per g).
    """
    import re

    import pandas as pd
    header, first_data = [], None
    with open(path) as f:
        for line in f:
            if line.startswith('#'):
                header.append(line.strip())
            elif line.strip():
                first_data = line.strip()
                break
    has_names = first_data is not None and first_data[0].isalpha()
    d = pd.read_csv(path, comment='#', header=0 if has_names else None)
    lsb = 2048.0
    for h in header:
        m = re.match(r'#\s*accel_lsb_per_g:\s*([0-9.]+)', h)
        if m:
            lsb = float(m.group(1))
    if has_names:
        # by column name, so both recorders work: record.py (ESP32-C3 + MPU-6050: sample_index,micros,ax_g,...)
        # and espwatch/capture_csv.py (Waveshare + QMI8658: sample_index,t_ms,label,ax_g,...)
        cols = {c.strip(): c for c in d.columns}
        acc = d[[cols['ax_g'], cols['ay_g'], cols['az_g']]].to_numpy('float64')
        micros = (d[cols['micros']] if 'micros' in cols else d[cols['t_ms']] * 1000).to_numpy('float64')
        values = np.column_stack([d[cols['sample_index']].to_numpy('float64'), micros])
    else:
        values = d.to_numpy('float64')
        acc = values[:, 2:5] / lsb
    label = next((h.split(':', 1)[1].strip() for h in header if h.startswith('# label:')), None)
    scale = None
    for h in header:
        m = re.search(r'scale ([0-9.]+)', h)
        if m and ('accel_rest_g' in h or 'SAVED calibration' in h):
            scale = float(m.group(1))
    return {'acc': acc, 'index': values[:, 0].astype(np.int64), 'micros': values[:, 1].astype(np.int64),
            'label': label, 'firmware_scale': scale, 'header': header}


def load_watch_csv(path):
    """Shortcut: -> (accel in g, not calibrated (n, 3), sample_index (n,))."""
    r = read_watch_csv(path)
    return r['acc'], r['index']


def watch_windows(acc, index, seconds=30, hz=50):
    """Cut a recording into 30 s windows, skipping any window with a missing sample (a gap in sample_index)."""
    n = seconds * hz
    out = []
    for start in range(0, len(acc) - n + 1, n):
        if index[start + n - 1] - index[start] == n - 1:
            out.append(acc[start:start + n])
    return np.array(out).reshape(-1, n, 3)
