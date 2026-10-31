# Data dictionary: NASA C-MAPSS FD001

**Source:** https://data.nasa.gov/docs/legacy/CMAPSSData.zip (NASA Open Data, "CMAPSS Jet Engine Simulated Data").
Downloaded zip: 12,425,978 bytes, SHA-256 `74BEF434A34DB25C7BF72E668EA4CD52AFE5F2CF8E44367C55A82BFD91A5A34F`.
Extract into `ml/data/raw/` (gitignored, not redistributed). Training embeds per-file SHA-256 in each artifact's `metadata.json`.
Reference: Saxena et al., "Damage Propagation Modeling for Aircraft Engine Run-to-Failure Simulation" (PHM08; PDF is in the zip).

**This is simulated data**, not recordings from real aircraft.

## Files (FD001: 1 operating condition, 1 fault mode — HPC degradation)
| File | Content |
|---|---|
| `train_FD001.txt` | 100 engines run to failure, 20,631 rows |
| `test_FD001.txt` | 100 engines, trajectories **cut before failure**, 13,096 rows |
| `RUL_FD001.txt` | 100 lines: true RUL *after the last observed test cycle*; line *i* = test engine *i* |

Whitespace-delimited, no header, trailing spaces. 26 columns: `unit`, `cycle`, `setting_1..3`, `s_1..s_21`.
`unit` = engine id; `cycle` = operating cycle (1, 2, ... consecutive; validated on load); settings = altitude / Mach / throttle-resolver
angle; `s_1..s_21` = sensor channels (temperatures, pressures, speeds, flow ratios; numbering follows the NASA readme, the
physical meaning of each is in the PHM08 paper).

## Findings measured on FD001 (see `ml/tests/test_data_labels_metrics.py`)
- No missing / non-finite values (enforced by the loader).
- Constant in train **and** test: `setting_3, s_1, s_5, s_10, s_16, s_18, s_19`. `s_6` has only 2 distinct values -> excluded.
  Settings 1/2 are excluded (single operating condition).
- **14 sensors used:** `s_2, s_3, s_4, s_7, s_8, s_9, s_11, s_12, s_13, s_14, s_15, s_17, s_20, s_21`.
- Train lifetimes: min 128, mean 206.3, max 362 cycles. Test last observed cycle: 31–303. True test RUL: 7–145 (11 of 100 > 125).

## Labels
- Train: `rul = last_cycle(engine) - cycle` (0 at failure). Regression **target = min(rul, 125)** (documented modelling choice).
- Failure classification: positive (1) iff **uncapped** true RUL <= 30.
- Test labels are only ever used for evaluation, never as inputs.

## Evaluation protocol
- Validation: 20 of the 100 training engines held out by **engine** (seed 42); no engine appears on both sides.
- Test: features computed from each test engine's history, prediction at its **last observed cycle**, compared with `RUL_FD001.txt`
  (uncapped; also reported against capped labels).
- Only the model trained on the 80 training engines is used; test data is not used for tuning or early stopping.

## Not done yet
A full exploratory notebook (distributions per sensor, correlation, degradation plots) is **not** written; only the checks above were run.
