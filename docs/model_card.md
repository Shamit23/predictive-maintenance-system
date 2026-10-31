# Model card: FD001 RUL model (Milestone 1)

**Intended use:** portfolio demonstration of decision support on *simulated* turbofan data. Not for real maintenance decisions.

**Model:** XGBoost regressor (RUL, target capped at 125) + XGBoost classifier (failure within 30 cycles). Inputs: 14 sensors +
`cycle` + causal rolling mean/std/min/max (windows 5/10/20) + 10-cycle slope + `history_len` (198 features, canonical order in
`pm_ml.features.feature_names`). Seed 42, hyperparameters in artifact `metadata.json`. Trained on 80 engines.

Artifacts are git-ignored; regenerate with `python -m pm_ml.training` (run from `ml/` with the venv active). A retrain writes a new timestamped version, so numbers may differ slightly across library versions.

**Results (actual run, artifact `fd001-xgb-20261031T232325Z`)** — test = last cycle of the 100 FD001 test engines vs `RUL_FD001.txt`:

| Model | MAE | RMSE | NASA score (lower=better) |
|---|---|---|---|
| Constant (mean capped RUL) | 35.91 | 43.24 | 35,722.9 |
| Ridge on raw 14 sensors | 17.43 | 21.65 | 1,279.8 |
| **XGBoost, engineered features** | **12.94** | **17.98** | **600.4** |

Validation (20 held-out engines, all cycles, vs capped target): XGBoost MAE 9.68, RMSE 14.63.
These are single-split, single-seed numbers on 100 test engines; no confidence intervals, and no claim about published results.

**Failure within 30 cycles (positive = RUL <= 30)**, test last cycle (25 positives of 100):
precision 0.95, recall 0.76, F1 0.84, confusion `[[TN 74, FP 1], [FN 6, TP 19]]`. Validation all cycles: P 0.92 / R 0.86 / F1 0.89.
The classifier score is **uncalibrated**; do not read it as a probability. Test set is small (25 positives), so these are noisy.

**Costs:** a missed failure (FN, late prediction) is more harmful than an unnecessary inspection (FP), which is why the NASA score
penalises late predictions more. Here 6 of 25 imminent failures were missed at the default 0.5 threshold; the threshold has not been tuned.

**Limitations:** simulated single-fault, single-condition data; the 125-cap means predictions above 125 are impossible
(11 test engines have true RUL > 125); no hyperparameter search; no cross-validation; not evaluated on FD002–FD004.
