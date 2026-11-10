"""Train the small on-device fall forest, export it and the HAR forest to C headers for the ESP32.

Run from this folder:   python export_models_to_c.py
Writes into ../../firmware/silverguard_logger/:
    fall_model.h    two-stage fall model (stage 2 forest + threshold + feature order)
    har_model.h     still / moving forest (rf_mag_uci.joblib, 5 magnitude features)
    test_vectors.h  a few real windows + the answers Python expects, for the on-device self-test

Note on scores: emlearn's C forest returns the FRACTION OF TREES voting each class.
scikit-learn's predict_proba averages leaf probabilities instead, which is a different number.
So everything here (threshold choice, evaluation, test vectors) uses the tree-vote fraction,
exactly what the ESP32 will compute.
"""
from pathlib import Path

import emlearn
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold

import fad_utils as fu

OUT = Path('../../firmware/silverguard_logger')
HAR_JOBLIB = Path('../Model based of UCI DataSet/rf_mag_uci.joblib')
HAR_FEATURES = ['mag_mean', 'mag_std', 'mag_min', 'mag_max', 'mag_range']   # order from features.py
HAR_WIN = 128
TRIGGER_G = 2.5
TEST_SUBJECTS = [1, 11, 12]
MIN_RECALL = 0.90
SEED = 42
SMALL = dict(n_estimators=30, max_depth=8, min_samples_leaf=5)   # small enough for the ESP32-C3
BIG = dict(n_estimators=300, min_samples_leaf=3)                  # the notebook version, for comparison


def vote_fraction(rf, X):
    """Fraction of trees voting 'fall' = what emlearn's *_predict_proba returns on the device."""
    X = np.asarray(X, dtype=np.float32)
    return np.mean([est.predict(X) for est in rf.estimators_], axis=0)


def new_rf(params):
    return RandomForestClassifier(class_weight='balanced_subsample', n_jobs=-1, random_state=SEED, **params)


def pick_threshold(X, y, groups, trig, params):
    """Grouped CV on triggered windows; highest threshold that keeps overall fall recall >= 90%."""
    Xs, ys, gs = X[trig], y[trig], groups[trig]
    score = np.zeros(len(ys))
    for tr, va in GroupKFold(5).split(Xs, ys, gs):
        score[va] = vote_fraction(new_rf(params).fit(Xs[tr], ys[tr]), Xs[va])
    need = min(MIN_RECALL * y.sum() / ys.sum(), 1.0)       # recall needed inside stage 2
    for t in np.unique(score)[::-1]:                         # try the strictest threshold first
        if (score[ys == 1] >= t).mean() >= need:
            return float(t)
    return 0.0


