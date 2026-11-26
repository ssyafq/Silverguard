"""Code shared by Stage 1 (random forest), Stage 2 (CNN), Stage 3 (XGBoost) and the cross-validation notebooks.

WHY a shared file: the comparison is only fair if all models use exactly the same people,
the same HMM and the same metrics.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, cohen_kappa_score, confusion_matrix, f1_score

CLASSES = ['sleep', 'sedentary', 'light', 'moderate-vigorous']
BINARY = ['still', 'moving']
DATA_DIR = Path('processed')
RESULTS_DIR = Path('results')
OLD_TEST_FILE = Path('../Model based of UCI DataSet/capture24_test_participants.csv')
N_TEST = 30            # 20% of 151
SEED = 42
N_FOLDS = 5            # cross-validation over all 151 people


# ---------- people ----------

def split_people():
    """-> (train ids, test ids). Test = the earlier 11 people + random others, up to 30.

    WHY by person: windows from one person look alike, so a person in both sets would inflate the score.
    """
    everyone = sorted(f.stem for f in DATA_DIR.glob('P*.npz'))
    assert len(everyone) == 151
    old_test = sorted(pd.read_csv(OLD_TEST_FILE)['participant_id'])
    others = [p for p in everyone if p not in old_test]
    extra = np.random.default_rng(SEED).choice(others, size=N_TEST - len(old_test), replace=False)
    test = sorted(old_test + [str(p) for p in extra])
    train = [p for p in everyone if p not in test]
    return train, test


def cv_folds():
    """-> {person id: fold 0-4}. All 151 people, shuffled with the seed and dealt into 5 folds like cards.

    WHY folds of people, not windows: the same reason as the test split. Every person is tested exactly once,
    by models that never saw them.
    """
    everyone = sorted(f.stem for f in DATA_DIR.glob('P*.npz'))
    assert len(everyone) == 151
    order = np.random.default_rng(SEED).permutation(everyone)
    return {str(p): i % N_FOLDS for i, p in enumerate(order)}


def to_binary(y):
    """4 classes -> 0 = still (sleep, sedentary), 1 = moving (light, moderate-vigorous)."""
    return (np.asarray(y) >= 2).astype(int)


# ---------- features (Stage 1 and Stage 3) ----------
# A word-for-word copy of the feature cell in stage1_rf_hmm.ipynb. Stage 3 checks that it gives the same numbers.

HZ, WIN = 50, 1500
FREQS = np.fft.rfftfreq(WIN, 1 / HZ)               # the frequency of each FFT step (0.033 Hz apart)
LOW_BAND = (FREQS >= 0.5) & (FREQS < 3)
HIGH_BAND = (FREQS >= 3) & (FREQS <= 8)
BOTH_BANDS = LOW_BAND | HIGH_BAND

FEATURES = ['mag_mean', 'mag_std', 'mag_min', 'mag_max', 'mag_range',
            'x_mean', 'y_mean', 'z_mean', 'x_std', 'y_std', 'z_std',
            'pitch', 'roll', 'dom_freq', 'power_0.5_3', 'power_3_8']


def make_features(X):
    """(n, 1500, 3) windows in g -> (n, 16) features."""
    X = X.astype('float64')                        # WHY: more exact sums over 1500 small numbers
    mag = np.sqrt((X ** 2).sum(axis=2))
    mean, std = X.mean(axis=1), X.std(axis=1)

    # WHY the mean: over 30 s, movement mostly averages out and what is left points along gravity
    pitch = np.degrees(np.arctan2(-mean[:, 0], np.sqrt(mean[:, 1] ** 2 + mean[:, 2] ** 2)))
    roll = np.degrees(np.arctan2(mean[:, 1], mean[:, 2]))

    # WHY subtract the mean: gravity is a constant 1 g and would swamp everything else in the FFT
    spectrum = np.abs(np.fft.rfft(mag - mag.mean(axis=1, keepdims=True), axis=1)) ** 2
    # WHY this scale: a band's power is then the variance that band adds to the magnitude (in g squared)
    spectrum = spectrum * 2 / WIN ** 2
    dom_freq = FREQS[BOTH_BANDS][spectrum[:, BOTH_BANDS].argmax(axis=1)]

    return np.column_stack([mag.mean(axis=1), mag.std(axis=1), mag.min(axis=1), mag.max(axis=1),
                            mag.max(axis=1) - mag.min(axis=1), mean, std, pitch, roll, dom_freq,
                            spectrum[:, LOW_BAND].sum(axis=1), spectrum[:, HIGH_BAND].sum(axis=1)])


# ---------- HMM ----------

def learn_hmm(y, pid):
    """Count how often each label follows each label. -> (start probabilities, transition matrix)

    WHY from true labels and not from model output: this way both models get the identical HMM.
    A step from one person to the next is not counted. A step across an unlabelled gap IS counted:
    almost every label change in Capture-24 sits across a gap, so skipping them would teach the
    HMM that activity never changes.
    """
    k = len(CLASSES)
    counts = np.ones((k, k))       # WHY start at 1: a change never seen in training stays unlikely, not impossible
    same_person = pid[1:] == pid[:-1]
    np.add.at(counts, (y[:-1][same_person], y[1:][same_person]), 1)
    transition = counts / counts.sum(axis=1, keepdims=True)
    start = np.bincount(y, minlength=k) / len(y)
    return start, transition


def viterbi(probs, start, transition):
    """The single most likely sequence of labels for ONE person, given the model's probabilities (n, k)."""
    # WHY logs: multiplying thousands of small numbers would underflow to 0, adding their logs does not.
    # WHY clip: a probability of exactly 0 would forbid a label forever.
    log_p = np.log(np.clip(probs, 1e-6, 1))
    log_t = np.log(transition)
    n, k = probs.shape
    came_from = np.zeros((n, k), dtype=int)

    score = np.log(start) + log_p[0]               # best score of any path ending in each label
    for i in range(1, n):
        options = score[:, None] + log_t           # [from, to]
        came_from[i] = options.argmax(axis=0)      # the best label to have come from
        score = options.max(axis=0) + log_p[i]

    path = np.zeros(n, dtype=int)
    path[-1] = score.argmax()
    for i in range(n - 1, 0, -1):                  # walk back along the remembered best steps
        path[i - 1] = came_from[i, path[i]]
    return path


