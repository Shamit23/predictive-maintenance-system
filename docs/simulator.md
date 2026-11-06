# Historical replay simulator (Phase 4a)

`simulator/` replays NASA C-MAPSS FD001 readings through the public ingestion API one cycle at a time. It is a **simulation built
from a historical dataset, not a connection to real aircraft**. It talks to the backend only over HTTP (`POST /api/v1/readings`
plus a few reads), so everything downstream (features, prediction, alerts, dashboard) runs exactly as for any client.

## Run (Windows PowerShell, repo root, venv `.venv`)
```powershell
.\.venv\Scripts\python -m pip install -e simulator              # once (after -e ml -e backend)
# terminal 1: API (cd backend; ..\.venv\Scripts\uvicorn app.main:create_app --factory --port 8000)
# terminal 2:
.\.venv\Scripts\python -m pm_simulator replay --scenario test --engines 24,57 --interval 1.0
.\.venv\Scripts\python -m pm_simulator replay --scenario test --engines 24 --dry-run     # show plan, send nothing
```
Options: `--scenario test|train`, `--engines 24 | 1,5,7 | 1-3,24`, `--interval SECONDS` (default 1.0, `0` = fast as possible),
`--max-cycles N` (per engine, this run), `--id-offset N`, `--api-url` (default `http://127.0.0.1:8000`, or env `PM_API_URL`),
`--raw-dir`, `--env-file`, `--timeout`, `--dry-run`. Exit codes: 0 done, 1 error, 2 bad arguments, 130 interrupted.

**API key:** read from env var `API_KEY`, else from `API_KEY=` in the repo-root `.env`. It is sent only as the `X-API-Key` header and
is never printed; credentials inside `--api-url` are stripped from output.

## Scenarios and engine ids
| Scenario | Source | Default backend id | Ground truth |
|---|---|---|---|
| `test` | `test_FD001.txt` (cut before failure) | unit (offset 0) | none per cycle. `RUL_FD001.txt` value shown **once, after the full observed sequence, labelled EVALUATION ONLY, never sent to the API** |
| `train` | `train_FD001.txt` (run to failure) | unit + 1000 | true RUL = last_cycle − cycle, shown per cycle as **SIMULATED GROUND TRUTH** (not sent to the API) |

Train and test files both number engines 1–100, so ids would collide; hence the offsets. `--id-offset` overrides them
(backend id = unit + offset, max 1,000,000). Replaying an engine that is already stored is a safe no-op (identical duplicates are
idempotent); to replay it as fresh data use a new offset. There is deliberately no reset endpoint.

## What is sent
Exactly the 24 source values (`setting_1..3`, `s_1..s_21`) per cycle, unmodified, in cycle order, plus `engine_id`, `cycle` and
`event_time` = **UTC time of sending** (event metadata only; the dataset has no wall-clock times). Several engines are interleaved
one cycle per tick. Replays are deterministic: the same inputs give identical values and, with the same model, identical predictions.

## Resume, duplicates and gaps
* **Start:** per engine `GET /api/v1/engines/{id}` gives `last_cycle`. If > 0 the simulator re-sends that cycle as a *handshake* and
  requires `200 duplicate`; a `409 conflicting_duplicate` means the id holds different data, so it stops (use `--id-offset`).
  A server with more cycles than the source is also a hard stop. It then resumes at `last_cycle + 1`.
* **200 duplicate** while replaying: counted, replay continues. **201:** counted, prediction/alert shown.
* **409 cycle_gap:** `details.expected_cycle` must be an integer, `1 ≤ E ≤ source length` and `E < cycle` just sent, otherwise the run
  stops with a reconcile error (no resend). A valid hint rewinds to `E`; at most 3 rewinds per engine, then it stops.
* **401, 422, other 4xx/5xx, conflicts:** stop with a clear message (response bodies and the key are never echoed).
* **Transport errors, 502/503/504:** retried with backoff 0.5 s, 1 s, 2 s (4 attempts), then stop. `/ready` is read once at start
  and its failing checks are reported.
* **Ctrl+C:** finishes cleanly with a summary and exit 130; a hard kill is also safe. Re-run the same command to resume.

## Observing
Each line shows the cycle, outcome, predicted RUL, risk, alert action and (train only) simulated truth. The summary adds per-engine
counts, the last prediction, and unresolved alerts (`GET /api/v1/alerts`). Acknowledge alerts through the API (`POST .../acknowledge`).

## Worker mode (Phase 4b): runs controlled through the API
```powershell
.\.venv\Scripts\python -m pm_simulator worker              # long-running; needs API_KEY only (never the control key)
.\.venv\Scripts\python -m pm_simulator worker --once       # execute one queued run, then exit
```
Operators queue, stop and re-pace runs with the control API (`docs/api.md`); the worker does the replay. Options: `--poll-interval 2`,
`--heartbeat-interval 2`, `--heartbeat-grace 10`, `--max-outage 120`, `--api-url`, `--raw-dir`, `--env-file`, `--timeout`.

