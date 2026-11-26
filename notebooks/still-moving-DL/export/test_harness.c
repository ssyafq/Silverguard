
// Desktop test of the watch code: reads windows, writes features, probabilities and live-HMM labels.
#include <stdio.h>
#include <stdlib.h>
#include "sg_xgb_test_vectors.h"
int main(int argc, char **argv) {
  FILE *in = fopen(argv[1], "rb"), *out = fopen(argv[2], "wb");
  int n, person, last = -1;
  static float w[SG_WIN * 3], ax[SG_WIN], ay[SG_WIN], az[SG_WIN];
  float f[SG_N_FEATURES], p[SG_N_CLASSES];
  sg_hmm_state s;
  printf("self-test worst probability error: %g\n", sg_self_test());
  fread(&n, sizeof(int), 1, in);
  for (int k = 0; k < n; k++) {
    fread(&person, sizeof(int), 1, in);
    fread(w, sizeof(float), SG_WIN * 3, in);
    for (int i = 0; i < SG_WIN; i++) { ax[i] = w[3 * i]; ay[i] = w[3 * i + 1]; az[i] = w[3 * i + 2]; }
    if (person != last) { sg_hmm_reset(&s); last = person; }   // a new person starts a new day
    sg_features(ax, ay, az, f);
    sg_predict_proba(f, p);
    float label = (float)sg_hmm_step(&s, p);
    fwrite(f, sizeof(float), SG_N_FEATURES, out);
    fwrite(p, sizeof(float), SG_N_CLASSES, out);
    fwrite(&label, sizeof(float), 1, out);
  }
  fclose(out);
  return 0;
}
