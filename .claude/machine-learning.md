# ML Training & Deployment Rules

These rules apply to any project which leverages ML. They are the
distilled lessons from this codebase's training pipeline plus the
post-mortems of bugs that actually happened in production.

---

## 1. Decision Parameter Discipline (Single Source of Truth)

The decision threshold of a classifier — what counts as "model said yes" —
is a parameter, not a constant. It belongs to ONE function or config
entry. Every consumer imports it. NEVER duplicate.

This applies to:
- Production inference (`predict() -> decide()`)
- Training data labelling (which historical predictions count as positives)
- Evaluation metrics (precision/recall/F1)
- Dashboards, reports, alerts

If even one of these uses a different cutoff, your metrics describe a
classifier you are not running, and your retraining loop optimizes for
the wrong objective. Silently. For months.

**Forbidden**: magic-number literals (anything except 0, 1, -1) on the
right side of `>=`, `<=`, `>`, `<` in any decision/labelling code path.
Enforce with a regression test that greps the codebase.

---

## 2. Don't Use 0.5 as a Threshold

0.5 is a textbook default that almost never matches reality. Compute
the operating threshold from a held-out validation set:

- Optimize for the metric the business actually cares about
  (F1, F-beta, expected value, precision-at-k — pick deliberately).
- Sweep candidate thresholds (e.g. 0.01..0.99 step 0.01).
- Store the chosen threshold INSIDE the model artifact, not as a
  separate config value that can drift.
- Re-derive on every retraining. Treat it as part of the model.

---

## 3. Calibrate Probabilities

Raw classifier outputs from imbalanced training are not real
probabilities. Before any threshold logic runs, calibrate:

