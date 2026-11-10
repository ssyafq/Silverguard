"""Turns UCI-HAR, Capture-24 and GOTOV into one window table, using the same maths for all three.

It lives in a .py file (not the notebook) because Windows worker processes can only
run functions they can import. Results are cached per participant in data/processed/eda,
so a stopped run can resume and the notebook can reload them in seconds.
"""
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'Model based of UCI DataSet'))
from features import WIN, FEATURE_NAMES, mag_features   # the same 5 features the current models use

TARGET_HZ = 50
OPEN_DATA = PROJECT / 'data' / 'open datasets'
UCI_DIR = OPEN_DATA / 'UCI HAR Dataset'
C24_DIR = OPEN_DATA / 'capture24' / 'capture24'
GOTOV_DIR = OPEN_DATA / 'GOTOV Human Physical Activity and Energy Expenditure Dataset on Older Individuals_2_all'
CACHE = PROJECT / 'data' / 'processed' / 'eda'

# what each window is summarised into; only the magnitude and spread features are orientation-free
EXTRA_FEATURES = ['enmo', 'jerk_std', 'axes_std', 'dom_freq', 'band_0p5_3hz', 'spec_entropy',
                  'x_mean', 'y_mean', 'z_mean', 'tilt_z_deg']
ALL_FEATURES = FEATURE_NAMES + EXTRA_FEATURES

CLIP_G = 7.9   # both wrist devices are +/-8 g, so readings past this are at or near the limit
FREQS = np.fft.rfftfreq(WIN, 1 / TARGET_HZ)


def window_table(xyz):
    """xyz: array (n_windows, WIN, 3) at 50 Hz, in g. Returns a feature table and each window's power spectrum."""
    mag = np.sqrt((xyz ** 2).sum(axis=2))
    table = pd.DataFrame(mag_features(mag), columns=FEATURE_NAMES)

    table['enmo'] = np.maximum(mag - 1, 0).mean(axis=1)                  # the ENMO baseline in context.md
    table['jerk_std'] = np.diff(mag, axis=1).std(axis=1) * TARGET_HZ     # how sharply the magnitude changes (g/s)

    # A slow wrist turn moves gravity between axes but leaves the magnitude at 1 g,
    # so the magnitude can't see it. The spread of each axis can.
    table['axes_std'] = np.sqrt((xyz.std(axis=1) ** 2).sum(axis=1))

    spectrum = np.abs(np.fft.rfft(mag - mag.mean(axis=1, keepdims=True), axis=1)) ** 2
    psd = 2 * spectrum / (TARGET_HZ * WIN)                               # g^2/Hz
    power = spectrum[:, 1:]
    total = power.sum(axis=1)
    flat = total < 1e-12                                                 # a perfectly flat window has no spectrum
    share = power / np.where(flat, 1, total)[:, None]
    table['dom_freq'] = np.where(flat, 0, FREQS[1:][power.argmax(axis=1)])
    in_band = (FREQS[1:] >= 0.5) & (FREQS[1:] <= 3)                      # roughly the range of human steps
    table['band_0p5_3hz'] = share[:, in_band].sum(axis=1)
    entropy = -(share * np.log(np.where(share > 0, share, 1))).sum(axis=1) / np.log(power.shape[1])
    table['spec_entropy'] = np.where(flat, 0, entropy)

    means = xyz.mean(axis=1)
    table[['x_mean', 'y_mean', 'z_mean']] = means
    table['tilt_z_deg'] = np.degrees(np.arctan2(means[:, 2], np.hypot(means[:, 0], means[:, 1])))
    return table.astype('float32'), psd


def psd_rows(psd, labels, participant):
    """Sum of spectra per label, so a dataset average can weight every window equally later."""
    rows = []
    for label in pd.unique(labels):
        chosen = psd[np.asarray(labels) == label]
        rows.append({'participant_id': participant, 'label': label, 'n': len(chosen),
                     **{f'{f:.3f}': v for f, v in zip(FREQS, chosen.sum(axis=0))}})
    return rows


