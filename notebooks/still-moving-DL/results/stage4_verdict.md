## 5. Verdict

**1. The HMM matters more than the choice of model.** Smoothing the day adds +0.107 (CNN) to +0.123 (forest) kappa on 4 classes, and helps 150–151 of the 151 people for every model. Whatever model SilverGuard uses, it should be followed by the HMM (or something like it).

**2. Best results: XGBoost + HMM.**
- 4-class kappa **0.822** (cross-validation): a **tie** with the random forest + HMM (0.825)
- Still vs moving kappa **0.812**: **better** than the forest (+0.019, 95% range entirely above 0). It finds more of the movement (89% vs 85%) at the cost of a few more false alarms (5.6% vs 4.8% of still windows called moving)
- Better than the CNN + HMM on both tasks
- 53+ group (34 people): ties the forest on 4 classes, **better** on still vs moving (26 of 34 people, +0.036)
- Moderate-vigorous: slightly behind the forest (F1 0.675 vs 0.688). On the single test it was ahead, so this one is close and not settled
- 15 MB, about 9x smaller than the scikit-learn forest

**3. Random forest + HMM: equally accurate on 4 classes, but 136 MB.** It is the only model with no clear advantage left: XGBoost matches it on 4 classes, beats it on still vs moving, and is much smaller.

**4. The 1D CNN + HMM did not beat the tree models.** That was the main question of this project.
- 4 classes: **worse** than both tree models (about −0.035 to −0.039 kappa, 95% ranges below 0)
- Still vs moving: **tied** with the forest, behind XGBoost (−0.018, 95% range just below 0)
- **Clearly weaker for the 53+ group on 4 classes** (median kappa 0.737 vs 0.820–0.831), though tied with both on still vs moving for them
- **Weakest on moderate-vigorous activity** (F1 0.588 vs 0.675–0.688, and it finds only 54% of it)
- Its big advantage is size: **0.5 MB**, 30x smaller than XGBoost

**5. Both kinds of evidence agree.** The single 30-person test and the 151-person cross-validation rank the models the same way, and the cross-validation scores are 0.004–0.046 higher than the single test. With 5x more people, the cross-validation can also settle what the single test could not: XGBoost + HMM's lead on still vs moving was a tie on 30 people, and is real on 151.

### Recommendation for SilverGuard
**XGBoost + HMM is the best candidate**, if it fits on the watch. The current model is 15 MB as a text file. A smaller version (depth 4 scored almost the same in Stage 3's tuning) and a compact export should shrink it a lot; that has to be checked at export. **The CNN is the fallback** if size forces it, but it would need work first, mainly for older people and active minutes, before it can be trusted for elderly users.

### What this does *not* tell us yet
- **The HMM hides short bursts of activity.** It lowers how much moderate-vigorous activity is found for every model (forest 66% → 64%, XGBoost 64% → 61%, CNN 56% → 54%), because a short burst looks like a mistake to smooth away. Older people's active bouts are often short, so this needs a closer look
- **The HMM uses the whole day**, including what comes *after* each window. On the watch, live, it can only use the past. A live (forward-only) version needs testing
- **Only labelled windows are scored.** Unlabelled stretches and windows of mixed activity were left out
- **Capture-24's oldest group is 53+**, and most SilverGuard users will be older. Testing on older people (for example GOTOV) is still to do
- **Capture-24 is not the SilverGuard watch.** Different sensor (Axivity vs MPU-6050), same wrist but different hardware
- **One CNN design, one seed.** A better CNN might close the gap; we tested a fair, simple one, not the best possible one
- **Settings were chosen on 121 of the 151 people** before the cross-validation, and XGBoost was added after the single test had been seen once. Both are small advantages, noted for honesty
- **In the cross-validation, "RF" is the GPU forest**, a close stand-in for the Stage 1 forest, not the same model