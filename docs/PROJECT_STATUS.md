# Project status

_Last updated: 2026-11-01. Status words: **Tested** = automated tests were run and passed; **Verified live** = exercised against a running server;
**Not tested** = code exists but was not exercised._

## Phase 1: ML core (committed: `c650030`) — Tested
`ml/src/pm_ml`: parser, labels (cap 125), causal features (198), XGBoost regressor + classifier, baselines, metrics (MAE/RMSE/NASA score),
pickle-free artifacts, predictor. Results in `docs/model_card.md`. 29 ML tests pass.

## Phase 2: Backend and persistence (this work, **uncommitted**)
| Item | Status |
|---|---|
| Config from env/.env, masked secrets, `.env.example` | Tested |
| SQLAlchemy models + Alembic `0001` (engines, sensor_readings, model_versions, predictions, alerts, ingestion_errors) | Tested (suite does downgrade-to-base + upgrade-to-head on `pm_test`); applied to `pm_dev` |
| Constraints: unique (engine,cycle), single active model, one unresolved alert/engine, FK/check | Tested |
| Model registry: explicit > DB-active > sole artifact; fails on none/ambiguous/invalid/skewed | Tested |
| Ingestion: validation, API key, idempotent duplicates, 409 conflicts and gaps, transactional prediction + alert, error recording | Tested |
| Prediction via shared `pm_ml` code; insufficient-history behaviour | Tested; **verified live** (API result equals standalone Milestone 1 predictor on real FD001 data) |
| Alert lifecycle + acknowledge (persisted, idempotent, API key) | Tested |
| Engines/readings/predictions/models/alerts read endpoints, paging, filters | Tested |
| Health/readiness, 503 when DB down or model missing, CORS, error envelope without leaks | Tested |
| Concurrent ingestion (row-lock design) | **Not tested** under parallel requests |
| Docker, simulator, frontend, drift, retraining, promotion/rollback endpoints | Not started (later phases) |

### Test results (actual runs, 2026-11-01)
- Backend: `82 passed, 0 skipped` (~25 s, against PostgreSQL `pm_test`).
- ML: `29 passed`.
- Live smoke: server on `pm_dev`, 186 FD001 readings for test engine 24 posted over HTTP -> prediction 22.995 (true RUL 20), critical alert opened,
  no-key 401, duplicate 200, gap 409, `/ready` all ok, server log contained no passwords/URLs/tracebacks.
- Pre-commit review fix: migration `0001` now declares the 7 timestamp columns NOT NULL (matching the ORM) and names all 7 foreign keys per the ORM
  naming convention. `alembic check` reports "No new upgrade operations detected" (before: 7 nullable mismatches). Re-run after the fix:
  backend 82 passed, ML 29 passed.
- Bugs found and fixed while testing: registry opened a transaction after querying; API mixed UTC and local-offset timestamps (now normalised to UTC);
  three test-code defects (leaked connection that hung the suite, helper argument clash, wrong engine length assumption).

## Commands (Windows PowerShell, repo root, venv `.venv`)
```powershell
.\.venv\Scripts\python -m pip install -e ml -e backend         # once
Copy-Item .env.example .env                                     # then edit passwords; never commit .env
cd backend
..\.venv\Scripts\alembic upgrade head                           # migrate DATABASE_URL (pm_dev)
..\.venv\Scripts\python -m pytest                               # backend tests (needs TEST_DATABASE_URL, db name ends _test)
..\.venv\Scripts\uvicorn app.main:create_app --factory --port 8000
cd ..\ml; ..\.venv\Scripts\python -m pytest                     # ML tests
cd ml; ..\.venv\Scripts\python -m pm_ml.training                # (re)train an artifact into ml/artifacts
```
Docs: `docs/api.md` (endpoints, errors, alert lifecycle). Interactive API: http://127.0.0.1:8000/docs while running.

## Known issues / limitations
- `pm_dev` is empty at revision `0001` (it was reset after the pre-commit review; the earlier smoke-test data was disposable and is gone).
- Alerts: no hysteresis (RUL hovering at a threshold can resolve/reopen); severity not lowered while open. Thresholds are demo values.
- Prediction runs inside the ingestion request and holds a per-engine row lock; fine for one engine step (ms), unmeasured under load.
- `POST /predict` and all GET endpoints are unauthenticated and unrate-limited; the API key is a shared secret, not user auth.
- Ingestion requires strictly consecutive cycles per engine; a lost message blocks that engine until the gap is filled (by design, documented).
- If `event_time` is omitted the server uses receipt time.
- Models are registered only for the artifact chosen at startup; candidate/archived registration, promotion and rollback come in Phase 6.
- Starlette emits a deprecation warning about `httpx` in the test client (cosmetic).
- Phase 1 leftovers: no full EDA notebook; no README yet.

## Exact next step
Review and approve the Phase 2 diff (`git status` / `git diff`), then commit and push it. After that, **Phase 3 (React frontend)** or,
if you prefer the data flow first, **Phase 4 (simulator + replay control)** so the dashboard has live data to show.
