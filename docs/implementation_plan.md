# Implementation Plan (Phase 0)

## 1. Confirmed environment (checked, not assumed)

| Item | State |
|---|---|
| OS / shell | Windows 11, PowerShell (Git Bash also available) |
| Repo | Was empty, not a git repo -> `git init -b main` done, no commits yet |
| Python | 3.11.9 (`F:\python.exe`); system pip missing, but `python -m venv` works and bundles pip 24.0. `uv` also installed |
| Node / npm | v24.15.0 / 11.12.1 |
| Git | 2.53; identity configured |
| **Docker** | **NOT installed** (no `docker`, no WSL) |
| PostgreSQL | not installed locally |
| Network | NASA and PyPI reachable |
| Disk | ~43 GB free on F: |

Dataset: official NASA link `https://data.nasa.gov/docs/legacy/CMAPSSData.zip` (linked from the NASA Open Data
page "CMAPSS Jet Engine Simulated Data"). Downloaded zip: 12,425,978 bytes, SHA-256
`74BEF434A34DB25C7BF72E668EA4CD52AFE5F2CF8E44367C55A82BFD91A5A34F`. Extracted to `ml/data/raw/` (gitignored).
FD001 sanity check: `train_FD001.txt` has 20,631 rows, `RUL_FD001.txt` has 100 labels. Files are whitespace-delimited with
trailing spaces and no header (26 columns: unit, cycle, 3 settings, 21 sensors).

## 2. Blockers / constraints

1. **Docker is not installed.** Phases 0-6 can be built and tested locally with no Docker. Phase 7 (Compose, persistence
   across restarts, containerized smoke test) cannot be verified until Docker Desktop is installed. I will write the
   Dockerfiles/Compose but mark them *implemented, not tested* until you install Docker.
2. **PostgreSQL is not installed.** Backend tests will use SQLite for fast unit tests *and* Postgres for integration,
   once available (Docker or a local install). Models stay Postgres-compatible (no SQLite-only features).

## 3. Architecture

```
simulator (Python) --HTTP--> FastAPI ingestion --> PostgreSQL
                                   |                  ^
                                   v                  |
                        feature service --> prediction service --> predictions, alerts
                                   |
             drift monitor <-------+--> retraining/promotion --> MLflow (tracking + registry)
React SPA --HTTP polling--> FastAPI (/api/v1/*)
```

- `ml/src/pm_ml` is an installable package holding feature definitions, labels, metrics, training, and inference.
  The backend imports the *same* feature code, which is how training-serving skew is prevented.
- Simulator is a separate process that talks to the backend only through the public ingestion API plus a control
  endpoint. Backend owns simulation state in DB so UI controls reflect real state.
- Single backend process, synchronous prediction inside the ingestion request (one engine step is milliseconds with
  XGBoost). Retraining runs in a background thread with DB-recorded status. No Redis/Celery/Kafka.

## 4. Key decisions and trade-offs

- **Row-level XGBoost on engineered features** (current sensors + rolling mean/std/min/max + slope, windows 5/10/20,
  causal only). Early cycles use `min_periods=1` windows; std/slope fall back to 0 with an explicit `history_len` feature.
- **RUL cap 125** as a documented modeling choice; also evaluate uncapped for comparison.
- **Splits**: GroupKFold / group train-val split by engine id. Scaler/reference profile fit on train only.
- **Test evaluation**: last cycle of each test engine vs `RUL_FD001.txt` (standard protocol), plus all-cycle view is
  explicitly *not* reported as the headline metric.
- **NASA score**: d = pred - true; s = sum(exp(-d/13)-1) for d<0, sum(exp(d/10)-1) for d>=0.
- **Classification**: positive = RUL <= 30 (true RUL, uncapped-consistent). Reported with P/R/F1/confusion matrix; no
  calibration claim without calibration.
- **Drift**: KS test per feature against stored training reference, with minimum sample size and Bonferroni/BH correction
  plus effect size (KS statistic) threshold. Reports *data drift only*.
- **Replay scenarios**: (a) FD001 training engines, which have known failure cycle, so simulated ground-truth RUL is
  legitimate; (b) FD001 test engines, truncated before failure, no ground truth shown. Drift scenario: a documented
  sensor offset injected into replay.
- **Promotion gate**: candidate must beat active on validation MAE by a margin, not regress NASA score beyond a
  tolerance, and pass data validation. Drift never auto-promotes.
- **Auth**: simple API-key header for control/model-management endpoints (config via env).

## 5. Planned structure

As in your spec; `ml/src/pm_ml/` replaces the sub-folder fan-out (`ingestion/preprocessing/...`) until module sizes justify
splitting. `ml/src/pm_ml/{data,features,labels,metrics,training,inference}.py`. Frontend directory created in Phase 3.

## 6. Initial dependencies

Python: numpy, pandas, scikit-learn, xgboost, pytest, joblib (later fastapi, uvicorn, sqlalchemy, alembic,
psycopg, pydantic-settings, mlflow). Pinned in lockfile via `uv pip compile` or `pip freeze` at each milestone.

## 7. Milestone 1 (Phase 1) acceptance criteria

Status after implementation (tests: `cd ml; ..\.venv\Scripts\python -m pytest -q`):
- [x] Parser returns correct columns/shapes for FD001 (tests)
- [x] RUL labels + cap verified by tests
- [x] Feature builder is causal (leakage test) and has fixed column order
- [x] Constant + Ridge baselines and XGBoost trained reproducibly (fixed seed; reproducibility test)
- [x] MAE, RMSE, NASA score on FD001 test last-cycle; classification P/R/F1/confusion
- [x] Versioned artifact + metadata JSON saved (git-ignored; regenerate with `python -m pm_ml.training`); reload predictions match in-memory model
- [x] Standalone prediction from raw history works
- [~] Data dictionary done; full EDA notebook **not done** (only basic checks)
