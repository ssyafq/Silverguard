"""Feature sets and the experiment behind feature_combinations.ipynb.

Question: does any accelerometer-only feature combination beat one cut-off on mag_std for moving vs still?

It lives in a .py file because Windows worker processes can only run functions they can import, and
because the long steps need to resume after a stop. Every long step caches its results, so the notebook
can re-run in minutes:

    build_capture24_person(pid)   raw file -> feature parquet per window length
    build_gotov()                 wrist files in the zip -> feature parquet per window length
    run_cv(target)                GroupKFold on the 140 training people only
    lock_candidates()             writes the chosen feature set to disk BEFORE any held-out data can load
    run_final(target)             fits on the 140, scores the 11 held-out people and GOTOV

The windowing copies capture24_windows.ipynb (Capture-24) and eda_features.build_gotov (GOTOV), because
those versions keep only summary features, not the signal. The notebook checks that this code reproduces them.
"""
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, roc_curve
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(HERE))    # eda_features.py sits in this folder
import eda_features as E

SEED = 42
TARGET_HZ = 50
RAW_HZ = 100
FACTOR = RAW_HZ // TARGET_HZ
LENGTHS = {'2.56s': 128, '5s': 250, '10s': 500}          # window name -> samples at 50 Hz
SECONDS = {name: n / TARGET_HZ for name, n in LENGTHS.items()}

DATA_DIR = PROJECT / 'data' / 'open datasets' / 'capture24' / 'capture24'
C24_CACHE = PROJECT / 'data' / 'processed' / 'capture24_windows' / 'feature_sets'
OLD_C24_DIR = PROJECT / 'data' / 'processed' / 'capture24_windows'
GOTOV_CACHE = PROJECT / 'data' / 'processed' / 'eda'
RESULTS = PROJECT / 'data' / 'processed' / 'feature_combinations'
TEST_LIST = HERE.parent / 'Model based of UCI DataSet' / 'capture24_test_participants.csv'
LOCK_FILE = RESULTS / 'locked_candidates.json'
FIRST_LOAD_FILE = RESULTS / 'held_out_first_loaded.json'

LABELS = ['sleep', 'sedentary', 'light', 'moderate-vigorous']
UNLABELLED = -1
ANTI_ALIAS = butter(4, 20, fs=RAW_HZ, output='sos')      # same 20 Hz low-pass as capture24_windows.ipynb

# ------------------------------------------------------------------ the feature groups
GROUP_1 = ['mag_std']
# Each value is relative to the window mean. Absolute min or percentiles would carry the sensor's
# calibration offset, which is why mag_mean and ENMO are excluded in the first place.
GROUP_2 = ['mag_min_rel', 'mag_max_rel', 'mag_p10_rel', 'mag_p25_rel', 'mag_p75_rel', 'mag_p90_rel', 'mag_range']
GROUP_3 = ['dom_freq', 'band_power_0p5_3hz', 'total_power', 'spec_entropy']
GROUP_4 = ['pitch_mean', 'pitch_std', 'roll_mean', 'roll_std']
BASE_FEATURES = GROUP_1 + GROUP_2 + GROUP_3 + GROUP_4
LITERAL_2 = ['mag_min', 'mag_max', 'mag_p10', 'mag_p25', 'mag_p75', 'mag_p90', 'mag_range']   # diagnostic only


def magnitude_features(xyz):
    """xyz: (n_windows, n_samples, 3) at 50 Hz, in g. Returns one row of features per window."""
    n = xyz.shape[1]
    mag = np.sqrt((xyz ** 2).sum(axis=2))
    mean = mag.mean(axis=1)
    table = {'mag_mean': mean, 'mag_std': mag.std(axis=1)}       # population std, same as features.py

    table['mag_min_rel'] = mag.min(axis=1) - mean
    table['mag_max_rel'] = mag.max(axis=1) - mean
    percentiles = np.percentile(mag, [10, 25, 75, 90], axis=1)
    for name, values in zip(['mag_p10_rel', 'mag_p25_rel', 'mag_p75_rel', 'mag_p90_rel'], percentiles):
        table[name] = values - mean
    table['mag_range'] = table['mag_max_rel'] - table['mag_min_rel']

    # frequency content of the magnitude. Scaled so total_power is close to mag_std squared at any window length.
    spectrum = np.abs(np.fft.rfft(mag - mean[:, None], axis=1)) ** 2
    power = 2 * spectrum[:, 1:] / n ** 2
    freqs = np.fft.rfftfreq(n, 1 / TARGET_HZ)[1:]
    total = power.sum(axis=1)
    flat = total < 1e-12                                         # a perfectly flat window has no spectrum
    share = power / np.where(flat, 1, total)[:, None]
    table['dom_freq'] = np.where(flat, 0, freqs[power.argmax(axis=1)])
    table['band_power_0p5_3hz'] = power[:, (freqs >= 0.5) & (freqs <= 3)].sum(axis=1)
    table['total_power'] = total
    entropy = -(share * np.log(np.where(share > 0, share, 1))).sum(axis=1) / np.log(power.shape[1])
    table['spec_entropy'] = np.where(flat, 0, entropy)

    # Wrist angle, per sample. Roll wraps round at +/-180 degrees, so it uses circular maths:
    # a wrist sitting at 180 would otherwise flip between +179 and -179 and look like huge movement.
    x, y, z = xyz[:, :, 0], xyz[:, :, 1], xyz[:, :, 2]
    pitch = np.arctan2(-x, np.hypot(y, z))
    roll = np.arctan2(y, z)
    table['pitch_mean'] = np.degrees(pitch.mean(axis=1))
    table['pitch_std'] = np.degrees(pitch.std(axis=1))
    sin_mean, cos_mean = np.sin(roll).mean(axis=1), np.cos(roll).mean(axis=1)
    table['roll_mean'] = np.degrees(np.arctan2(sin_mean, cos_mean))
    resultant = np.clip(np.hypot(sin_mean, cos_mean), 1e-12, 1)
    table['roll_std'] = np.degrees(np.sqrt(-2 * np.log(resultant)))
    return pd.DataFrame(table).astype('float32')