- Use a SEPARATE calibration set, not the training set.
- The calibration set must reflect the true class distribution
  (i.e. UNBALANCED — don't apply training-side resampling to it).
- Isotonic regression for ≥1000 samples, Platt/sigmoid below.
- Verify: print min/median/max of raw vs calibrated probabilities.
  If calibrated probs aren't roughly centered around the true base
  rate, calibration is broken or the cal set is wrong.

---

## 4. Three-Way Split: Train / Calibrate / Test

For any classifier with calibration:
- Train (≈60%): fit the model
- Calibrate (≈20%, UNBALANCED): fit the probability calibrator
- Test (≈20%, UNBALANCED): final evaluation, NEVER touched during
  model selection

Cross-validation is supplementary and informational. The held-out
test set is non-negotiable. If you tuned anything against it, it's
no longer a test set.

---

## 5. Class Imbalance: Resample Train Only

Never resample the calibration or test set. Only the training set
gets balanced, and the resampling ratio is itself a hyperparameter:

- Track which ratio is in use in the model metadata.
- Drive ratio adjustments from production feedback (precision too
  low → more negatives; recall too low → fewer negatives), not from
  intuition.
- Use deterministic seeds for any sampling so training is
  reproducible.

---

## 6. Config Hash: Detect Silent Drift

Hash the relevant config (feature list, label definition, regime
buckets, lookahead window, etc.) and store the hash IN the model
artifact. On load, compare against the current code's hash and
WARN loudly on mismatch.

A model trained with one feature definition and served by code
expecting another is a silent precision killer. The hash makes it
loud.

---

## 7. Close the Feedback Loop with Real Outcomes

Simulated/backtested labels from historical data are not enough.
Every production prediction the model makes is a potential training
sample once the outcome is known.

- Persist every prediction with: model version, threshold, all
  inputs, and the gate/business decision that followed.
- Periodically pull the matured outcomes and feed them back as
  training samples.
- Weight real outcomes HIGHER than simulated ones (e.g. 5x).
  Real outcomes are scarce but they're the ground truth.
- Filter by the SAME threshold the production system used to
  decide — not the textbook 0.5 (see Rule 1).

---

## 8. Pre-Training Feedback Measurement

Before retraining, measure how the OLD model performed on its real
production outcomes. Use that measurement to adjust retraining
hyperparameters (balance ratio, sample weights, regularization).

This turns retraining into a closed control loop instead of an
open-loop "fit again on more data".

---

## 9. Surface Decision Parameters in Every Output

Every metrics report, every prediction log, every dashboard tile
that contains "precision: X%" must also contain the threshold and
model version it was computed with. Otherwise the number is
meaningless and someone will eventually optimize against the wrong
operating point.

---

## 10. Reproducibility Hygiene

- `random_state=42` (or any constant) on every estimator, splitter,
  and sampler. Non-determinism in training is a debugging nightmare.
- Pin library versions in the dependency file — `scikit-learn`
  changes calibration internals between minor versions.
- Capture the data window (start/end timestamps) in the model
  artifact. "Trained on 300 days" is meaningless without knowing
  WHICH 300 days.

---

## 11. Auto-Reload, Don't Restart

Production inference code should detect a new model file (mtime)
and reload it without a process restart. Restarts couple model
deployment to deployment cycles and discourage frequent retraining.

---

## 12. Two Numbers, Always: Holdout AND Production

The "official" precision/recall comes from the held-out test set
at training time. The "honest" precision/recall comes from real
production outcomes after deployment. They will differ. The gap
is your distribution-shift signal.

If holdout says 60% and production says 9%, your test set is
unrepresentative — fix the data pipeline before you touch the model.

---

## 13. Sample Stride ≥ Lookahead Window

For time-series labels with a forward-looking window of length `L`,
the spacing between training samples MUST be `≥ L`. Otherwise
adjacent samples share most of their lookahead window and the model
sees the same future event multiple times — once as label, again as
neighbour-correlated feature drift.

Block-level chronological splits (Train | Embargo | Cal | Embargo |
Test) only solve INTER-block leakage. Without sufficient stride,
INTRA-block leakage remains and inflates holdout precision while
hiding the true generalization error.

**Symptom**: holdout precision >2× higher than honest production
precision even after a chronological split with embargo.

**Test**: compute Kendall's τ on the autocorrelation of labels
within each fold. Should be ≈0. If it's high, samples aren't
independent.

---

## 14. Label Semantics Must Be Invariant Under Environment Shift

If the definition of "positive class" depends on a slow-moving
environment variable (regime, season, traffic mix, user cohort),
the positive-class distribution silently shifts as the environment
shifts — even if the underlying patterns the model should learn
don't.

The model trained on a mixed-semantics window then represents an
average of two different problems and generalizes well to neither.
Calibration breaks first, precision follows.

**Forbidden**: label definitions that include the current value of
anything you can't predict at training time.

**Acceptable**: a single fixed cutoff (e.g. "rose 10% within 24h")
that may produce different per-period base rates but never changes
what "positive" means.

---

## 15. Anything That Defines the Label Must Be a Feature (or a Constant)

If variable `X` enters the label-generation function, the model has
exactly two acceptable relationships to `X`:

1. `X` is constant across all samples → can be treated as implicit.
2. `X` is exposed to the model as a feature → can be conditioned on.

Anything else is a hidden confounder. The model is asked to predict
an outcome whose definition depends on a value it was never shown.

**Concrete failure mode from this codebase (D-054)**: regime
determined the TP/SL targets used to compute labels, but regime
itself was not in the feature vector. The model could not condition
on which "type of label" it was being shown.

---

## 16. Validate the SHAPE of the Decision Function, Not Just the Threshold

Before tuning a threshold, plot outcome vs. predicted score on
recent data. Look for monotonicity. A threshold-based gate
(`predict if score >= T`) makes a strong implicit assumption: that
expected outcome is monotone increasing with score. If the curve is
non-monotone — sweet spot in the middle, decay at the extremes —
NO single threshold is the right gate; you need bucket-lookup or
calibrated rank logic.

**Forbidden**: tuning threshold values without first plotting the
shape they assume.

**Test**: bucket recent decisions by predicted score (e.g. 5-10 bins)
and report avg outcome per bucket. If the trend isn't monotone, the
gate structure is wrong, not the cutoff.

---

## 17. Per-Decision Impact > Hit-Rate When N is Small or Outcomes are Asymmetric

Win-rate / hit-rate flattens magnitude. A bucket with 10 decisions
at 30% win rate looks similar to one at 35% — but if the 30% bucket's
losers are 10× larger than its wins, the per-decision EUR/USD/utility
impact tells the real story.

For any decision-quality analysis, report at least:
- Count
- Win-rate (or hit-rate)
- **Per-decision expected outcome** (avg gain per call, avg utility
  per recommendation, avg revenue per impression)
- Sum-of-outcomes for the cohort

Skipping per-decision impact is how a 7%-of-decisions / 83%-of-loss
bucket goes undetected for weeks.

---

## 18. Shadow-Mode-First. Always.

Any new gate, filter, or scoring component ships in shadow mode
first: it computes the decision, logs what it WOULD have done, but
the live system continues to operate as before. Only after data
proves the new component decides correctly does it switch to enforce.

This is non-negotiable, even for "obviously correct" changes.
Especially for those.

**Cost of shadow-first**: a feature flag, a few logs, sometimes a
diagnostic table. Negligible.

**Cost of skipping it**: discovering the new gate blocks 100% of
your traffic in production — at which point the only options are
emergency rollback or extended outage.

When the shadow data eventually proves the new component is wrong,
the withdrawal is cheap (delete the feature flag, write a decision
record). The shadow phase IS the experiment, not preparation for
the experiment.

---

## 19. Diagnostic Tooling Outlives the Feature It Was Built For

Build diagnostic scripts to analyze the DOMAIN, not the specific
feature you're currently shipping. Write the analysis against the
underlying data sources (predictions table, outcomes table, etc.),
not against the new component's logs. Then when the new component
gets withdrawn, the analyzer keeps working.

**Concrete pattern**: the precision-floor shadow log was deleted
when the floor was withdrawn (D-053), but the analyzer that
reconstructed shadow decisions from `ml_predictions` survived and
became the validation tool for the next model version.

---

## 20. Sophistication Multiplies Risk

Each "smart" addition to a model pipeline (calibration,
auto-thresholding, regime-aware labels, sample weighting, ensemble
stacking) brings its own assumption set. When the assumption holds,
the addition helps. When it breaks, the addition makes things
WORSE than not having it.

A simpler baseline classifier with no calibration is often more
robust under distribution shift than a heavily-tuned pipeline whose
three layers of cleverness all assume stationarity.

**Rule of thumb**: each addition needs an explicit "this assumption
must hold" comment + a regression alert if the assumption can be
measured. No alert, no addition.

---

## Anti-patterns to refuse on sight

- `if prob >= 0.5:` anywhere
- Different code paths computing the same metric with different
  cutoffs
- "We'll calibrate later" (no, you'll forget)
- Test set used for hyperparameter tuning
- Model file without embedded threshold + config hash + version
- Retraining schedule with no feedback measurement of the previous
  model
- Resampling applied to the test or calibration set
- Missing `random_state` on any sklearn estimator
- Sample stride less than the label lookahead window
- A variable that determines the label but is not a feature
- Threshold tuning before the outcome-vs-score curve has been plotted
- Win-rate quoted without per-decision impact (avg outcome per call)
- New gate/filter going to enforce without a shadow-mode observation
  period of at least one full business cycle
- Diagnostic tools whose only data source is the feature being
  diagnosed