def evaluate(name, pred, y, meta):
    tp, fn = int(((pred == 1) & (y == 1)).sum()), int(((pred == 0) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    hours = (y == 0).sum() * fu.HOP / fu.FS_OUT / 3600
    caught = meta.assign(p=pred)[y == 1].groupby('rec').p.max()
    print(f'  {name:28s} recall {tp / (tp + fn):.3f} | false alarms/h {fp / hours:6.1f} | fall recordings caught {int(caught.sum())}/{len(caught)}')


def c_array(name, values, per_line=10):
    def lit(v):                                   # float literal C++ accepts: '1.0f' not '1f'; 9 digits = exact float32
        t = f'{float(np.float32(v)):.9g}'
        return (t if ('.' in t or 'e' in t or 'n' in t) else t + '.0') + 'f'
    vals = [lit(v) for v in np.asarray(values).ravel()]
    lines = [', '.join(vals[i:i + per_line]) for i in range(0, len(vals), per_line)]
    return f'static const float {name}[{len(vals)}] = {{\n  ' + ',\n  '.join(lines) + '\n};\n'


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # ---------------- data ----------------
    print('loading FallAllD wrist (D2)...')
    trials, ACC, GYR = fu.load_device()
    win, wa, wg = fu.make_windows(ACC, GYR, trials)[:3]
    Xdf = fu.make_features(wa, wg)
    cols = fu.feature_sets(Xdf.columns)['accel + gyro (magnitude)']
    X = Xdf[cols].to_numpy(dtype=np.float32)
    y = win.y.to_numpy()
    groups = win.subject.to_numpy()
    trig = Xdf['am_max'].to_numpy() > TRIGGER_G
    print(f'{len(y):,} windows, {y.sum()} fall windows, {trig.sum():,} pass the {TRIGGER_G} g trigger')
    print('feature order:', cols)

    # ---------------- held-out check: small vs big forest ----------------
    is_test = np.isin(groups, TEST_SUBJECTS)
    tr = ~is_test
    print(f'\nheld-out people {TEST_SUBJECTS}:')
    evaluate(f'rule: peak > {TRIGGER_G} g', trig[is_test].astype(int), y[is_test], win[is_test])
    for name, params in [('big forest (300 trees)', BIG), ('small forest (30 trees, d8)', SMALL)]:
        thr = pick_threshold(X[tr], y[tr], groups[tr], trig[tr], params)
        rf = new_rf(params).fit(X[tr & trig], y[tr & trig])
        pred = np.zeros(is_test.sum(), dtype=int)
        t_te = trig[is_test]
        pred[t_te] = (vote_fraction(rf, X[is_test][t_te]) >= thr).astype(int)
        evaluate(f'{name} thr={thr:.2f}', pred, y[is_test], win[is_test])

    # ---------------- final model on all 13 people ----------------
    thr = pick_threshold(X, y, groups, trig, SMALL)
    fall_rf = new_rf(SMALL).fit(X[trig], y[trig])
    n_nodes = sum(e.tree_.node_count for e in fall_rf.estimators_)
    print(f'\nfinal fall forest: {len(fall_rf.estimators_)} trees, {n_nodes} nodes, threshold {thr:.3f}')

    c = emlearn.convert(fall_rf, method='inline', dtype='float')
    c.save(file=str(OUT / 'fall_model_trees.h'), name='fall_model')
    order = '\n'.join(f'//   [{i:2d}] {n}' for i, n in enumerate(cols))
    (OUT / 'fall_model.h').write_text(
        '// Generated by notebooks/fall model/export_models_to_c.py - do not edit by hand.\n'
        '// Two-stage fall model trained on FallAllD wrist (D2), all 13 people, 50 Hz, accel in g, gyro in deg/s.\n'
        f'// Stage 1: peak |accel| in the window > {TRIGGER_G} g.  Stage 2: forest below.\n'
        '// Score = fraction of trees voting fall (fall_model_predict_proba(...)[1]).\n'
        '// Feature order (must match fall_features.h):\n' + order + '\n'
        '#pragma once\n'
        '#include "fall_model_trees.h"\n'
        f'const float FALL_TRIGGER_G       = {TRIGGER_G}f;\n'
        f'const float FALL_PROBA_THRESHOLD = {thr:.6f}f;\n'
        f'const int   FALL_WIN             = {fu.WIN};      // 2.5 s at 50 Hz\n'
        f'const int   FALL_N_FEATURES      = {len(cols)};\n')

    # ---------------- HAR model ----------------
    har_rf = joblib.load(HAR_JOBLIB)
    assert har_rf.n_features_in_ == len(HAR_FEATURES)
    emlearn.convert(har_rf, method='inline', dtype='float').save(file=str(OUT / 'har_model.h'), name='har_model')
    print(f'HAR forest: {len(har_rf.estimators_)} trees, 5 features {HAR_FEATURES}, classes {list(har_rf.classes_)} (0 still, 1 moving)')

    # ---------------- self-test vectors ----------------
    # 3 windows that pass the trigger (one real fall, one daily activity) + one still window (no trigger)
    am_max = Xdf['am_max'].to_numpy()
    fall_i = int(np.where((y == 1) & trig)[0][0])
    cand = np.where((y == 0) & (am_max > TRIGGER_G + 0.3) & (win.activity.to_numpy() < 100))[0]   # clear of the trigger line
    adl_i = int(cand[np.argmax(vote_fraction(fall_rf, X[cand]) < thr)])     # a daily-activity spike the model rejects
    still_i = int(np.argmin(Xdf['am_std'].to_numpy()))
    picks = [fall_i, adl_i, still_i]
    txt = ['// Generated by export_models_to_c.py: real FallAllD windows + the answers Python expects.',
           '// Used by SELF_TEST in silverguard_logger.ino to prove the device maths matches Python.',
           '#pragma once', f'const int TV_N = {len(picks)};', f'const int TV_WIN = {fu.WIN};',
           f'const int TV_HAR_WIN = {HAR_WIN};']
    acc_all, gyr_all, feats, scores, har_mag, har_feat, har_cls = [], [], [], [], [], [], []
    for i in picks:
        acc_all.append(wa[i]); gyr_all.append(wg[i]); feats.append(X[i])
        scores.append(vote_fraction(fall_rf, X[i:i + 1])[0])      # forest score, checked even if the trigger would not fire
        # HAR: 128 accel magnitudes from the same recording, starting at the window start
        r, s = int(win.rec.iat[i]), int(win.start.iat[i])
        s = min(s, ACC.shape[1] - HAR_WIN)
        mag = np.linalg.norm(ACC[r, s:s + HAR_WIN], axis=1).astype(np.float32)
        hf = np.array([mag.mean(), mag.std(), mag.min(), mag.max(), mag.max() - mag.min()], dtype=np.float32)
        votes = np.mean([e.predict(hf[None, :]) for e in har_rf.estimators_])
        har_mag.append(mag); har_feat.append(hf); har_cls.append(1 if votes > 0.5 else 0)   # ties -> 0, like emlearn
    txt.append('const char* TV_NAME[TV_N] = {"fall", "daily activity", "still"};')
    txt.append(c_array('TV_ACC', np.array(acc_all)))            # [TV_N][TV_WIN][3] g
    txt.append(c_array('TV_GYR', np.array(gyr_all)))            # [TV_N][TV_WIN][3] deg/s
    txt.append(c_array('TV_FEAT', np.array(feats)))             # [TV_N][FALL_N_FEATURES]
    txt.append(c_array('TV_SCORE', np.array(scores)))           # vote fraction (fall_model_predict_proba out[1])
    txt.append(c_array('TV_HAR_MAG', np.array(har_mag)))        # [TV_N][TV_HAR_WIN]
    txt.append(c_array('TV_HAR_FEAT', np.array(har_feat)))      # [TV_N][5]
    txt.append('const int TV_HAR_CLASS[TV_N] = {' + ', '.join(map(str, har_cls)) + '};')
    (OUT / 'test_vectors.h').write_text('\n'.join(txt) + '\n')
    print('test vectors:', {n: (round(float(am_max[i]), 2), round(float(s), 3), h)
                            for n, i, s, h in zip(['fall', 'adl', 'still'], picks, scores, har_cls)})

    for f in ('fall_model.h', 'fall_model_trees.h', 'har_model.h', 'test_vectors.h'):
        print(f'wrote {OUT / f}  ({(OUT / f).stat().st_size / 1024:.0f} KB)')


if __name__ == '__main__':
    main()