def features_for_length(xyz, name):
    """Features over the full window. The 10 s table also holds the last 5 s and last 2.56 s of each
    window, for the 'everything' set: that's what a device would have in its buffer at that moment."""
    table = magnitude_features(xyz)
    if name == '10s':
        for short in ['5s', '2.56s']:
            part = magnitude_features(xyz[:, -LENGTHS[short]:])[BASE_FEATURES]
            table = pd.concat([table, part.add_suffix(f'__{short}')], axis=1)
    return table


def with_literal_features(table):
    """Adds the absolute versions back (value + window mean), for the one diagnostic row."""
    table = table.copy()
    for literal, relative in zip(['mag_min', 'mag_max', 'mag_p10', 'mag_p25', 'mag_p75', 'mag_p90'], GROUP_2[:6]):
        table[literal] = table[relative] + table['mag_mean']
    return table


def _set(length, features, ladder, eligible=True):
    return {'length': length, 'features': list(features), 'ladder': ladder, 'eligible': eligible}


FEATURE_SETS = {}
for _name in LENGTHS:
    FEATURE_SETS[f'{_name}: 1'] = _set(_name, GROUP_1, '1 mag_std')
    FEATURE_SETS[f'{_name}: 1+2'] = _set(_name, GROUP_1 + GROUP_2, '+2 magnitude')
    FEATURE_SETS[f'{_name}: 1+2+3'] = _set(_name, GROUP_1 + GROUP_2 + GROUP_3, '+3 frequency')
    FEATURE_SETS[f'{_name}: 1+2+3+4'] = _set(_name, BASE_FEATURES, '+4 wrist angle')
FEATURE_SETS['2.56s: 1+3'] = _set('2.56s', GROUP_1 + GROUP_3, 'marginal')
FEATURE_SETS['2.56s: 1+4'] = _set('2.56s', GROUP_1 + GROUP_4, 'marginal')
FEATURE_SETS['2.56s: 1+2 literal (diagnostic)'] = _set('2.56s', GROUP_1 + LITERAL_2, 'diagnostic', eligible=False)
FEATURE_SETS['everything (multi-scale)'] = _set(
    '10s', BASE_FEATURES + [f'{f}__{s}' for s in ['5s', '2.56s'] for f in BASE_FEATURES], 'everything')
assert len(FEATURE_SETS) == 16


# ------------------------------------------------------------------ labels
def target_y(labels, target):
    """Capture-24 labels -> keep mask and y. 0 is the still side, 1 is the moving side."""
    labels = np.asarray(labels, dtype=object)
    if target == 'primary':      # moving vs still. Sleep is dropped, same as TO_BINARY in the old notebook
        keep = labels != 'sleep'
        return keep, (labels[keep] != 'sedentary').astype(np.int8)
    if target == 'secondary':    # very still (sleep) vs everything else
        return np.ones(len(labels), bool), (labels != 'sleep').astype(np.int8)
    raise ValueError(target)


GOTOV_SITTING = {'sittingChair', 'sittingSofa', 'sittingCouch'}
GOTOV_LYING = {'lyingDownLeft', 'lyingDownRight'}


def gotov_y(labels, target):
    """standing counts as moving, because all Capture-24 standing annotations are 'light' under Walmsley."""
    labels = pd.Series(labels).astype(str)
    if target == 'primary':
        return (~labels.isin(GOTOV_SITTING | GOTOV_LYING)).to_numpy(np.int8)
    if target == 'secondary':    # awake lying for about 3 minutes, a rough stand-in for sleep
        return (~labels.isin(GOTOV_LYING)).to_numpy(np.int8)
    raise ValueError(target)


# ------------------------------------------------------------------ Capture-24 windows
def find_runs(codes, times_ns):
    """(start, end) of each stretch that is labelled and has no time gap. Same as capture24_windows.ipynb."""
    period = 1e9 / RAW_HZ
    dt = np.diff(times_ns)
    labelled = codes != UNLABELLED
    breaks = np.flatnonzero((labelled[1:] != labelled[:-1]) | (dt > 1.5 * period) | (dt <= 0)) + 1
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(codes)]
    return [(s, e) for s, e in zip(starts, ends) if labelled[s]]


