"""Random-forest predictions for Stage 5 (GOTOV). Run with anaconda's Python, NOT .venv-tf.

WHY a separate script: the forest was saved by scikit-learn 1.2.2 (anaconda, Stage 1), and the newer
scikit-learn in .venv-tf cannot open that file. This only predicts, it does not train.

usage: python rf_predict.py <features .npz with F> <output .npy for the probabilities>
"""
import sys

import joblib
import numpy as np

saved = joblib.load('results/rf_4class.joblib')    # Stage 1 saved {'model', 'features', 'classes'}
rf = saved['model']
assert saved['classes'] == ['sleep', 'sedentary', 'light', 'moderate-vigorous'] and len(saved['features']) == 16
rf.n_jobs = 4                      # WHY: prediction only, keep the CPU cool

# WHY this check: prove this is the same forest giving the same answers as in Stage 1
stage1 = np.load('results/stage1_predictions.npz')
features = np.load('processed/features_all.npz')
test = np.isin(features['pid'], np.unique(stage1['pid']))
assert (features['pid'][test] == stage1['pid']).all()
assert (rf.predict(features['F'][test]) == stage1['pred_rf']).all(), 'forest does not reproduce Stage 1'

with np.load(sys.argv[1]) as f:
    probs = rf.predict_proba(f['F'])
np.save(sys.argv[2], probs)
print(f'forest reproduces Stage 1; predicted {len(probs):,} windows')
