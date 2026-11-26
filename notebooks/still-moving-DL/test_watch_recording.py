"""Test the activity models on recordings from the SilverGuard watch itself, and compare old vs new.

  NEW model: Stage 8's XGBoost + live HMM (4 classes, every 30 s). Needs calibration and the axis setup (Stage 7).
  OLD model: the still/moving forest on the watch today (firmware/FinalPrototype/har_model.h, every 0.5 s on 2.56 s),
             run through the firmware's OWN C code, exactly as the watch runs it.

Record with firmware/FinalPrototype/record.py. Name each trial after what you did, e.g.:
    python record.py COM5 calibration --seconds 120     (6 still positions, 10 s each, FACE UP FIRST)
    python record.py COM5 sitting_desk --seconds 300
    python record.py COM5 walk_brisk --seconds 300
The activity words the script understands are in ACTIVITIES below.

Usage (run from this folder with the .venv-tf Python):
    python test_watch_recording.py recordings/*.csv --calibration recordings/calibration_*.csv
    python test_watch_recording.py recordings/*.csv --calibration ... --orientation labels      (quick, optimistic)
    python test_watch_recording.py new_recordings/*.csv --setup watch_setup.json                 (reuse a saved setup)

Options for --orientation:
    search  (default) pick the axis setup WITHOUT labels (the Stage 7 tool). Needs hours of normal wear to be reliable
    labels  pick the setup that scores best on these recordings' own labels. Quick, but optimistic: confirm on new data
    none    use the watch's axes as they are
    or a setup name, e.g. "model x, y, z = watch -y, +x, +z"
"""
import argparse
import glob
import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

import watch_alignment as W
from common import CLASSES, FEATURES, make_features, scores, smooth, to_binary

HERE = Path(__file__).resolve().parent
FIRMWARE = HERE.parent.parent / 'firmware' / 'FinalPrototype'
HZ, WIN = 50, 1500

# keyword in the trial name -> (class by Capture-24's rules, still or moving the plain physical way)
# WHY two: the new model learned Capture-24's rules, where STANDING counts as light (moving). The old model learned
# from UCI, where standing is still. Scoring both ways keeps the comparison fair to each.
ACTIVITIES = {
    'sleep': ('sleep', 'still'), 'lying': ('sedentary', 'still'), 'lie': ('sedentary', 'still'),
    'sitting': ('sedentary', 'still'), 'sit': ('sedentary', 'still'), 'desk': ('sedentary', 'still'),
    'sofa': ('sedentary', 'still'), 'typing': ('sedentary', 'still'), 'reading': ('sedentary', 'still'),
    'tv': ('sedentary', 'still'),
    'standing': ('light', 'still'), 'stand': ('light', 'still'),
    'dishes': ('light', 'moving'), 'cooking': ('light', 'moving'), 'chores': ('light', 'moving'),
    'cleaning': ('light', 'moving'), 'vacuum': ('light', 'moving'), 'shelves': ('light', 'moving'),
    'household': ('light', 'moving'), 'walk_slow': ('light', 'moving'), 'slow_walk': ('light', 'moving'),
    'stroll': ('light', 'moving'),
    'walk_brisk': ('moderate-vigorous', 'moving'), 'brisk': ('moderate-vigorous', 'moving'),
    'walk_fast': ('moderate-vigorous', 'moving'), 'walking': ('moderate-vigorous', 'moving'),
    'walk': ('moderate-vigorous', 'moving'), 'stairs': ('moderate-vigorous', 'moving'),
    'cycling': ('moderate-vigorous', 'moving'), 'bike': ('moderate-vigorous', 'moving'),
    'jog': ('moderate-vigorous', 'moving'), 'exercise': ('moderate-vigorous', 'moving'),
}


def activity_of(label):
    """'walk_brisk_01' -> ('walk_brisk', class, still/moving). Longest keyword wins (walk_slow before walk)."""
    if not label:
        return None
    text = label.lower()
    for key in sorted(ACTIVITIES, key=len, reverse=True):
        if key in text:
            return (key, *ACTIVITIES[key])
    return None