def cut_windows(times_ns, xyz, codes):
    """Raw 100 Hz arrays -> for each window length, the single-label windows: signal, label codes, start times."""
    out = {name: ([], [], []) for name in LENGTHS}
    for s, e in find_runs(codes, times_ns):
        if e - s < 2 * LENGTHS['2.56s']:
            continue
        xyz_50 = sosfiltfilt(ANTI_ALIAS, xyz[s:e], axis=0)[::FACTOR]
        for name, n in LENGTHS.items():
            raw_win = n * FACTOR
            n_win = (e - s) // raw_win
            if n_win == 0:
                continue
            # check the label at full 100 Hz, so even a one-sample label change is caught
            win_codes = codes[s:s + n_win * raw_win].reshape(n_win, raw_win)
            single = (win_codes == win_codes[:, [0]]).all(axis=1)
            signal, labels, starts = out[name]
            signal.append(xyz_50[:n_win * n].reshape(n_win, n, 3)[single])
            labels.append(win_codes[single, 0])
            starts.append(times_ns[s + np.flatnonzero(single) * raw_win])
    return {name: (np.concatenate(sig) if sig else np.empty((0, LENGTHS[name], 3)),
                   np.concatenate(lab) if lab else np.empty(0, np.int8),
                   np.concatenate(sta) if sta else np.empty(0, np.int64))
            for name, (sig, lab, sta) in out.items()}


def person_path(pid, name):
    return C24_CACHE / name / f'{pid}.parquet'


def build_capture24_person(pid, overwrite=False):
    """One raw file -> one parquet per window length. Finished lengths are skipped, so a stopped run can resume."""
    if not overwrite and all(person_path(pid, name).exists() for name in LENGTHS):
        return pid
    df = pd.read_csv(DATA_DIR / f'{pid}.csv.gz', usecols=['time', 'x', 'y', 'z', 'annotation'],
                     dtype={'x': float, 'y': float, 'z': float, 'annotation': 'category'},
                     parse_dates=['time'], date_format='ISO8601')
    if df[['time', 'x', 'y', 'z']].isna().any().any():
        raise ValueError(f'{pid}: NaN in time or x/y/z')

    label_map = pd.read_csv(DATA_DIR / 'annotation-label-dictionary.csv', index_col='annotation')['label:Walmsley2020']
    mapped = label_map.reindex(df['annotation'].cat.categories)
    unknown = mapped.index[~mapped.isin(LABELS)].tolist()
    if unknown:
        raise ValueError(f'{pid}: annotations with no Walmsley label: {unknown}')
    code_of_category = mapped.map({name: i for i, name in enumerate(LABELS)}).to_numpy(np.int8)
    raw = df['annotation'].cat.codes.to_numpy()
    codes = np.where(raw < 0, UNLABELLED, code_of_category[raw]).astype(np.int8)
    times_ns = df['time'].to_numpy('datetime64[ns]').astype('int64')
    xyz = df[['x', 'y', 'z']].to_numpy()
    del df

    age_bin = pd.read_csv(DATA_DIR / 'metadata.csv', index_col='pid').loc[pid, 'age']
    for name, (signal, labels, starts) in cut_windows(times_ns, xyz, codes).items():
        table = features_for_length(signal, name)
        table['label'] = np.array(LABELS, dtype=object)[labels]
        table['participant_id'] = pid
        table['age_bin'] = age_bin
        table['window_start'] = pd.to_datetime(starts)
        E.save_parquet(table, person_path(pid, name))
    return pid


def all_people():
    return sorted(pd.read_csv(DATA_DIR / 'metadata.csv')['pid'])


def test_people():
    ids = pd.read_csv(TEST_LIST)['participant_id'].tolist()
    assert len(ids) == 11
    return sorted(ids)


def training_people():
    return sorted(set(all_people()) - set(test_people()))


def _load_people(people, name, columns=None):
    frames = [pd.read_parquet(person_path(p, name), columns=columns) for p in people]
    table = pd.concat(frames, ignore_index=True)
    for col in ['participant_id', 'label', 'age_bin']:
        if col in table:
            table[col] = table[col].astype('category')
    return table


def load_training(name, columns=None):
    """The only way the experiment reads Capture-24 before the candidate is locked."""
    return _load_people(training_people(), name, columns)


def load_held_out(name, columns=None):
    """Refuses to run until the candidates are on disk, and records the first time it ran."""
    if not LOCK_FILE.exists():
        raise RuntimeError('The candidates must be locked before the held-out people are loaded.')
    if not FIRST_LOAD_FILE.exists():
        E.save_json({'first_loaded': time.time()}, FIRST_LOAD_FILE)
    return _load_people(test_people(), name, columns)


# ------------------------------------------------------------------ GOTOV windows
def gotov_path(name):
    return GOTOV_CACHE / f'gotov_feature_sets_{name}.parquet'


