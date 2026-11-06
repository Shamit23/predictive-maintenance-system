# ADR 0001: Simulation control plane (Phase 4b)

Status: accepted; implemented and tested, not yet committed or applied to `pm_dev`. Date: 2026-11-02.

## Context
The UI must start, stop and re-pace the historical replay and show its real state. Phase 4a is a CLI that cannot be controlled remotely and
produces unmarked data. Requirements: a separate process (see `implementation_plan.md`), survive API restarts, no duplicate runs or workers,
never claim "running" without a worker, mark simulated data without mislabelling ordinary ingestion, protect operator actions.

## Decision
* **State in PostgreSQL, execution in a separate worker.** `simulation_runs` holds desired and observed state; `python -m pm_simulator worker` claims
  and executes it through the public ingestion API. Rejected: running the replay inside the API process (it dies with the API, duplicates under
  multiple API workers, and bypasses the public ingestion path).
* **One active run** via a partial unique index on a constant; **atomic claim** (`FOR UPDATE SKIP LOCKED`) with a **lease** renewed by heartbeats;
  expiry is applied lazily inside API transactions using the database clock (no background threads). A run is `running` only after a claim.
* **Two secrets.** `CONTROL_API_KEY` (`X-Control-Key`) authorises start, stop and speed changes; `API_KEY` (`X-API-Key`) authorises ingestion and the worker
  protocol. Different powers, constant-time comparison, never logged or returned; operator endpoints fail closed (503) if the control key is unset.
* **Marker = header + immutable engine origin.** `X-Simulation-Run` is validated against the run; `engines.origin` is set at creation and mismatches are
  rejected (409), so data is never mixed and real engines cannot be relabelled. A header (not a body field) keeps the existing contract unchanged.
* **Server-derived ids and limits.** Clients choose scenario, engine numbers 1..100 and slot 0..9; the server computes ids and enforces count and interval bounds.
* **Engine ids are single-use.** Ids follow `unit + scenario base + 10,000 x slot` (collision-free, max 91,100). Because engine records are never deleted, creating a
  run is refused (`409 engine_ids_in_use`) when any of its ids already exists, so a new run can never silently reuse a previous run's (or an ad-hoc replay's) engine
  records. Rejected alternatives: allocating ids from the run id (a larger contract change with no extra safety once the check exists) and silently continuing the
  old data (hides that the run produced nothing new).
* **Additive migration `0002`.** New tables and columns are nullable or defaulted, so application code can be rolled back without downgrading.

## Consequences
* A worker process must be running; otherwise a started run stays `queued` (visibly) and then fails `no_worker`.
* Ad-hoc CLI replays and engines created before `0002` are `origin='api'` (provenance unknown; not claimed to be simulated).
* Per-engine progress is worker-reported; stored-reading counts come from the database.
* Rollback: stop the worker, revert the code (it works against the `0002` schema), optionally `alembic downgrade 0001` (drops run history and origin labels).
* Single shared secrets suit only a trusted local environment; there is no user identity or audit trail of who started a run.
* Lazy expiry means run state is corrected on the next API access, not at the exact timeout instant.
* Each (scenario, engine, slot) serves one run for the lifetime of the database: 10 fresh replays per scenario and engine. Raising the slot bound would need the schema
  limit and the DB check changed (the 10,000 stride would fit slots up to about 98 under the 1,000,000 id limit); there is deliberately no delete/reset endpoint.

## Frontend: entering the control key safely (for Phase 3)
Never embed either key in source, `VITE_*` variables or the bundle. The UI asks the operator for the control key at runtime, keeps it **in memory only**
(React state; not `localStorage`, `sessionStorage` or cookies), sends it solely as `X-Control-Key` on start/stop/speed calls, and discards it on reload or
"lock". Read-only pages need no key, and the browser never needs `API_KEY`. Local development: run the API on 127.0.0.1 with `CORS_ORIGINS` set to the
dev-server origin.