def say(*parts):
    print(*parts, flush=True)


# ---------- 1. read ----------

def read_all(paths):
    files = []
    for p in paths:
        r = W.read_watch_csv(p)
        r['path'] = Path(p)
        r['activity'] = activity_of(r['label'] or Path(p).stem)
        dt = np.diff(r['micros']) % 2 ** 32
        steps = np.diff(r['index'])
        r['rate'] = (len(dt)) / (dt.sum() / 1e6) if len(dt) else np.nan
        r['missing'] = int((steps - 1).clip(min=0).sum())
        files.append(r)
        act = r['activity'][0] if r['activity'] else 'NO LABEL (not scored)'
        say(f'  {Path(p).name}: {len(r["acc"]) / HZ / 60:.1f} min, {r["rate"]:.2f} Hz, {r["missing"]} samples missing, '
            f'activity: {act}')
        if abs(r['rate'] - HZ) > 1:
            say(f'    WARNING: rate {r["rate"]:.2f} Hz, the models expect {HZ} Hz')
    return files


# ---------- 2. calibration ----------

def calibrate(files, calibration_path):
    """-> offset, gain, face axis (or None), report. Prefers the 6-position recording."""
    if calibration_path:
        c = W.read_watch_csv(calibration_path)
        offset, gain, report = W.fit_calibration(W.still_blocks(c['acc'], seconds=5))   # 5 s: a shaky 10 s hold still counts
        report['source'] = Path(calibration_path).name
        first = (c['acc'][:10 * HZ] - offset) * gain                  # the first 10 s: lying face up
        face = int(np.abs(first.mean(axis=0)).argmax())
        if first.std(axis=0).max() > 0.02 or np.abs(first.mean(axis=0)).max() < 0.9:
            say('  WARNING: the first 10 s of the calibration file are not one still position; face axis unknown')
            face = None
    else:
        points = np.concatenate([W.still_blocks(f['acc']) for f in files if len(f['acc']) >= 10 * HZ] or [np.zeros((0, 3))])
        offset, gain, report = W.fit_calibration(points)
        report['source'] = 'still moments in the recordings'
        face = None
    if not report['used']:
        scales = [f['firmware_scale'] for f in files if f['firmware_scale']]
        s = scales[0] if scales else 1.0
        offset, gain = np.zeros(3), np.full(3, s)
        say(f'  WARNING: not enough different still positions for a proper calibration '
            f'(do the 6-position routine). Using the firmware\'s single scale {s:.3f} instead')
    return offset, gain, face, report


# ---------- 3. windows ----------

def windows_of(f, offset, gain):
    """30 s windows with no missing sample, calibrated. -> (X (n, 1500, 3), first sample of each)."""
    acc = (f['acc'] - offset) * gain
    X, first = [], []
    for s in range(0, len(acc) - WIN + 1, WIN):
        if f['index'][s + WIN - 1] - f['index'][s] == WIN - 1:
            X.append(acc[s:s + WIN]); first.append(s)
    return np.array(X).reshape(-1, WIN, 3), np.array(first, dtype=int)


# ---------- 4. the new model ----------

def live_hmm(probs, start, transition):
    """Forward filter: each window labelled from the windows before it only (as sg_xgb.h does on the watch)."""
    out, belief = np.zeros(len(probs), dtype=int), None
    for i, p in enumerate(probs):
        e = np.clip(p, 1e-6, 1)
        belief = start * e if belief is None else (belief @ transition) * e
        belief /= belief.sum()
        out[i] = belief.argmax()
    return out


