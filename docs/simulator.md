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

## Limitations
* Not yet a controllable service: no start/stop/speed/status endpoints for the UI (planned Phase 4b), and the backend has no field
  marking data as simulated.
* The resume handshake verifies only the last stored cycle, not the whole stored history.
* Sequential and single-process; `--interval` is a sleep between ticks, not a drift-corrected schedule.
* A closed port takes about 13 s to fail on Windows (retries plus connection delays).
* FD001 only; no drift-injection scenario yet (Phase 5).