def save_parquet(df, path):
    """Write to a temp name first, so a crash can't leave a half-written file that looks finished."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    df.to_parquet(tmp, engine='pyarrow', index=False)
    os.replace(tmp, path)


def save_json(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


# ------------------------------------------------------------------ UCI-HAR
def build_uci(overwrite=False):
    """UCI needs no filtering or resampling: total_acc is already 50 Hz, low-pass filtered and in 128-sample windows."""
    out = CACHE / 'uci_windows.parquet'
    if out.exists() and not overwrite:
        return pd.read_parquet(out)

    def read(split, name):
        path = UCI_DIR / split / 'Inertial Signals' / f'{name}_{split}.txt'
        return pd.read_csv(path, sep=r'\s+', header=None).to_numpy()

    names = dict(pd.read_csv(UCI_DIR / 'activity_labels.txt', sep=r'\s+', header=None).to_numpy())
    tables, spectra = [], []
    for split in ['train', 'test']:
        acc = np.stack([read(split, f'total_acc_{a}') for a in 'xyz'], axis=2)
        gyro = np.stack([read(split, f'body_gyro_{a}') for a in 'xyz'], axis=2)
        table, psd = window_table(acc)
        gyro_mag = np.degrees(np.sqrt((gyro ** 2).sum(axis=2)))          # rad/s in the files, deg/s on our sensor
        table['gyro_mag_mean'] = gyro_mag.mean(axis=1).astype('float32')
        table['gyro_mag_std'] = gyro_mag.std(axis=1).astype('float32')
        table['acc_max_abs'] = np.abs(acc).max(axis=(1, 2)).astype('float32')
        table['label'] = pd.read_csv(UCI_DIR / split / f'y_{split}.txt', header=None)[0].map(names).to_numpy()
        table['participant_id'] = pd.read_csv(UCI_DIR / split / f'subject_{split}.txt', header=None)[0].to_numpy()
        table['split'] = split
        tables.append(table)
        spectra.append((psd, table['label'].to_numpy()))

    table = pd.concat(tables, ignore_index=True)
    save_parquet(table, out)
    rows = psd_rows(np.concatenate([s[0] for s in spectra]), np.concatenate([s[1] for s in spectra]), 'all')
    save_parquet(pd.DataFrame(rows), CACHE / 'uci_psd.parquet')
    return table


# ------------------------------------------------------------------ Capture-24
C24_HZ = 100
C24_FACTOR = C24_HZ // TARGET_HZ
C24_RAW_WIN = WIN * C24_FACTOR
C24_SOS = butter(4, 20, fs=C24_HZ, output='sos')                         # same 20 Hz low-pass as capture24_windows.ipynb


def build_capture24_participant(pid, overwrite=False):
    """One raw file -> one window parquet, one spectrum parquet and one summary. Same windowing rules as capture24_windows.ipynb."""
    folder = CACHE / 'capture24'
    summary_path = folder / f'{pid}.json'
    if summary_path.exists() and not overwrite:
        return pid

    df = pd.read_csv(C24_DIR / f'{pid}.csv.gz', usecols=['time', 'x', 'y', 'z', 'annotation'],
                     dtype={'x': float, 'y': float, 'z': float, 'annotation': 'category'},
                     parse_dates=['time'], date_format='ISO8601')
    if df[['time', 'x', 'y', 'z']].isna().any().any():
        raise ValueError(f'{pid}: NaN in time or x/y/z')

    dictionary = pd.read_csv(C24_DIR / 'annotation-label-dictionary.csv', index_col='annotation')
    categories = df['annotation'].cat.categories
    unknown = categories[~categories.isin(dictionary.index)].tolist()
    if unknown:
        raise ValueError(f'{pid}: annotations missing from the dictionary: {unknown}')
    meta = pd.read_csv(C24_DIR / 'metadata.csv', index_col='pid')

    codes = df['annotation'].cat.codes.to_numpy()                        # -1 = no annotation
    times = df['time'].to_numpy('datetime64[ns]').astype('int64')
    xyz = df[['x', 'y', 'z']].to_numpy()
    dt = np.diff(times)
    labelled = codes >= 0

    seconds = pd.Series(codes[labelled]).map(dict(enumerate(categories))).value_counts() / C24_HZ
    summary = {'participant_id': pid, 'rows': len(df), 'hours': len(df) / C24_HZ / 3600,
               'labelled_pct': 100 * labelled.mean(), 'median_dt_ms': float(np.median(dt)) / 1e6,
               'time_gaps': int((dt > 1.5e9 / C24_HZ).sum()), 'max_abs_g': float(np.abs(xyz).max()),
               'clipped_pct': float(100 * (np.abs(xyz) >= CLIP_G).any(axis=1).mean()),
               'annotation_seconds': seconds.to_dict()}
    del df

    # stretches that are labelled and have no time gap; a window never crosses a gap
    breaks = np.flatnonzero((labelled[1:] != labelled[:-1]) | (dt > 1.5e9 / C24_HZ) | (dt <= 0)) + 1
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(codes)]

    windows, win_codes, win_starts = [], [], []
    for s, e in zip(starts, ends):
        if not labelled[s] or e - s < C24_RAW_WIN:
            continue
        n_win = (e - s) // C24_RAW_WIN
        xyz_50 = sosfiltfilt(C24_SOS, xyz[s:e], axis=0)[::C24_FACTOR][:n_win * WIN]
        codes_win = codes[s:s + n_win * C24_RAW_WIN].reshape(n_win, C24_RAW_WIN)
        single = (codes_win == codes_win[:, [0]]).all(axis=1)            # one annotation for the whole window
        windows.append(xyz_50.reshape(n_win, WIN, 3)[single])
        win_codes.append(codes_win[single, 0])
        win_starts.append(times[s + np.flatnonzero(single) * C24_RAW_WIN])

    table, psd = window_table(np.concatenate(windows))
    annotation = categories[np.concatenate(win_codes)]
    table['label_specific'] = dictionary['label:WillettsSpecific2018'].reindex(annotation).to_numpy()
    table['label_walmsley'] = dictionary['label:Walmsley2020'].reindex(annotation).to_numpy()
    table['participant_id'] = pid
    table['age_bin'] = meta.loc[pid, 'age']
    table['sex'] = meta.loc[pid, 'sex']
    table['window_start'] = pd.to_datetime(np.concatenate(win_starts))
    summary['windows'] = len(table)

    save_parquet(table, folder / f'{pid}.parquet')
    save_parquet(pd.DataFrame(psd_rows(psd, table['label_specific'].to_numpy(), pid)), folder / f'{pid}_psd.parquet')
    save_json(summary, summary_path)                                     # written last: it marks this person as finished
    return pid


# ------------------------------------------------------------------ GOTOV
GOTOV_SOS_CUTOFF = 20


def gotov_wrist_members(archive):
    return sorted(n for n in archive.namelist() if n.endswith('GENEActiv-wrist.csv') and not n.startswith('__MACOSX'))


def build_gotov(overwrite=False):
    """Reads the wrist files straight from the zip. The clock isn't evenly spaced, so each bout is
    put on an even grid first, then filtered, then resampled to 50 Hz."""
    out = CACHE / 'gotov_windows.parquet'
    if out.exists() and not overwrite:
        return pd.read_parquet(out)

    tables, spectra, summaries, bouts = [], [], [], []
    with zipfile.ZipFile(GOTOV_DIR / 'Activity_Measurements.zip') as archive:
        for member in gotov_wrist_members(archive):
            pid = member.split('/')[1]
            df = pd.read_csv(archive.open(member), usecols=['time', 'x', 'y', 'z', 'labels'], low_memory=False)
            times = df['time'].to_numpy(float)
            xyz = df[['x', 'y', 'z']].to_numpy()
            labels = df['labels'].fillna('').to_numpy(str)
            dt = np.diff(times)
            fs = 1000 / dt.mean()
            sos = butter(4, GOTOV_SOS_CUTOFF, fs=fs, output='sos')

            summaries.append({'participant_id': pid, 'rows': len(df), 'hours': (times[-1] - times[0]) / 3.6e6,
                              'median_dt_ms': float(np.median(dt)), 'mean_dt_ms': float(dt.mean()), 'fs_from_mean_dt': fs,
                              'min_dt_ms': float(dt.min()), 'max_dt_ms': float(dt.max()), 'time_gaps': int((dt > 100).sum()),
                              'labelled_pct': 100 * (labels != '').mean(), 'nan_values': int(df[['x', 'y', 'z']].isna().sum().sum()),
                              'max_abs_g': float(np.abs(xyz).max()),
                              'clipped_pct': float(100 * (np.abs(xyz) >= CLIP_G).any(axis=1).mean()),
                              'xyz_md5': hashlib.md5(xyz.tobytes()).hexdigest()})

            changes = np.flatnonzero(labels[1:] != labels[:-1]) + 1
            windows, win_labels, win_starts = [], [], []
            for s, e in zip(np.r_[0, changes], np.r_[changes, len(labels)]):
                if labels[s] == '':
                    continue
                bout_seconds = (times[e - 1] - times[s]) / 1000
                bouts.append({'participant_id': pid, 'label': labels[s], 'seconds': bout_seconds})
                if bout_seconds < WIN / TARGET_HZ:
                    continue
                even_t = np.arange(times[s], times[e - 1], 1000 / fs)
                even = np.stack([np.interp(even_t, times[s:e], xyz[s:e, a]) for a in range(3)], axis=1)
                filtered = sosfiltfilt(sos, even, axis=0)
                t_50 = np.arange(times[s], times[e - 1], 1000 / TARGET_HZ)
                n_win = len(t_50) // WIN
                if n_win == 0:
                    continue
                xyz_50 = np.stack([np.interp(t_50, even_t, filtered[:, a]) for a in range(3)], axis=1)
                windows.append(xyz_50[:n_win * WIN].reshape(n_win, WIN, 3))
                win_labels.extend([labels[s]] * n_win)
                win_starts.append(t_50[::WIN][:n_win])

            table, psd = window_table(np.concatenate(windows))
            table['label'] = win_labels
            table['participant_id'] = pid
            table['window_start_ms'] = np.concatenate(win_starts)
            tables.append(table)
            spectra.extend(psd_rows(psd, np.array(win_labels), pid))

    table = pd.concat(tables, ignore_index=True)
    save_parquet(table, out)
    save_parquet(pd.DataFrame(spectra), CACHE / 'gotov_psd.parquet')
    save_parquet(pd.DataFrame(summaries), CACHE / 'gotov_summary.parquet')
    save_parquet(pd.DataFrame(bouts), CACHE / 'gotov_bouts.parquet')
    return table
