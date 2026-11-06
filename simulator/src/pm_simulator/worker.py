"""Simulation worker: a long-running process that executes the run the API has queued.

    python -m pm_simulator worker            # needs API_KEY only (never the operator CONTROL_API_KEY)

Loop: poll `claim` -> replay with the 4a core (every reading marked with X-Simulation-Run) -> heartbeat every
couple of seconds (renews the lease, reports progress, learns about stop requests and speed changes) -> `finish`.

Reliability rules
  * The API owns the desired state; the worker only executes it. A run is 'running' only after this worker claimed it.
  * A definitive heartbeat refusal (any other 4xx, e.g. 401 / 404 / 409 lease_lost / run_not_active) means someone
    else owns the run, it ended, or the key is wrong: stop immediately and do NOT report anything for it. Never retried.
  * `claim` failures follow the same split. Transient ones (HTTP 5xx / 408 / 429, or the backend unreachable) are
    retried every `poll_interval` seconds, both while idle and while re-claiming after an outage (bounded there by
    `max_outage`). Any other 4xx (401, 403, 404, 422...) is definitive: the worker logs a clear message and exits with
    code 1, never with an uncaught traceback.
  * Transient heartbeat failures (HTTP 5xx / 408 / 429, or the backend unreachable) are retried a few times with
    bounded backoff and never abandon the run by themselves. If heartbeats keep failing for longer than
    `heartbeat_grace` seconds, ingestion is paused and ownership is re-verified through the atomic `claim`: the
    replay resumes only if this worker still holds the run (and honours a stop it learns about then).
    This is NOT a hard bound: the time without a confirmed heartbeat is roughly
        heartbeat_interval + heartbeat_grace + one failing attempt (+ one long tick)
    where a failing attempt costs ~0 s for an HTTP 5xx, ~3.5 s of client retries for an unreachable backend and
    much more if requests time out. Measured with the defaults (2 s + 10 s): ~12.5 s for HTTP 5xx, ~17.5 s for an
    unreachable backend, against the server's default 30 s lease (SIM_LEASE_SECONDS). If you lower the lease or raise
    the heartbeat interval or grace, keep that sum comfortably below the lease.
  * After a run ends for ANY reason (finished, failed, abandoned) the worker waits `poll_interval` before it polls
    again, so a run it keeps losing or refusing can never turn into a tight claim loop.
  * If the API becomes unreachable the replay is paused and the worker re-claims the SAME run (it still owns the
    lease) for up to `max_outage` seconds, then gives up on it locally. The server marks a silent run failed.
    Every request is idempotent, so resuming after an outage or a restart never duplicates data.
  * Ctrl+C reports the run as failed(worker_interrupted) and exits with code 130.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable

from pm_simulator.replay import (
    ApiClient, AuthError, ConflictError, ReconcileError, RejectedError, ReplayError, ReplayResult, ServerUnavailable,
    build_plan, is_transient_status, run_replay,
)

HEARTBEAT_ATTEMPTS = 3      # tries per heartbeat before waiting for the next tick
HEARTBEAT_BACKOFF = 0.5     # seconds; doubles per retry (0.5, 1.0)
DEFAULT_HEARTBEAT_GRACE = 10.0   # see the module docstring: measured ~12.5-17.5 s without a heartbeat vs a 30 s lease

FAILURE_CODES = {AuthError: "auth_error", ConflictError: "id_conflict", ReconcileError: "reconcile_error",
                 RejectedError: "rejected"}


def new_worker_id() -> str:
    return "w-" + uuid.uuid4().hex[:16]


def _progress(result: ReplayResult) -> dict:
    out = {}
    for s in result.plan:
        st = result.engines[s.engine_id]
        last = st.last_event.cycle if st.last_event else st.resumed_after
        out[str(s.engine_id)] = {"unit": s.source_unit, "n_cycles": s.n_cycles, "last_cycle": last,
                                 "created": st.created, "duplicates": st.duplicates, "rewinds": st.rewinds,
                                 "complete": st.complete}
    return out


class _RunState:
    """Mutable facts about the run being executed, shared with the replay hooks."""

    def __init__(self, interval: float):
        self.interval = interval
        self.stop = False
        self.abandon = False
        self.last_beat = float("-inf")
        self.result: ReplayResult | None = None
        self.base = {"created": 0, "duplicates": 0}   # counts from segments before an outage/resume
        self.base_errors = 0
        self.hb_fail_since: float | None = None   # monotonic time of the first failure in the current heartbeat streak


def run_worker(api: ApiClient, *, worker_id: str, raw_dir=None, poll_interval: float = 2.0,
               heartbeat_interval: float = 2.0, max_outage: float = 120.0, once: bool = False,
               heartbeat_grace: float = DEFAULT_HEARTBEAT_GRACE,
               sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
               log: Callable[[str], None] = print) -> int:
    """Run until interrupted (or, with once=True, after one run / when idle). Returns a process exit code."""
    try:
        while True:
            try:
                plan = api.claim(worker_id)
            except ServerUnavailable as e:  # unreachable, or a transient HTTP error (5xx / 408 / 429)
                log(f"backend unavailable ({e}); will retry in {poll_interval:g}s")
                if once:
                    return 1
                sleep(poll_interval)
                continue
            if plan is None:
                if once:
                    return 0
                sleep(poll_interval)
                continue
            if _execute(api, plan, worker_id, raw_dir, heartbeat_interval, max_outage, sleep, monotonic, log,
                        poll_interval, heartbeat_grace) == 130:
                return 130
            if once:
                return 0
            # Never re-claim in a tight loop after an abandoned/failed run (e.g. the server keeps refusing our
            # heartbeats while the run is still ours). The claim itself still re-checks ownership atomically.
            sleep(poll_interval)
    except KeyboardInterrupt:
        log("worker interrupted")
        return 130
    except ReplayError as e:
        # Definitive failures (wrong API key, a claim refused with another 4xx...): retrying cannot help, so report
        # clearly and exit with code 1 instead of an uncaught traceback. Messages never contain the key.
        log(f"ERROR: {e}")
        return 1


def _finish(api, run_id, worker_id, outcome, log, code=None, message=None) -> None:
    payload = {"worker_id": worker_id, "outcome": outcome}
    if outcome == "failed":
        payload.update(failure_code=code or "worker_error", failure_message=(message or "Worker error.")[:500])
    try:
        r = api.finish(run_id, payload)
        log(f"run {run_id}: reported {outcome}" + ("" if r.status_code == 200 else f" (server answered HTTP {r.status_code})"))
    except ReplayError as e:
        log(f"run {run_id}: could not report {outcome} ({e}); the server will time the run out")


def _reconnect(api, worker_id, run_id, state, max_outage, poll_interval, sleep, monotonic, log) -> bool:
    """After an outage: re-claim the same run (we still hold its lease). False => give up on it."""
    deadline = monotonic() + max_outage
    while monotonic() < deadline:
        try:
            plan = api.claim(worker_id)
        except ServerUnavailable:
            sleep(poll_interval)
            continue
        if plan is None or plan["run_id"] != run_id:
            log(f"run {run_id}: no longer ours after the outage; abandoning it")
            return False
        state.interval = plan["interval_seconds"]
        state.stop = state.stop or plan["status"] == "stopping"
        log(f"run {run_id}: reconnected, resuming")
        return True
    log(f"run {run_id}: backend unreachable for more than {max_outage:.0f}s; giving up on this run locally")
    return False


def _execute(api, plan, worker_id, raw_dir, heartbeat_interval, max_outage, sleep, monotonic, log,
             poll_interval, heartbeat_grace=DEFAULT_HEARTBEAT_GRACE) -> int | None:
    run_id = plan["run_id"]
    log(f"claimed run {run_id}: {plan['scenario']} engines {plan['engine_units']} (id offset {plan['id_offset']}, "
        f"interval {plan['interval_seconds']}s). SIMULATED replay of NASA C-MAPSS FD001, not real aircraft data.")
    if plan["status"] == "stopping":
        _finish(api, run_id, worker_id, "stopped", log)
        return None
    try:
        sources = build_plan(plan["scenario"], plan["engine_units"], plan["id_offset"],
                             **({"raw_dir": raw_dir} if raw_dir else {}))
    except ReplayError as e:
        _finish(api, run_id, worker_id, "failed", log, "plan_error", str(e))
        return None

    run_api = api.with_run(run_id)
    st = _RunState(plan["interval_seconds"])

    def totals(result):
        created = st.base["created"] + sum(e.created for e in result.engines.values())
        dups = st.base["duplicates"] + sum(e.duplicates for e in result.engines.values())
        errors = st.base_errors + run_api.retries + sum(e.rewinds for e in result.engines.values())
        return created, dups, errors

    def beat(result, force=False):
        st.result = result
        now = monotonic()
        if st.abandon or (not force and now - st.last_beat < heartbeat_interval):
            return
        st.last_beat = now
        created, dups, errors = totals(result)
        payload = {"worker_id": worker_id, "progress": _progress(result), "readings_created": created,
                   "readings_duplicate": dups, "error_count": errors, "last_error": run_api.last_transient}
        # A stop request is already known: do not delay shutting down with retry backoff.
        attempts = 1 if st.stop else HEARTBEAT_ATTEMPTS
        for attempt in range(attempts):
            try:
                r = api.heartbeat(run_id, payload)
                status = r.status_code
            except ServerUnavailable:
                r, status = None, None
            if status == 200:
                data = r.json()
                st.interval = data["interval_seconds"]
                st.stop = st.stop or data["status"] == "stopping"
                st.hb_fail_since = None
                return
            if status is not None and not is_transient_status(status):
                # definitive: 401 / 404 / 409 (lease_lost, run_not_active) / any other 4xx. Never retried.
                log(f"run {run_id}: heartbeat refused (HTTP {status}); stopping without reporting")
                st.abandon = True
                return
            # transient: HTTP 5xx / 408 / 429, or the backend is unreachable
            t = monotonic()
            if st.hb_fail_since is None:
                st.hb_fail_since = t
                what = "unreachable" if status is None else f"HTTP {status}"
                log(f"run {run_id}: heartbeat failing ({what}); retrying, ownership is re-verified if it lasts "
                    f"more than {heartbeat_grace:.0f}s")
            if t - st.hb_fail_since > heartbeat_grace:
                # We can no longer prove we hold the lease. Pause ingestion and let the reconnect path re-claim
                # (atomic): we resume only if the run is still ours.
                raise ServerUnavailable(f"heartbeats failing for more than {heartbeat_grace:.0f}s")
            if attempt < attempts - 1:
                sleep(HEARTBEAT_BACKOFF * 2 ** attempt)

    while True:
        try:
            result = run_replay(sources, run_api, interval=st.interval,
                                should_stop=lambda: st.stop or st.abandon, get_interval=lambda: st.interval,
                                on_tick=beat, on_event=None, sleep=sleep)
        except ServerUnavailable:
            if st.result is not None:  # fold the interrupted segment into the running totals
                c, d, e = totals(st.result)
                st.base, st.base_errors = {"created": c, "duplicates": d}, e
                run_api.retries = 0
            if not _reconnect(api, worker_id, run_id, st, max_outage, poll_interval, sleep, monotonic, log):
                return None
            st.hb_fail_since, st.last_beat = None, float("-inf")   # the claim just renewed our lease
            if st.stop:  # a stop was requested while we could not hear about it: honour it, do not resume
                _finish(api, run_id, worker_id, "stopped", log)
                return None
            continue
        except ReplayError as e:
            code = next((c for t, c in FAILURE_CODES.items() if isinstance(e, t)), "worker_error")
            _finish(api, run_id, worker_id, "failed", log, code, str(e))
            return None
        except Exception as e:  # noqa: BLE001 - never leave the run dangling on a worker bug
            log(f"run {run_id}: unexpected {type(e).__name__}")
            _finish(api, run_id, worker_id, "failed", log, "worker_error", "Unexpected worker error.")
            return None
        break

    if st.abandon:
        return None
    try:
        beat(result, force=True)  # final progress before the terminal report (best effort)
    except ServerUnavailable:
        pass
    if result.interrupted:
        _finish(api, run_id, worker_id, "failed", log, "worker_interrupted", "The worker was interrupted.")
        return 130
    _finish(api, run_id, worker_id, "stopped" if (st.stop or result.stopped) else "completed", log)
    return None