def predict_new(F_list, model, hmm):
    """Per file: probabilities, live-HMM labels, whole-recording HMM labels."""
    out = []
    for F in F_list:
        if len(F) == 0:
            out.append((np.zeros((0, 4)), np.zeros(0, int), np.zeros(0, int)))
            continue
        pr = model.predict_proba(F)
        out.append((pr, live_hmm(pr, hmm['start'], hmm['transition']),
                    smooth(pr, np.zeros(len(pr)), hmm['start'], hmm['transition'])))
    return out


# ---------- 5. the old model, through the firmware's own C code ----------

OLD_HARNESS = r'''
// Runs the watch's still/moving forest exactly as ESP32-SpeakerUSBVer.ino does (HAR part only).
#include <stdio.h>
#include <stdint.h>
#include "fall_features.h"
#include "har_model.h"
#define HAR_WIN 128
#define RESULT_EVERY 25
int main(int argc, char **argv) {
  FILE *in = fopen(argv[1], "rb"), *out = fopen(argv[2], "wb");
  int n; fread(&n, sizeof(int), 1, in);
  float ring[HAR_WIN], am[HAR_WIN], hf[5];
  int pos = 0, filled = 0, since = 0;
  for (int k = 0; k < n; k++) {
    float m; int gap; fread(&m, sizeof(float), 1, in); fread(&gap, sizeof(int), 1, in);
    if (gap) filled = 0;                               // the firmware empties the window after a gap
    ring[pos] = m; pos = (pos + 1) % HAR_WIN;
    if (filled < HAR_WIN) filled++;
    since++;
    int result = -1;
    if (filled == HAR_WIN && since >= RESULT_EVERY) {
      since = 0;
      for (int i = 0; i < HAR_WIN; i++) am[i] = ring[(pos + i) % HAR_WIN];   // oldest first
      har_features(am, HAR_WIN, hf);
      result = har_model_predict(hf, 5);
    }
    fwrite(&result, sizeof(int), 1, out);
  }
  fclose(out);
  return 0;
}
'''