def build_gotov(overwrite=False):
    """Same bout logic as eda_features.build_gotov: put each bout on an even grid by timestamp, filter,
    then interpolate to 50 Hz. The clock is irregular, so the rate comes from the timestamps, not the docs."""
    if not overwrite and all(gotov_path(name).exists() for name in LENGTHS):
        return
    tables = {name: [] for name in LENGTHS}
    with zipfile.ZipFile(E.GOTOV_DIR / 'Activity_Measurements.zip') as archive:
        for member in E.gotov_wrist_members(archive):
            pid = member.split('/')[1]
            df = pd.read_csv(archive.open(member), usecols=['time', 'x', 'y', 'z', 'labels'], low_memory=False)
            times = df['time'].to_numpy(float)
            xyz = df[['x', 'y', 'z']].to_numpy()
            labels = df['labels'].fillna('').to_numpy(str)
            fs = 1000 / np.diff(times).mean()
            sos = butter(4, E.GOTOV_SOS_CUTOFF, fs=fs, output='sos')

            collected = {name: ([], [], []) for name in LENGTHS}
            changes = np.flatnonzero(labels[1:] != labels[:-1]) + 1
            for s, e in zip(np.r_[0, changes], np.r_[changes, len(labels)]):
                if labels[s] == '' or (times[e - 1] - times[s]) / 1000 < SECONDS['2.56s']:
                    continue
                even_t = np.arange(times[s], times[e - 1], 1000 / fs)
                even = np.stack([np.interp(even_t, times[s:e], xyz[s:e, a]) for a in range(3)], axis=1)
                filtered = sosfiltfilt(sos, even, axis=0)
                t_50 = np.arange(times[s], times[e - 1], 1000 / TARGET_HZ)
                xyz_50 = np.stack([np.interp(t_50, even_t, filtered[:, a]) for a in range(3)], axis=1)
                for name, n in LENGTHS.items():
                    n_win = len(t_50) // n
                    if n_win == 0:
                        continue
                    signal, win_labels, starts = collected[name]
                    signal.append(xyz_50[:n_win * n].reshape(n_win, n, 3))
                    win_labels.extend([labels[s]] * n_win)
                    starts.append(t_50[::n][:n_win])

            for name, (signal, win_labels, starts) in collected.items():
                table = features_for_length(np.concatenate(signal), name)
                table['label'] = win_labels
                table['participant_id'] = pid
                table['window_start_ms'] = np.concatenate(starts)
                tables[name].append(table)
    for name, parts in tables.items():
        E.save_parquet(pd.concat(parts, ignore_index=True), gotov_path(name))


def load_gotov(name, columns=None):
    if not LOCK_FILE.exists():
        raise RuntimeError('The candidates must be locked before GOTOV is scored.')
    if not FIRST_LOAD_FILE.exists():
        E.save_json({'first_loaded': time.time()}, FIRST_LOAD_FILE)
    return pd.read_parquet(gotov_path(name), columns=columns)


# ------------------------------------------------------------------ threshold baseline
def tune_threshold(values, y, rule='balanced'):
    """Best cut-off among the same 199 quantiles as the old notebook. Above the cut-off means moving.
    rule='balanced' is what the decision rule scores. rule='accuracy' is the old notebook's rule, kept for reference."""
    values, y = np.asarray(values, float), np.asarray(y)
    candidates = np.quantile(values, np.linspace(0.01, 0.99, 199))
    moving, still = np.sort(values[y == 1]), np.sort(values[y == 0])
    moving_caught = len(moving) - np.searchsorted(moving, candidates, side='right')
    still_caught = np.searchsorted(still, candidates, side='right')
    if rule == 'balanced':
        score = moving_caught / len(moving) + still_caught / len(still)
    elif rule == 'accuracy':
        score = moving_caught + still_caught
    else:
        raise ValueError(rule)
    return float(candidates[int(np.argmax(score))])


# ------------------------------------------------------------------ models
def make_forest():
    return RandomForestClassifier(n_estimators=20, max_depth=8, class_weight='balanced', random_state=SEED, n_jobs=-1)


def make_logistic():
    # class_weight='balanced' so it aims at the same score as the forest
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=500, class_weight='balanced', random_state=SEED))


def people_folds():
    """Who is in which fold. Set once from the 2.56 s table, so every feature set uses the same split."""
    table = load_training('2.56s', columns=['participant_id'])
    groups = table['participant_id'].astype(str).to_numpy()
    folds = {}
    for fold, (_, val) in enumerate(GroupKFold(n_splits=5).split(groups, groups=groups)):
        for pid in np.unique(groups[val]):
            folds[pid] = fold
    assert set(folds) == set(training_people()) and not set(folds) & set(test_people())
    return folds


def _training_table(name):
    table = load_training(name)
    return with_literal_features(table) if name == '2.56s' else table