* **Lifecycle:** poll `claim` -> replay (every reading sent with `X-Simulation-Run`) -> heartbeat every ~2 s (renews the lease, reports progress,
  returns stop requests and speed changes) -> `finish` as completed, stopped or failed. Waits are sliced to 1 s or less so stop and speed changes apply quickly.
* **Stop:** the API shows `stopping` until the worker has finished its current request and confirmed `stopped`.
* **Definitive heartbeat refusal (any other 4xx, i.e. everything except 408 and 429: 401, 404, 409 `lease_lost` / `run_not_active`, 403, 422):** the worker
  stops immediately and reports nothing for that run. These are never retried, so a worker that lost its lease cannot keep working.
* **`claim` failures follow the same split.** Transient ones (HTTP 5xx, 408, 429, or the backend unreachable) are retried every `--poll-interval` seconds, both
  while idle and while re-claiming after an outage (bounded there by `--max-outage`); with `--once` a transient failure while idle exits with code 1. Any other
  4xx (401, 403, 404, 422...) is definitive: the worker prints a clear message (for example `the API refused the worker's claim (HTTP 403)...`) and exits with
  code 1 instead of an uncaught traceback. If this happens while re-claiming a run after an outage, the run is left to time out on the server (`worker_lost`).
* **Transient heartbeat failure (HTTP 5xx, 408, 429, or the backend unreachable):** retried up to 3 times with backoff (0.5 s, 1 s) and it never abandons the
  run by itself; if a stop is already known the retries are skipped so shutdown is not delayed. If heartbeats keep failing for more than
  `--heartbeat-grace` seconds (default 10), the worker pauses ingestion and re-verifies ownership with the atomic `claim`: it resumes only if it still holds
  the run (and honours a stop it learns about then).
* **Timing of that re-check (an estimate, not a guarantee):** the time without a confirmed heartbeat is roughly
  `heartbeat_interval + heartbeat_grace + one failing attempt (+ one long tick)`. A failing attempt costs about 0 s for an HTTP 5xx, about 3.5 s of client
  retries for an unreachable backend, and much more if requests time out. Measured on the real worker code with a virtual clock (no real network) and the
  defaults (2 s + 10 s): **~12.5 s for HTTP 5xx and ~17.5 s for an unreachable backend**, against the server's default lease of 30 s (`SIM_LEASE_SECONDS`). The
  previous default grace of 20 s gave 22.5 s / 26 s (and 27-29 s with a 5 s heartbeat interval), too close to the lease, hence the change. If you lower the
  lease or raise the heartbeat interval or grace, keep that sum comfortably below the lease; the worker cannot see the server's lease setting. A test in the
  backend suite checks that the defaults keep at least 10 s of headroom.
* **After every run** (finished, failed or abandoned) the worker waits `--poll-interval` before polling again, so a run it keeps refusing or losing can never
  turn into a tight claim loop. `--once` returns immediately instead.
* **API outage:** the replay pauses and the worker re-claims the same run for up to `--max-outage` seconds, then gives up locally (the server marks a
  silent run `failed(worker_lost)`). Requests are idempotent, so resuming never duplicates data.
* **Worker crash or restart:** the lease expires (30 s); a new worker takes over and resumes from the data already stored.
* **Ctrl+C:** reports `failed(worker_interrupted)` and exits with code 130. Failures are reported with a code (`id_conflict`, `reconcile_error`,
  `rejected`, `auth_error`, `plan_error`, `worker_error`) and a sanitised message; secrets never appear.
* If no worker is running, a started run stays `queued` (the API says so) and fails with `no_worker` after 60 s.
* **Every run needs engine ids that do not exist yet.** The API refuses a start (`409 engine_ids_in_use`, with the list of free `replay_slot`s) when any of the run's
  engine ids already holds readings, so a repeated run can never "finish" on the previous run's data. Use another `replay_slot` to replay an engine again. This does not
  affect recovery of the same run: a restarted worker resumes the engines its own run created. (The ad-hoc `replay` command above has its own `--id-offset` rules.)

## Limitations
* The ad-hoc `replay` command sends no simulation marker, so its data is stored as ordinary (`api`) data; use the worker for data the backend labels as simulated.
* The resume handshake verifies only the last stored cycle, not the whole stored history.
* Sequential and single-process; `--interval` is a sleep between ticks, not a drift-corrected schedule.
* A closed port takes about 13 s to fail on Windows (retries plus connection delays).
* FD001 only; no drift-injection scenario yet (Phase 5).