def old_model(files, workdir):
    """Per file: the old model's decision at each sample (-1 = no decision at that sample)."""
    workdir.mkdir(exist_ok=True)
    src, exe = workdir / 'old_model_harness.c', workdir / 'old_model_harness.exe'
    src.write_text(OLD_HARNESS)
    build = subprocess.run([sys.executable, '-m', 'ziglang', 'cc', '-O2', '-w', f'-I{FIRMWARE}', '-o', str(exe), str(src)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        say('  could not compile the old model:', build.stderr[:500])
        return None
    results = []
    for f in files:
        scale = f['firmware_scale'] or 1.0
        mag = (scale * np.linalg.norm(f['acc'], axis=1)).astype(np.float32)     # as the firmware: |a| x acc_scale
        gap = np.r_[0, (np.diff(f['index']) != 1).astype(np.int32)]
        with open(workdir / 'old_in.bin', 'wb') as fh:
            np.array([len(mag)], dtype=np.int32).tofile(fh)
            np.rec.fromarrays([mag, gap.astype(np.int32)], names='m,g').tofile(fh)
        subprocess.run([str(exe), str(workdir / 'old_in.bin'), str(workdir / 'old_out.bin')], check=True)
        results.append(np.fromfile(workdir / 'old_out.bin', dtype=np.int32))
        if not f['firmware_scale']:
            say(f'  NOTE {f["path"].name}: no firmware scale in the header, the old model was run without it')
        events = f['path'].with_name(f['path'].stem + '_events.csv')
        if events.exists():                                # the watch's own answers, if record.py saved them
            dev = pd.read_csv(events, header=None, comment='E', names=['kind', 'sample_index', 'har', 'peak', 'score', 'fall'])
            dev = dev[dev.kind == 'result']
            mine = pd.Series(results[-1], index=f['index'])
            both = mine.reindex(dev.sample_index.to_numpy())
            ok = both.notna() & (both >= 0)
            if ok.any():
                say(f'  check vs the watch\'s own answers in {events.name}: same answer for '
                    f'{100 * (both[ok].to_numpy() == dev.har.to_numpy()[ok.to_numpy()]).mean():.1f}% of {ok.sum()} decisions')
    return results


# ---------- 6. scoring ----------

def score_table(rows_true, rows_pred, n):
    m = ~pd.isna(rows_true)
    if m.sum() == 0:
        return None
    t, p = np.asarray(rows_true)[m].astype(int), np.asarray(rows_pred)[m].astype(int)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return {'windows': int(m.sum()), **{k: round(float(v), 3) for k, v in scores(t, p, n).items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('recordings', nargs='+', help='CSV files from record.py')
    ap.add_argument('--calibration', help='record.py file of the 6 still positions (face up first)')
    ap.add_argument('--orientation', default='search', help='search | labels | none | a setup name')
    ap.add_argument('--setup', help='use a saved watch_setup.json (calibration + orientation) instead of working them out')
    ap.add_argument('--model', default='final', choices=['final', 'tested'],
                    help='final = trained on all 151 people (what export/ holds); tested = its 121-person twin')
    ap.add_argument('--out', default='watch_test_results', help='folder for the results')
    ap.add_argument('--no-old', action='store_true', help='skip the old model')
    args = ap.parse_args()

    # WHY expand here: PowerShell passes '*.csv' to Python as it is. A folder means every CSV in it.
    expanded = []
    for a in args.recordings:
        if Path(a).is_dir():
            expanded += sorted(str(p) for p in Path(a).glob('*.csv'))
        elif any(ch in a for ch in '*?['):
            expanded += sorted(glob.glob(a))
        else:
            expanded.append(a)
    skip = ('_events', '_windows')                         # the watch's own answers; capture_csv.py's window file
    paths = [p for p in expanded if not Path(p).stem.endswith(skip)
             and (not args.calibration or Path(p).resolve() != Path(args.calibration).resolve())]
    if not paths:
        raise SystemExit(f'no recordings found in {args.recordings}')
    out = Path(args.out); out.mkdir(exist_ok=True)

    say('\n1. Recordings')
    files = read_all(paths)

    say('\n2. Calibration and axes')
    if args.setup:
        setup = json.loads(Path(args.setup).read_text())
        offset, gain, R = np.array(setup['offset']), np.array(setup['gain']), np.array(setup['matrix'])
        face, orient_name = setup.get('face_axis'), setup['orientation']
        say(f'  from {args.setup}: {orient_name}')
    else:
        offset, gain, face, report = calibrate(files, args.calibration)
        say(f'  calibration ({report["source"]}): offset {np.round(offset, 3)} g, gain {np.round(gain, 3)}; '
            f'error on still moments {report.get("error before (mg)", float("nan")):.0f} -> '
            f'{report.get("error after (mg)", float("nan")):.0f} mg')
        if face is not None:
            say(f'  face-up axis: watch {"xyz"[face]}')

    per_file = [windows_of(f, offset, gain) for f in files]
    X_all = np.concatenate([x for x, _ in per_file]) if per_file else np.zeros((0, WIN, 3))
    hours = len(X_all) * 30 / 3600
    say(f'  {len(X_all)} full 30 s windows ({hours:.1f} h)')
    if len(X_all) == 0:
        raise SystemExit('no full 30 s windows: record at least 30 s without gaps')

    model = xgb.XGBClassifier()
    model.load_model(HERE / 'results' / ('xgb_watch_all151.json' if args.model == 'final' else 'xgb_watch.json'))
    hmm = dict(np.load(HERE / 'results' / ('hmm_all151.npz' if args.model == 'final' else 'hmm.npz')))
    F_raw = [make_features(x) if len(x) else np.zeros((0, len(FEATURES))) for x, _ in per_file]
    truth4 = [np.full(len(x), CLASSES.index(f['activity'][1]) if f['activity'] else np.nan) for f, (x, _) in zip(files, per_file)]
    candidates = list(range(len(W.ORIENTATIONS)))
    if face is not None:                                        # Capture-24's face axis is z: keep model z = +-watch face axis
        candidates = [i for i, (_, Rm) in enumerate(W.ORIENTATIONS) if abs(Rm[2, face]) == 1]

    if not args.setup:
        o = args.orientation
        if o == 'none':
            pick = W.IDENTITY
        elif o == 'search':
            ref = W.load_reference(HERE / 'results' / 'axis_reference.npz')
            u, mv = W.window_summary(X_all)
            pick, _, d = W.choose_orientation(u, mv, ref, candidates)
            ranked = sorted(candidates, key=lambda i: d[i])
            say(f'  axis search without labels ({len(candidates)} candidates): best {W.ORIENTATIONS[pick][0]} '
                f'(distance {d[pick]:.3f}; runner-up {d[ranked[1]]:.3f})')
            if hours < 9:
                say(f'  WARNING: only {hours:.1f} h of data. Stage 7 found the search reliable with about 3 h each from '
                    f'3-5 people (about 9-15 h). Treat this choice as provisional')
        elif o == 'labels':
            y_all = np.concatenate(truth4)
            if np.isnan(y_all).all():
                raise SystemExit('--orientation labels needs labelled recordings')
            best = None
            for i in candidates:
                Rm = W.ORIENTATIONS[i][1]
                preds = [live_hmm(model.predict_proba(W.rotate_features(F, Rm, FEATURES)), hmm['start'], hmm['transition'])
                         if len(F) else np.zeros(0, int) for F in F_raw]
                s = score_table(y_all, np.concatenate(preds), 4)
                if best is None or s['kappa'] > best[1]:
                    best = (i, s['kappa'])
            pick = best[0]
            say(f'  axis setup chosen BY LABELS ({len(candidates)} candidates): {W.ORIENTATIONS[pick][0]}. '
                'OPTIMISTIC: chosen and scored on the same data. Confirm on a new recording with --setup')
        else:
            names = {n: i for i, (n, _) in enumerate(W.ORIENTATIONS)}
            if o not in names:
                raise SystemExit(f'unknown orientation {o!r}. Example: "{W.ORIENTATIONS[5][0]}"')
            pick = names[o]
        orient_name, R = W.ORIENTATIONS[pick]
        setup = {'orientation': orient_name, 'matrix': R.tolist(), 'mirror (other wrist)': bool(np.linalg.det(R) < 0),
                 'offset': offset.tolist(), 'gain': gain.tolist(), 'face_axis': face,
                 'chosen_by': args.orientation, 'hours_of_data': round(hours, 2)}
        (out / 'watch_setup.json').write_text(json.dumps(setup, indent=2))
        src = [int(np.flatnonzero(R[k])[0]) for k in range(3)]
        sign = [int(R[k, src[k]]) for k in range(3)]
        say(f'  setup saved to {out / "watch_setup.json"}. For the firmware (Stage 7, section 6):')
        say(f'    const float   ACC_OFFSET_G[3] = {{{", ".join(f"{v:.4f}f" for v in offset)}}};')
        say(f'    const float   ACC_GAIN[3]     = {{{", ".join(f"{v:.4f}f" for v in gain)}}};')
        say(f'    const uint8_t MODEL_FROM[3]   = {{{", ".join(map(str, src))}}};')
        say(f'    const int8_t  MODEL_SIGN[3]   = {{{", ".join(f"{s:+d}" for s in sign)}}};')
        say(f'    const bool    MIRROR          = {"true" if np.linalg.det(R) < 0 else "false"};')

    say(f'\n3. New model ({args.model}: XGBoost + live HMM, every 30 s)')
    F_rot = [W.rotate_features(F, np.asarray(R), FEATURES) if len(F) else F for F in F_raw]
    new = predict_new(F_rot, model, hmm)

    old = None if args.no_old else old_model(files, out / 'old_model_c')

    rows = []
    for k, (f, (x, first)) in enumerate(zip(files, per_file)):
        pr, live, whole = new[k]
        for j, s in enumerate(first):
            row = {'file': f['path'].name, 'start_s': round(s / HZ, 1),
                   'activity': f['activity'][0] if f['activity'] else '',
                   'true_class': f['activity'][1] if f['activity'] else '',
                   'true_physical': f['activity'][2] if f['activity'] else '',
                   'new_live': CLASSES[live[j]], 'new_whole_recording_hmm': CLASSES[whole[j]],
                   'new_no_hmm': CLASSES[pr[j].argmax()], **{f'p_{c}': round(float(v), 4) for c, v in zip(CLASSES, pr[j])}}
            if old is not None:
                d = old[k][s:s + WIN]
                d = d[d >= 0]
                row['old_moving_share'] = round(float(d.mean()), 3) if len(d) else np.nan
                row['old_call'] = ('moving' if d.mean() > 0.5 else 'still') if len(d) else ''
            rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(out / 'windows.csv', index=False)

    say('\n4. What each model said, per recording')
    summary = table.groupby('file').agg(activity=('activity', 'first'), windows=('start_s', 'size'),
                                        new_most_common=('new_live', lambda v: v.value_counts().index[0]),
                                        new_moving_pct=('new_live', lambda v: round(100 * v.isin(['light', 'moderate-vigorous']).mean())))
    if old is not None:
        summary['old_moving_pct'] = table.groupby('file').old_moving_share.mean().mul(100).round()
    say(summary.to_string())

    labelled = table[table.true_class != '']
    if len(labelled) == 0:
        say('\nNo labelled recordings (name the trials after the activity, see ACTIVITIES), so nothing is scored.')
        return
    say(f'\n5. Scores on {len(labelled)} labelled windows ({len(labelled) * 30 / 60:.0f} min)')
    t4 = labelled.true_class.map(CLASSES.index)
    res = {'new, 4 classes (live HMM)': score_table(t4, labelled.new_live.map(CLASSES.index), 4),
           'new, 4 classes (whole-recording HMM)': score_table(t4, labelled.new_whole_recording_hmm.map(CLASSES.index), 4)}
    new_moving = labelled.new_live.isin(['light', 'moderate-vigorous']).astype(int)
    for rule, truth in [("Capture-24's rule (standing = moving)", to_binary(t4)),
                        ('physical rule (standing = still)', (labelled.true_physical == 'moving').astype(int))]:
        res[f'new, still vs moving, {rule}'] = score_table(truth, new_moving, 2)
        if old is not None:
            res[f'OLD, still vs moving, {rule}'] = score_table(truth, (labelled.old_call == 'moving').astype(int), 2)
    say(pd.DataFrame(res).T.to_string())
    say('\nper activity, % of windows called moving (new: light or moderate-vigorous):')
    per_act = labelled.groupby('activity').agg(windows=('start_s', 'size'), true_class=('true_class', 'first'),
                                               new_moving_pct=('new_live', lambda v: round(100 * v.isin(['light', 'moderate-vigorous']).mean())))
    if old is not None:
        per_act['old_moving_pct'] = labelled.groupby('activity').old_moving_share.mean().mul(100).round()
    per_act['new_most_common'] = labelled.groupby('activity').new_live.agg(lambda v: v.value_counts().index[0])
    say(per_act.to_string())
    say('\nnew model, 4 classes (rows = truth, columns = live-HMM answer):')
    say(pd.crosstab(labelled.true_class, labelled.new_live).reindex(index=[c for c in CLASSES if c in set(labelled.true_class)],
                                                                    columns=CLASSES, fill_value=0).to_string())
    pd.DataFrame(res).T.to_csv(out / 'scores.csv')
    say(f'\nsaved: {out / "windows.csv"}, {out / "scores.csv"}' + ('' if args.setup else f', {out / "watch_setup.json"}'))
    say('Remember: standing counts as light for the new model; lying awake can read as sleep; short bursts are smoothed away.')


if __name__ == '__main__':
    main()