def run_cv(target, log=print):
    """5-fold CV by participant on the 140 training people. Each finished row is saved, so a stopped run can resume."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f'cv_{target}.parquet'
    rows = pd.read_parquet(out).to_dict('records') if out.exists() else []
    done = {(r['feature_set'], r['model'], r['fold']) for r in rows}
    folds = people_folds()

    for name in LENGTHS:
        table = _training_table(name)
        keep, y = target_y(table['label'], target)
        table = table[keep].reset_index(drop=True)
        fold_of = table['participant_id'].astype(str).map(folds).to_numpy()
        assert not set(table['participant_id'].astype(str)) & set(test_people())

        for fold in range(5):
            val = fold_of == fold
            assert not set(table.loc[val, 'participant_id'].astype(str)) & set(table.loc[~val, 'participant_id'].astype(str))
            key = (f'{name}: threshold', 'threshold', fold)
            if key not in done:
                x = table['mag_std'].to_numpy()
                cut = tune_threshold(x[~val], y[~val])
                rows.append({'feature_set': key[0], 'model': 'threshold', 'fold': fold, 'n_features': 1,
                             'balanced_accuracy': balanced_accuracy_score(y[val], x[val] > cut),
                             'auc': roc_auc_score(y[val], x[val]), 'seconds': 0.0})
                done.add(key)

        for set_name, spec in FEATURE_SETS.items():
            if spec['length'] != name:
                continue
            X = None
            for fold in range(5):
                val = fold_of == fold
                for model_name, maker in [('forest', make_forest), ('logistic', make_logistic)]:
                    if (set_name, model_name, fold) in done:
                        continue
                    if X is None:
                        X = table[spec['features']].to_numpy(np.float32)
                    t0 = time.time()
                    model = maker().fit(X[~val], y[~val])
                    prob = model.predict_proba(X[val])[:, 1]
                    rows.append({'feature_set': set_name, 'model': model_name, 'fold': fold, 'n_features': len(spec['features']),
                                 'balanced_accuracy': balanced_accuracy_score(y[val], prob >= 0.5),
                                 'auc': roc_auc_score(y[val], prob), 'seconds': time.time() - t0})
                    E.save_parquet(pd.DataFrame(rows), out)
                    log(f'{target} | {set_name} | {model_name} | fold {fold} | {rows[-1]["balanced_accuracy"]:.4f} | {rows[-1]["seconds"]:.0f} s')
        E.save_parquet(pd.DataFrame(rows), out)
        del table
    return pd.DataFrame(rows)


def cv_summary(target):
    cv = pd.read_parquet(RESULTS / f'cv_{target}.parquet')
    return (cv.groupby(['feature_set', 'model'])
            .agg(balanced_accuracy=('balanced_accuracy', 'mean'), fold_sd=('balanced_accuracy', 'std'), auc=('auc', 'mean'),
                 n_features=('n_features', 'first'), folds=('fold', 'nunique')).reset_index())


def choose_candidate(summary):
    """Best forest in CV. Within 0.5 points of the best, the fewest features wins, then the higher score."""
    forests = summary[(summary['model'] == 'forest')
                      & summary['feature_set'].map(lambda s: FEATURE_SETS[s]['eligible'] if s in FEATURE_SETS else False)]
    best = forests['balanced_accuracy'].max()
    close = forests[forests['balanced_accuracy'] >= best - 0.005]
    return close.sort_values(['n_features', 'balanced_accuracy'], ascending=[True, False]).iloc[0]['feature_set']


def lock_candidates():
    """Written once. The candidates can't be changed after this without deleting the file."""
    if LOCK_FILE.exists():
        return json.loads(LOCK_FILE.read_text())
    if FIRST_LOAD_FILE.exists():
        raise RuntimeError('Held-out data was already loaded, so a new lock would not be blind.')
    locked = {'locked_at': time.time()}
    for target in ['primary', 'secondary']:
        summary = cv_summary(target)
        assert (summary['folds'] == 5).all() and len(summary) == 2 * len(FEATURE_SETS) + len(LENGTHS)
        locked[target] = choose_candidate(summary)
    E.save_json(locked, LOCK_FILE)
    return locked


# ------------------------------------------------------------------ final fits
def run_final(target, log=print):
    """Fits every set on all 140 people and saves window predictions for the 11 and GOTOV.
    Only the locked candidate counts for the rule. The other sets' scores are description."""
    locked = lock_candidates()
    out_dir = RESULTS / f'final_{target}'
    out_dir.mkdir(parents=True, exist_ok=True)
    info_path = out_dir / 'models.json'
    info = json.loads(info_path.read_text()) if info_path.exists() else {'thresholds': {}, 'forests': {}}

    for name in LENGTHS:
        pred_path = out_dir / f'predictions_{name}.parquet'
        sets_here = {k: v for k, v in FEATURE_SETS.items() if v['length'] == name}
        if pred_path.exists():
            continue
        train = _training_table(name)
        keep, y = target_y(train['label'], target)
        train = train[keep].reset_index(drop=True)
        assert not set(train['participant_id'].astype(str)) & set(test_people())

        held_out = load_held_out(name)
        keep_h, y_h = target_y(held_out['label'], target)
        held_out = held_out[keep_h].reset_index(drop=True)
        gotov = load_gotov(name)
        y_g = gotov_y(gotov['label'], target)
        if name == '2.56s':
            held_out, gotov = with_literal_features(held_out), with_literal_features(gotov)

        cut = tune_threshold(train['mag_std'], y)
        old_cut = tune_threshold(train['mag_std'], y, rule='accuracy')
        info['thresholds'][name] = {'balanced_rule': cut, 'old_accuracy_rule': old_cut}

        columns = ['mag_std']
        for frame, frame_y, dataset in [(held_out, y_h, 'held_out'), (gotov, y_g, 'gotov')]:
            frame['y'] = frame_y
            frame['dataset'] = dataset
            frame['threshold_pred'] = (frame['mag_std'] > cut).astype(np.int8)
            frame['old_threshold_pred'] = (frame['mag_std'] > old_cut).astype(np.int8)
        for set_name, spec in sets_here.items():
            X = train[spec['features']].to_numpy(np.float32)
            forest = make_forest().fit(X, y)
            logistic = make_logistic().fit(X, y)
            info['forests'][set_name] = {
                'importances': dict(zip(spec['features'], forest.feature_importances_.tolist())),
                'nodes': int(sum(t.tree_.node_count for t in forest.estimators_)),
                'n_features': len(spec['features'])}
            for frame in [held_out, gotov]:
                X_f = frame[spec['features']].to_numpy(np.float32)
                frame[f'forest::{set_name}'] = forest.predict_proba(X_f)[:, 1].astype('float32')
                frame[f'logistic::{set_name}'] = logistic.predict_proba(X_f)[:, 1].astype('float32')
            log(f'{target} | final | {set_name}')
            del X
        keep_cols = ['dataset', 'participant_id', 'label', 'y', 'mag_std', 'threshold_pred', 'old_threshold_pred'] + \
            [c for c in held_out.columns if c.startswith(('forest::', 'logistic::'))]
        held_part = held_out[keep_cols + ['age_bin', 'window_start']].copy()
        gotov_part = gotov[keep_cols + ['window_start_ms']].copy()
        gotov_part['window_start'] = pd.to_datetime(gotov_part.pop('window_start_ms'), unit='ms')
        gotov_part['age_bin'] = 'GOTOV'
        for part in [held_part, gotov_part]:
            for col in ['dataset', 'participant_id', 'label', 'age_bin']:
                part[col] = part[col].astype(str)
        E.save_parquet(pd.concat([held_part, gotov_part], ignore_index=True), pred_path)
        E.save_json(info, info_path)
        del train, held_out, gotov, held_part, gotov_part
    return info


