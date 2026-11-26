// SilverGuard activity model for the watch: features + XGBoost + live HMM.
//
// Made in notebooks/still-moving-DL/stage8_export_xgboost.ipynb. Include ONE time in the firmware.
// Needs sg_xgb_model.h (generated: trees, HMM, constants) in the same folder.
//
// Use:
//   1. Collect 1500 samples (30 s at 50 Hz) of acceleration in g, ALREADY calibrated and turned into
//      the model's axes (see stage7_axis_alignment.ipynb, to_model_frame()).
//   2. float f[SG_N_FEATURES];  sg_features(ax, ay, az, f);
//   3. float p[SG_N_CLASSES];   sg_predict_proba(f, p);          // sleep, sedentary, light, moderate-vigorous
//   4. int label = sg_hmm_step(&state, p);                       // live smoothing, uses only the past
//      int moving = label >= 2;                                  // still (0) or moving (1)
//
// WHY float and not double: the ESP32-C3 has no floating-point hardware, both are done in software,
// and float is about twice as fast. Accuracy against the Python model is checked in the notebook.
#ifndef SG_XGB_H
#define SG_XGB_H

#include <math.h>
#include <stdint.h>

#include "sg_xgb_model.h"

#define SG_WIN 1500                       // samples per window (30 s at 50 Hz)

// ---------- features (the same 16 as Stage 1, see make_features in common.py) ----------

static float sg_cos[SG_WIN], sg_sin[SG_WIN];   // one full turn of cos/sin, worked out once (12 kB of RAM)
static int sg_ready = 0;

static void sg_init(void) {
  for (int m = 0; m < SG_WIN; m++) {
    double a = 6.283185307179586 * m / SG_WIN;
    sg_cos[m] = (float)cos(a);
    sg_sin[m] = (float)sin(a);
  }
  sg_ready = 1;
}

// ax, ay, az: SG_WIN samples each, in g. out: SG_N_FEATURES values, in the order of SG_FEATURE_NAMES.
static void sg_features(const float *ax, const float *ay, const float *az, float *out) {
  static float mag[SG_WIN];
  if (!sg_ready) sg_init();

  // WHY double sums: adding 1500 small numbers in float loses digits; 1500 double adds are cheap
  double sm = 0, sx = 0, sy = 0, sz = 0;
  float mn = 1e30f, mx = -1e30f;
  for (int i = 0; i < SG_WIN; i++) {
    float m = sqrtf(ax[i] * ax[i] + ay[i] * ay[i] + az[i] * az[i]);
    mag[i] = m;
    sm += m; sx += ax[i]; sy += ay[i]; sz += az[i];
    if (m < mn) mn = m;
    if (m > mx) mx = m;
  }
  float mean_m = (float)(sm / SG_WIN), mean_x = (float)(sx / SG_WIN);
  float mean_y = (float)(sy / SG_WIN), mean_z = (float)(sz / SG_WIN);

  double vm = 0, vx = 0, vy = 0, vz = 0;          // spread: two passes, as numpy does
  for (int i = 0; i < SG_WIN; i++) {
    double dm = mag[i] - mean_m, dx = ax[i] - mean_x, dy = ay[i] - mean_y, dz = az[i] - mean_z;
    vm += dm * dm; vx += dx * dx; vy += dy * dy; vz += dz * dz;
  }

  // the strength of each rhythm between 0.5 and 8 Hz (step k = k/30 Hz), as in the Stage 1 FFT
  float best_power = -1.0f, low = 0.0f, high = 0.0f;
  int best_k = SG_K_FIRST;
  for (int k = SG_K_FIRST; k <= SG_K_LAST; k++) {
    float re = 0.0f, im = 0.0f;
    int idx = 0;
    for (int i = 0; i < SG_WIN; i++) {
      float s = mag[i] - mean_m;                 // WHY subtract: gravity's steady 1 g would swamp everything
      re += s * sg_cos[idx];
      im -= s * sg_sin[idx];
      idx += k;
      if (idx >= SG_WIN) idx -= SG_WIN;
    }
    float power = (re * re + im * im) * (2.0f / ((float)SG_WIN * (float)SG_WIN));
    if (power > best_power) { best_power = power; best_k = k; }   // first maximum, like numpy argmax
    if (k <= SG_K_LOW_LAST) low += power; else high += power;
  }

  const float deg = 57.29577951308232f;
  out[0] = mean_m;
  out[1] = (float)sqrt(vm / SG_WIN);
  out[2] = mn;
  out[3] = mx;
  out[4] = mx - mn;
  out[5] = mean_x; out[6] = mean_y; out[7] = mean_z;
  out[8] = (float)sqrt(vx / SG_WIN); out[9] = (float)sqrt(vy / SG_WIN); out[10] = (float)sqrt(vz / SG_WIN);
  out[11] = atan2f(-mean_x, sqrtf(mean_y * mean_y + mean_z * mean_z)) * deg;   // pitch
  out[12] = atan2f(mean_y, mean_z) * deg;                                       // roll
  out[13] = best_k / 30.0f;                                                     // dominant rhythm (Hz)
  out[14] = low;                                                                // power 0.5-3 Hz
  out[15] = high;                                                               // power 3-8 Hz
}

