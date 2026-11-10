"""Why the device checks "did the wrist turn?" after a fall candidate (stage 3 in silverguard_logger.ino).

Simulates the exact on-device pipeline (every 0.5 s on the last 2.5 s, 2.5 g trigger, the exported 30-tree forest,
5 s lockout) on FallAllD (wrist D2) and WEDA-FALL, then measures how far the wrist turned:
gravity direction 1.5-0.5 s before the spike vs 1.0-2.5 s after it.

Run from this folder:  python confirm_stage_eval.py
Thresholds should be chosen on FallAllD; WEDA is only used to check them.
"""
import numpy as np
import pandas as pd

import export_models_to_c as ex
import fad_utils as fu

FS, W, STEP = fu.FS_OUT, fu.WIN, 25
PRE, POST = (-75, -25), (50, 125)        # samples relative to the impact (same as the sketch)
TURNS = [0, 8, 15, 20, 25, 30, 40]       # candidate CONFIRM_MIN_TURN_DEG values


def angle(u, v):
    return float(np.degrees(np.arccos(np.clip(np.dot(u, v) / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))))


def candidates(rf, thr, cols, acc, gyr):
    """Device pipeline on one recording -> wrist turn (deg) for each fall candidate (NaN = not enough data)."""
    ends = np.arange(128, len(acc) + 1, STEP)
    if len(ends) == 0:
        return []
    wa = np.stack([acc[e - W:e] for e in ends]); wg = np.stack([gyr[e - W:e] for e in ends])
    am = np.linalg.norm(wa, axis=2)
    trig = am.max(1) > fu_trigger()
    if not trig.any():
        return []
    score = np.full(len(ends), -1.0)
    score[trig] = ex.vote_fraction(rf, fu.make_features(wa[trig], wg[trig])[cols].to_numpy(np.float32))
    out, lock = [], -1
    for k in np.where(score >= thr)[0]:
        imp = ends[k] - W + int(am[k].argmax())
        if imp < lock:
            continue
        lock = imp + 5 * FS
        a0, a1 = max(imp + PRE[0], 0), imp + PRE[1]
        b0, b1 = imp + POST[0], min(imp + POST[1], len(acc))
        turn = np.nan if (a1 - a0 < 10 or b1 - b0 < 25) else angle(acc[a0:a1].mean(0), acc[b0:b1].mean(0))
        p0 = max(imp - 50, 0)                                   # the 1 s leading into the impact (stage 3b)
        AM, GM = np.linalg.norm(acc[p0:imp + 1], axis=1), np.linalg.norm(gyr[p0:imp + 1], axis=1)
        out.append((turn, AM[:-1].min() if len(AM) > 1 else np.nan, GM.max()))
    return out


def fu_trigger():
    return ex.TRIGGER_G


def main():
    # the exact exported model: same data, settings and seed as export_models_to_c.py
    trials, ACC, GYR = fu.load_device()
    win, wa, wg = fu.make_windows(ACC, GYR, trials)[:3]
    Xdf = fu.make_features(wa, wg)
    cols = fu.feature_sets(Xdf.columns)['accel + gyro (magnitude)']
    X = Xdf[cols].to_numpy(np.float32); y = win.y.to_numpy(); trig = Xdf.am_max.to_numpy() > ex.TRIGGER_G
    thr = ex.pick_threshold(X, y, win.subject.to_numpy(), trig, ex.SMALL)
    rf = ex.new_rf(ex.SMALL).fit(X[trig], y[trig])

    def collect(tr, accs, gyrs):
        rows = []
        for r in range(len(tr)):
            for t, min_g, max_dps in candidates(rf, thr, cols, accs[r], gyrs[r]):
                rows.append({'rec': r, 'fall': bool(tr.is_fall.iat[r]), 'activity': tr.activity.iat[r],
                             'group': tr['group'].iat[r] if 'group' in tr else '', 'turn': t,
                             'min_g_before': min_g, 'max_dps_before': max_dps})
        return pd.DataFrame(rows)

    fad = collect(trials, ACC, GYR)
    wt, WA, WG = fu.load_weda()
    wed = collect(wt, WA, WG)

    def recs(df, mask, a):     # recordings with at least one alarm (NaN turn = not confirmed here)
        d = df[mask].assign(c=df[mask].turn > a)
        return int(d.groupby('rec').c.max().sum())

    rows = []
    for a in TURNS:
        rows.append({'min turn (deg)': a,
                     f'FallAllD falls (of {int(trials.is_fall.sum())})': recs(fad, fad.fall, a),
                     f'FallAllD ADL (of {int((~trials.is_fall).sum())})': recs(fad, ~fad.fall, a),
                     'WEDA falls (of 350)': recs(wed, wed.activity.str.startswith('F'), a),
                     'WEDA hit table D09 (of 73)': recs(wed, wed.activity == 'D09', a),
                     'WEDA clapping D10 (of 74)': recs(wed, wed.activity == 'D10', a),
                     'WEDA older ADL (of 157)': recs(wed, wed.group == 'older', a)})
    print('Recordings that raise a fall alarm, by minimum wrist turn (0 = no stage 3):')
    print(pd.DataFrame(rows).to_string(index=False))
    kind = wed.activity.map(lambda a: 'FALL' if a.startswith('F') else {'D09': 'hit table', 'D10': 'clapping'}.get(a, 'other ADL'))
    print('\nWEDA wrist turn after the spike (deg):')
    print(wed.groupby(kind).turn.describe(percentiles=[.1, .5, .9]).round(1).to_string())

    # stage 3b: reject a candidate if, in the 1 s before the spike, |a| never dipped below DIP g AND rotation stayed < ROT dps
    print('\nStage 3b cost: alarmed falls (already past the 20 deg check) that the no-fall-motion rule would drop')
    rows = []
    for dip in (0.8, 0.85, 0.9):
        for rot in (100, 150, 200):
            r = {'reject if min |a| before >': dip, 'and max gyro before <': rot}
            for name, df in (('FallAllD', fad), ('WEDA', wed)):
                f = df[df.fall & (df.turn > 20)]
                keep = ~((f.min_g_before > dip) & (f.max_dps_before < rot))
                g = f.assign(keep=keep).groupby('rec').keep.max()
                r[f'{name} falls lost'] = f'{int((~g).sum())}/{len(g)}'
            rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == '__main__':
    main()
