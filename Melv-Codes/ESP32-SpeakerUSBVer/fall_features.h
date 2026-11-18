// Window features, written to match the Python training code exactly:
//   fall: notebooks/fall model/fad_utils.py  make_features()  (magnitude-only 14 features)
//   HAR:  notebooks/Model based of UCI DataSet/features.py  mag_features()  (5 features)
// std is the POPULATION std (divide by n), like numpy's .std().
#pragma once
#include <math.h>

const int FS_HZ = 50;

// mean, std, min, max, range of v[0..n-1] -> out[0..4]
static inline void stats5(const float* v, int n, float* out) {
  float sum = 0, mn = v[0], mx = v[0];
  for (int i = 0; i < n; i++) {
    sum += v[i];
    if (v[i] < mn) mn = v[i];
    if (v[i] > mx) mx = v[i];
  }
  float mean = sum / n, sq = 0;
  for (int i = 0; i < n; i++) { float d = v[i] - mean; sq += d * d; }   // two passes: more accurate than sum of squares
  out[0] = mean; out[1] = sqrtf(sq / n); out[2] = mn; out[3] = mx; out[4] = mx - mn;
}

// largest change between neighbouring samples, per second ("jerk": how sudden the movement is)
static inline float jerk_max(const float* v, int n) {
  float m = 0;
  for (int i = 1; i < n; i++) { float d = fabsf(v[i] - v[i - 1]); if (d > m) m = d; }
  return m * FS_HZ;
}

// HAR: 5 features of the accel magnitude (order: mean, std, min, max, range)
static inline void har_features(const float* mag, int n, float* f) {
  stats5(mag, n, f);
}

// Fall: 14 features from accel magnitude am (g) and gyro magnitude gm (deg/s). Order must match fall_model.h:
//  0-4 am mean/std/min/max/range, 5-9 gm mean/std/min/max/range,
//  10 am_jerk_max, 11 am_energy, 12 am_tail_std (last 1 s), 13 gm_jerk_max
static inline void fall_features(const float* am, const float* gm, int n, float* f) {
  stats5(am, n, f);
  stats5(gm, n, f + 5);
  f[10] = jerk_max(am, n);
  float e = 0;
  for (int i = 0; i < n; i++) e += am[i] * am[i];
  f[11] = e / n;                                   // energy = mean of |a|^2
  float tail[5];
  stats5(am + (n - FS_HZ), FS_HZ, tail);           // last 50 samples = after the spike, is the wrist still?
  f[12] = tail[1];
  f[13] = jerk_max(gm, n);
}