// ---------- XGBoost ----------

// p: SG_N_CLASSES probabilities that add up to 1.
static void sg_predict_proba(const float *f, float *p) {
  float margin[SG_N_CLASSES];
  for (int c = 0; c < SG_N_CLASSES; c++) margin[c] = SG_BASE_MARGIN[c];
  for (int t = 0; t < SG_N_TREES; t++) {
    sg_index_t n = SG_TREE_ROOT[t];
    while (SG_NODE_FEATURE[n] != SG_LEAF) {      // go left if the feature is below the threshold, as XGBoost does
      n = (f[SG_NODE_FEATURE[n]] < SG_NODE_VALUE[n]) ? SG_NODE_LEFT[n] : SG_NODE_RIGHT[n];
    }
    margin[t % SG_N_CLASSES] += SG_NODE_VALUE[n];  // tree t adds to class t mod 4
  }
  float top = margin[0], total = 0.0f;           // softmax: margins -> probabilities
  for (int c = 1; c < SG_N_CLASSES; c++) if (margin[c] > top) top = margin[c];
  for (int c = 0; c < SG_N_CLASSES; c++) { p[c] = expf(margin[c] - top); total += p[c]; }
  for (int c = 0; c < SG_N_CLASSES; c++) p[c] /= total;
}

// ---------- live HMM (forward filter) ----------
// The notebooks' HMM (Viterbi) looks at the whole day, including what comes later. The watch can only use the
// past, so it keeps a running belief: "given everything so far, how likely is each activity now?"

typedef struct {
  float belief[SG_N_CLASSES];
  int started;
} sg_hmm_state;

static void sg_hmm_reset(sg_hmm_state *s) { s->started = 0; }

// Add one window's probabilities; returns the most likely activity now (0 sleep, 1 sedentary, 2 light, 3 MVPA).
static int sg_hmm_step(sg_hmm_state *s, const float *p) {
  float b[SG_N_CLASSES], total = 0.0f;
  for (int c = 0; c < SG_N_CLASSES; c++) {
    float prior = 0.0f;
    if (!s->started) prior = SG_HMM_START[c];
    else for (int j = 0; j < SG_N_CLASSES; j++) prior += s->belief[j] * SG_HMM_TRANSITION[j][c];
    float e = p[c] < 1e-6f ? 1e-6f : p[c];       // the same clip as the notebooks' Viterbi
    b[c] = prior * e;
    total += b[c];
  }
  int best = 0;
  for (int c = 0; c < SG_N_CLASSES; c++) {
    s->belief[c] = b[c] / total;                  // WHY rescale every step: otherwise it shrinks to 0
    if (s->belief[c] > s->belief[best]) best = c;
  }
  s->started = 1;
  return best;
}

#endif