def load_predictions(target, name):
    return pd.read_parquet(RESULTS / f'final_{target}' / f'predictions_{name}.parquet')


def load_model_info(target):
    return json.loads((RESULTS / f'final_{target}' / 'models.json').read_text())


# ------------------------------------------------------------------ scoring
def rates(y, pred):
    y, pred = np.asarray(y).astype(bool), np.asarray(pred).astype(bool)
    return {'moving_recall': pred[y].mean(), 'still_recall': (~pred[~y]).mean()}


def inside_longer(short, long, long_seconds):
    """Mask over the 2.56 s rows: True if the whole window lies inside a kept longer window of the same person.
    Longer windows are dropped more often for mixed labels, so this scores the baseline on the same stretches."""
    mask = np.zeros(len(short), bool)
    short_len = np.timedelta64(int(SECONDS['2.56s'] * 1e9), 'ns')
    long_len = np.timedelta64(int(long_seconds * 1e9), 'ns')
    short_people = short['participant_id'].to_numpy()
    long_people = long['participant_id'].to_numpy()
    for key in ['held_out', 'gotov']:
        s_rows = short['dataset'].to_numpy() == key
        l_rows = long['dataset'].to_numpy() == key
        for pid in np.unique(short_people[s_rows]):
            s_idx = np.flatnonzero(s_rows & (short_people == pid))
            lt = np.sort(long['window_start'].to_numpy()[l_rows & (long_people == pid)])
            if len(lt) == 0:
                continue
            st = short['window_start'].to_numpy()[s_idx]
            before = np.searchsorted(lt, st, side='right') - 1
            found = before >= 0
            ok = np.zeros(len(st), bool)
            ok[found] = st[found] + short_len <= lt[before[found]] + long_len
            mask[s_idx] = ok
    return mask


def bootstrap_gain(y_a, pred_a, people_a, y_b, pred_b, people_b, n=2000, seed=SEED):
    """95% CI of (balanced accuracy of A - balanced accuracy of B), resampling whole people."""
    def counts(y, pred, people):
        frame = pd.DataFrame({'p': np.asarray(people), 'y': np.asarray(y).astype(bool), 'pred': np.asarray(pred).astype(bool)})
        frame['tp'] = frame['y'] & frame['pred']
        frame['tn'] = ~frame['y'] & ~frame['pred']
        frame['neg'] = ~frame['y']
        return frame.groupby('p')[['tp', 'tn', 'y', 'neg']].sum()
    a, b = counts(y_a, pred_a, people_a), counts(y_b, pred_b, people_b)
    ids = a.index.intersection(b.index)
    a, b = a.loc[ids].to_numpy(float), b.loc[ids].to_numpy(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ids), size=(n, len(ids)))

    def balanced(c):
        s = c[pick].sum(axis=1)
        with np.errstate(invalid='ignore', divide='ignore'):
            return (s[:, 0] / s[:, 2] + s[:, 1] / s[:, 3]) / 2
    gains = balanced(a) - balanced(b)
    return np.nanpercentile(gains, [2.5, 97.5])


# Block-level scoring, same logic as make_blocks and separate in capture24_windows.ipynb Part 3
BLOCK_MINUTES = [10, 30]
MIN_COVERAGE = 0.4
STILL_BELOW = 0.1
ACTIVE_ABOVE = 0.3
MIN_BLOCKS_TO_SCORE = 10     # with fewer blocks of a kind than this, a rate means very little


def make_blocks(windows, methods, minutes, window_seconds):
    start = windows['window_start'].dt.floor(f'{minutes}min').rename('block_start')
    blocks = windows.groupby(['participant_id', start], observed=True).agg(
        n=('y', 'size'), actual=('y', 'mean'), **{m: (m, 'mean') for m in methods}).reset_index()
    blocks = blocks[blocks['n'] * window_seconds / (minutes * 60) >= MIN_COVERAGE].reset_index(drop=True)
    blocks['kind'] = np.select([blocks['actual'] < STILL_BELOW, blocks['actual'] > ACTIVE_ABOVE], ['still', 'active'], 'in between')
    return blocks