def smooth(probs, pid, start, transition):
    """Viterbi on each person separately. Rows must already be in time order within each person."""
    out = np.zeros(len(probs), dtype=int)
    for p in np.unique(pid):
        mine = pid == p
        out[mine] = viterbi(probs[mine], start, transition)
    return out


# ---------- metrics ----------

def scores(y_true, y_pred, n_classes):
    return {'accuracy': accuracy_score(y_true, y_pred),
            # WHY macro: each class counts the same, so rare moderate-vigorous matters as much as sleep
            'macro-F1': f1_score(y_true, y_pred, labels=range(n_classes), average='macro', zero_division=0),
            # WHY kappa: agreement beyond what guessing the class shares would give
            'kappa': cohen_kappa_score(y_true, y_pred)}


def evaluate(name, y_true, y_pred):
    """One row per task. The binary answer is the 4-class answer merged into still/moving."""
    return [{'model': name, 'task': '4-class', **scores(y_true, y_pred, 4)},
            {'model': name, 'task': 'binary', **scores(to_binary(y_true), to_binary(y_pred), 2)}]


def paired_bootstrap(a, b, n=10_000):
    """Mean of (a - b) over people, with a 95% range from resampling people. a, b = one score per person.

    WHY paired: the same people are scored by both models, so we resample the per-person differences.
    """
    d = np.asarray(a) - np.asarray(b)
    rng = np.random.default_rng(SEED)
    means = d[rng.integers(0, len(d), (n, len(d)))].mean(axis=1)
    return d.mean(), np.percentile(means, 2.5), np.percentile(means, 97.5)


def confusion_table(y_true, y_pred, names):
    """Rows = truth, columns = prediction, in windows and as a percent of each true class."""
    cm = confusion_matrix(y_true, y_pred, labels=range(len(names)))
    table = pd.DataFrame(cm, index=[f'true {n}' for n in names], columns=names)
    percent = pd.DataFrame((100 * cm / cm.sum(axis=1, keepdims=True)).round(1), index=table.index,
                           columns=[f'{n} %' for n in names])
    return pd.concat([table, percent], axis=1)