def separate(blocks, method):
    """AUC, the best threshold on predicted, and what happens at it. The threshold is picked on these same blocks, so this is a best case."""
    two = blocks[blocks['kind'].isin(['still', 'active'])]
    is_still = (two['kind'] == 'still').to_numpy()
    pred = two[method].to_numpy()
    fpr, tpr, thr = roc_curve(is_still, -pred, drop_intermediate=False)
    finite = np.isfinite(thr)
    best = np.flatnonzero(finite)[np.argmax((tpr - fpr)[finite])]
    threshold = -thr[best]
    flagged = pred <= threshold
    return {'AUC': roc_auc_score(is_still, -pred), 'threshold': threshold,
            'false-alert rate': flagged[~is_still].mean(), 'still-detection rate': flagged[is_still].mean(),
            'active blocks': int((~is_still).sum()), 'still blocks': int(is_still.sum())}


def block_scores(windows, methods, window_seconds):
    """Pooled and 'new person' block scores. The new-person threshold comes from everyone else in the same group."""
    rows = []
    for minutes in BLOCK_MINUTES:
        blocks = make_blocks(windows, methods, minutes, window_seconds)
        two = blocks[blocks['kind'].isin(['still', 'active'])]
        n_still, n_active = (two['kind'] == 'still').sum(), (two['kind'] == 'active').sum()
        for m in methods:
            row = {'method': m, 'block_min': minutes, 'still blocks': int(n_still), 'active blocks': int(n_active)}
            if min(n_still, n_active) < MIN_BLOCKS_TO_SCORE:
                row.update({'AUC': np.nan, 'false-alert rate': np.nan, 'still-detection rate': np.nan,
                            'new person: false-alert rate': np.nan, 'new person: still detection': np.nan, 'scored': False})
            else:
                pooled = separate(blocks, m)
                false_alerts = caught = 0
                for pid in two['participant_id'].unique():
                    rest, one = two[two['participant_id'] != pid], two[two['participant_id'] == pid]
                    if rest['kind'].nunique() < 2:
                        continue
                    t = separate(rest, m)['threshold']
                    false_alerts += (one.loc[one['kind'] == 'active', m] <= t).sum()
                    caught += (one.loc[one['kind'] == 'still', m] <= t).sum()
                row.update({'AUC': pooled['AUC'], 'false-alert rate': pooled['false-alert rate'],
                            'still-detection rate': pooled['still-detection rate'],
                            'new person: false-alert rate': false_alerts / n_active, 'new person: still detection': caught / n_still,
                            'scored': True})
            rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ ESP32-C3 cost estimate
# Rough software floating-point cycles on a CPU with no FPU. These are assumptions, not measurements.
CYCLES = {'add': 150, 'mul': 150, 'div': 500, 'sqrt': 800, 'cmp': 50, 'atan2': 5000, 'sincos': 6000, 'log': 4000}
CPU_HZ = 160e6
LIMITS = {'cpu_pct': 5.0, 'ram_kb': 32.0, 'flash_kb': 256.0}
NODE_BYTES = 12              # feature index, threshold, child index, leaf value
HOP_FRACTION = 0.5           # context.md uses 50% overlap on the device, the harder case for the CPU


def _group_cycles(n):
    c = CYCLES
    log_n = np.log2(n)
    magnitude = n * (3 * c['mul'] + 2 * c['add'] + c['sqrt'])
    group_1 = n * (2 * c['add'] + c['mul']) + c['div'] + c['sqrt']
    group_2 = n * log_n * c['cmp'] + 7 * c['add']                               # a sort, then read off the values
    fft = (n / 2) * log_n * (4 * c['mul'] + 6 * c['add'])
    group_3 = fft + (n / 2) * (2 * c['mul'] + c['add'] + c['log'] + c['div'])
    group_4 = n * (2 * c['atan2'] + c['sqrt'] + c['mul'] + c['add'] + c['sincos']) + 4 * c['sqrt']
    return {'magnitude': magnitude, 1: group_1, 2: group_2, 3: group_3, 4: group_4}


def groups_in(features):
    found = {1}
    for f in features:
        base = f.split('__')[0]
        if base in GROUP_2 or base in LITERAL_2:
            found.add(2)
        elif base in GROUP_3:
            found.add(3)
        elif base in GROUP_4:
            found.add(4)
    return found


def estimate_cost(set_name, nodes):
    """Estimated CPU share, RAM and flash for one feature set with a fitted forest of `nodes` nodes."""
    spec = FEATURE_SETS[set_name]
    n = LENGTHS[spec['length']]
    groups = groups_in(spec['features'])
    multi = set_name.startswith('everything')
    scales = [500, 250, 128] if multi else [n]
    cycles = _group_cycles(n)['magnitude']
    for scale in scales:
        table = _group_cycles(scale)
        cycles += sum(table[g] for g in groups)
    cycles += 20 * 8 * CYCLES['cmp']                                            # 20 trees, up to 8 comparisons each
    budget = CPU_HZ * n / TARGET_HZ * HOP_FRACTION
    ram = n * 3 * 2 + n * 4                                                     # int16 x, y, z and float magnitude
    if 2 in groups:
        ram += n * 4                                                            # copy to sort
    if 3 in groups:
        ram += n * 8                                                            # complex FFT buffer
    result = {'cpu_pct': 100 * cycles / budget, 'ram_kb': ram / 1024, 'flash_kb': nodes * NODE_BYTES / 1024}
    result['passes'] = all(result[k] <= LIMITS[k] for k in LIMITS)
    return result


# ------------------------------------------------------------------ self-test
def self_test():
    """Known answers on fake signals, so a logic bug shows up here rather than in the results."""
    n = 128
    t = np.arange(n) / TARGET_HZ

    # 1 g stays 1 g, with no spread and no spectrum
    still = np.tile([0.0, 0.0, 1.0], (1, n, 1))
    table = magnitude_features(still)
    assert np.isclose(table['mag_mean'][0], 1) and table['mag_std'][0] < 1e-6 and table['total_power'][0] < 1e-6
    assert table['dom_freq'][0] == 0 and table['spec_entropy'][0] == 0

    # a 2 Hz wobble: right frequency, nearly all power in the band, total power close to the variance
    wobble = np.zeros((1, n, 3))
    wobble[0, :, 2] = 1 + 0.2 * np.sin(2 * np.pi * (100 / 256 * 5) * t)          # 5 bins up = 1.953 Hz, exactly on a bin
    table = magnitude_features(wobble)
    assert abs(table['dom_freq'][0] - 100 / 256 * 5) < 1e-3
    assert table['band_power_0p5_3hz'][0] / table['total_power'][0] > 0.99
    assert np.isclose(table['total_power'][0], table['mag_std'][0] ** 2, rtol=1e-3)
    assert table['spec_entropy'][0] < 0.05

    # group 2 ignores a calibration offset: scaling z by a small constant level shift barely moves it
    noise = np.random.default_rng(SEED).normal(0, 0.05, (1, n, 3))
    base = noise + [0, 0, 1]
    shifted = noise + [0, 0, 1.03]
    a, b = magnitude_features(base), magnitude_features(shifted)
    assert abs(a['mag_mean'][0] - b['mag_mean'][0]) > 0.02
    assert all(abs(a[f][0] - b[f][0]) < 0.005 for f in GROUP_2)

    # roll crossing +/-180 degrees must not look like movement
    angle = np.radians(180 + 2 * np.sin(2 * np.pi * t))
    crossing = np.stack([np.zeros(n), np.sin(angle), np.cos(angle)], axis=1)[None]
    table = magnitude_features(crossing)
    assert table['roll_std'][0] < 3 and abs(abs(table['roll_mean'][0]) - 180) < 1
    pitch_up = np.tile([-np.sin(np.radians(30)), 0, np.cos(np.radians(30))], (1, n, 1))
    assert np.isclose(magnitude_features(pitch_up)['pitch_mean'][0], 30, atol=1e-3)

    # windowing: gaps and label changes, at 100 Hz
    raw = 256

    def fake(count):
        return np.arange(count) * 10_000_000, np.tile([0.0, 0.0, 1.0], (count, 1)), np.zeros(count, np.int8)
    times, xyz, codes = fake(raw * 3 + 10)
    cut = cut_windows(times, xyz, codes)
    assert len(cut['2.56s'][0]) == 3 and np.allclose(np.sqrt((cut['2.56s'][0] ** 2).sum(axis=2)), 1, atol=1e-6)
    assert (cut['2.56s'][2] == times[[0, raw, 2 * raw]]).all()
    assert len(cut['5s'][0]) == 1 and len(cut['10s'][0]) == 0

    gap = codes.copy()
    gap[raw + 100:raw + 200] = UNLABELLED
    assert len(cut_windows(times, xyz, gap)['2.56s'][0]) == 2

    jump = times.copy()
    jump[raw + 50:] += 3_600_000_000_000
    assert len(cut_windows(jump, xyz, codes)['2.56s'][0]) == 2

    mixed = codes.copy()
    mixed[raw * 2 - 1] = 1
    kept = cut_windows(times, xyz, mixed)['2.56s']
    assert len(kept[0]) == 2 and (kept[2] == times[[0, raw * 2]]).all()

    # thresholds: a clean split is found, whichever rule is used
    values = np.r_[np.linspace(0, 1, 500), np.linspace(2, 3, 100)]
    labels = np.r_[np.zeros(500, int), np.ones(100, int)]
    for rule in ['balanced', 'accuracy']:
        cut_off = tune_threshold(values, labels, rule)
        assert 0.95 <= cut_off < 2.05

    # the matched-window mask
    base_time = pd.Timestamp('2020-01-01')
    short = pd.DataFrame({'dataset': 'held_out', 'participant_id': 'A',
                          'window_start': [base_time + pd.Timedelta(seconds=s) for s in [0, 2.56, 5.12, 7.68, 10.24]]})
    long = pd.DataFrame({'dataset': 'held_out', 'participant_id': ['A'], 'window_start': [base_time]})
    assert inside_longer(short, long, 10).tolist() == [True, True, True, False, False]

    # labels
    keep, y = target_y(['sleep', 'sedentary', 'light', 'moderate-vigorous'], 'primary')
    assert keep.tolist() == [False, True, True, True] and y.tolist() == [0, 1, 1]
    keep, y = target_y(['sleep', 'sedentary', 'light'], 'secondary')
    assert keep.all() and y.tolist() == [0, 1, 1]
    assert gotov_y(['sittingSofa', 'standing', 'lyingDownLeft', 'cycling'], 'primary').tolist() == [0, 1, 0, 1]
    assert gotov_y(['sittingSofa', 'standing', 'lyingDownLeft', 'cycling'], 'secondary').tolist() == [1, 1, 0, 1]
    return 'all checks passed'
