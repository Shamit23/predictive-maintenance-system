"""Simulation control plane: the API owns desired state in PostgreSQL; a separate worker process executes it.

State machine (one active run at a time, enforced by a partial unique index):

    queued --claim--> running --stop--> stopping --worker finish--> stopped
       |                 |  \\--worker finish--> completed | failed
       |--stop-->  stopped   \\--no heartbeat for SIM_STALE_TIMEOUT--> failed(worker_lost)
       \\--no claim for SIM_QUEUE_TIMEOUT--> failed(no_worker)

* A run is 'running' ONLY after a worker has claimed it (lease_owner set). 'queued' means no worker has it.
* Claiming is atomic (row lock, SKIP LOCKED) and takes a lease renewed by every heartbeat. A worker whose lease
  was taken over gets 409 lease_lost. A restarted worker can take over a run whose lease expired; the replay
  itself is idempotent and resumes from the data already stored.
* Expiry is applied lazily inside the transaction of any control/status call (no background threads in the API).
* All times come from the database clock (`now()`), never from clients.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Engine, SensorReading, SimulationRun, SimulationWorker
from app.schemas.api import (
    FinishIn, HeartbeatIn, HeartbeatOut, RunCreate, RunOut, SimulationLimits, SimulationStatus, WorkerPlan,
)
from app.services.errors import AppError, NotFound

ACTIVE = ("queued", "running", "stopping")
TERMINAL = ("stopped", "completed", "failed")
BASE_OFFSET = {"test": 0, "train": 1000}  # FD001 train and test both number engines 1..100
SLOT_STRIDE = 10_000
SLOTS = range(10)  # replay_slot 0..9 (schema le=9, DB check). Max engine id = 1000 + 100 + 9 * 10_000 = 91_100 < 1_000_000


def engine_ids_for(scenario: str, slot: int, units) -> list[int]:
    """Backend engine ids of a run: unit + scenario base + 10_000 * slot.

    Collision-free by construction: test ids occupy [1..100] and train ids [1001..1100] inside each slot's block of
    10_000, so no two (scenario, slot, unit) triples share an id (checked by a test).
    """
    offset = BASE_OFFSET[scenario] + SLOT_STRIDE * slot
    return [u + offset for u in units]
MAX_CYCLES = 100_000
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _now(session: Session) -> datetime:
    return session.scalar(select(func.now()))


def _clean(text: str | None, limit: int = 500) -> str | None:
    return None if text is None else _CONTROL_CHARS.sub(" ", text)[:limit]


def _lease_active(run: SimulationRun, now: datetime) -> bool:
    return (run.status in ("running", "stopping") and run.lease_owner is not None
            and run.lease_expires_at is not None and run.lease_expires_at > now)


def _end(run: SimulationRun, now: datetime, status: str, code: str | None = None, message: str | None = None) -> None:
    run.status, run.ended_at, run.updated_at = status, now, now
    run.lease_owner = run.lease_expires_at = None
    run.failure_code, run.failure_message = code, _clean(message)


def to_out(session: Session, run: SimulationRun, now: datetime) -> RunOut:
    stored = session.scalar(select(func.count()).select_from(SensorReading)
                            .where(SensorReading.simulation_run_id == run.id))
    return RunOut(
        id=run.id, status=run.status, scenario=run.scenario, engine_units=list(run.engine_units),
        engine_ids=[u + run.id_offset for u in run.engine_units], replay_slot=run.replay_slot,
        interval_seconds=run.interval_seconds, max_cycles=run.max_cycles, created_at=run.created_at,
        started_at=run.started_at, ended_at=run.ended_at, stop_requested_at=run.stop_requested_at,
        last_heartbeat_at=run.last_heartbeat_at, lease_active=_lease_active(run, now), progress=run.progress or {},
        readings_created=run.readings_created, readings_duplicate=run.readings_duplicate, readings_stored=stored,
        error_count=run.error_count, last_error=run.last_error, failure_code=run.failure_code,
        failure_message=run.failure_message,
    )


def _reap(session: Session, settings: Settings, now: datetime) -> None:
    """Apply timeouts to the active run (if any). Caller owns the transaction."""
    run = session.scalar(select(SimulationRun).where(SimulationRun.status.in_(ACTIVE)).with_for_update())
    if run is None:
        return
    if run.status == "queued":
        if now - run.created_at > timedelta(seconds=settings.sim_queue_timeout_seconds):
            _end(run, now, "failed", "no_worker", "No simulation worker claimed this run in time. "
                 "Start a worker with: python -m pm_simulator worker")
    elif run.status == "running":
        ref = run.last_heartbeat_at or run.started_at or run.created_at
        if now - ref > timedelta(seconds=settings.sim_stale_timeout_seconds):
            _end(run, now, "failed", "worker_lost", "The worker stopped sending heartbeats.")
    elif run.status == "stopping" and not _lease_active(run, now):
        _end(run, now, "stopped")  # stop was requested and the worker is gone: nothing left to wait for


def _touch_worker(session: Session, worker_id: str, now: datetime) -> None:
    session.execute(pg_insert(SimulationWorker).values(worker_id=worker_id, first_seen_at=now, last_seen_at=now)
                    .on_conflict_do_update(index_elements=["worker_id"], set_={"last_seen_at": now}))
    session.execute(delete(SimulationWorker).where(SimulationWorker.last_seen_at < now - timedelta(days=1)))


def _check_interval(settings: Settings, value: float) -> None:
    lo, hi = settings.sim_min_interval_seconds, settings.sim_max_interval_seconds
    if not lo <= value <= hi:
        raise AppError("invalid_interval", 422, f"interval_seconds must be between {lo} and {hi}.",
                       {"min": lo, "max": hi})


def limits(settings: Settings) -> SimulationLimits:
    return SimulationLimits(
        scenarios=sorted(BASE_OFFSET), units="1..100", max_engines=settings.sim_max_engines,
        min_interval_seconds=settings.sim_min_interval_seconds,
        max_interval_seconds=settings.sim_max_interval_seconds, max_cycles=MAX_CYCLES, replay_slots="0..9")


# ---- operator actions -----------------------------------------------------------------------------
def create_run(session: Session, settings: Settings, req: RunCreate) -> RunOut:
    if len(req.engines) > settings.sim_max_engines:
        raise AppError("too_many_engines", 422, f"At most {settings.sim_max_engines} engines per run.",
                       {"max": settings.sim_max_engines})
    _check_interval(settings, req.interval_seconds)
    try:
        with session.begin():
            now = _now(session)
            _reap(session, settings, now)
            active = session.scalar(select(SimulationRun.id).where(SimulationRun.status.in_(ACTIVE)))
            if active is not None:  # checked first: "stop the active run" is the more fundamental conflict
                raise _already_active(active)
            _reject_engine_reuse(session, req)
            run = SimulationRun(
                status="queued", scenario=req.scenario, engine_units=list(req.engines), replay_slot=req.replay_slot,
                id_offset=BASE_OFFSET[req.scenario] + SLOT_STRIDE * req.replay_slot,
                interval_seconds=req.interval_seconds, max_cycles=req.max_cycles, created_at=now, updated_at=now)
            session.add(run)
            session.flush()  # the unique index rejects a second active run here (concurrent creates)
            return to_out(session, run, now)
    except IntegrityError:
        with session.begin():
            active = session.scalar(select(SimulationRun.id).where(SimulationRun.status.in_(ACTIVE)))
        raise _already_active(active) from None


def _already_active(run_id: int | None) -> AppError:
    return AppError("simulation_already_active", 409, "A simulation run is already active; stop it first.",
                    {"active_run_id": run_id})


def _reject_engine_reuse(session: Session, req: RunCreate) -> None:
    """A new run must never reuse engine records that already exist.

    Engine ids are derived from (scenario, replay_slot, unit) and engine records are never deleted, so a later run with
    the same triple would find the earlier run's stored cycles: the worker would treat them as "already replayed" (the
    run would finish as `completed` with no fresh data) or resume in the middle of them (one engine's history split
    across two runs). Engine ids that were created by ordinary ingestion or an ad-hoc `pm_simulator replay` are
    refused for the same reason (they would only fail later with engine_origin_mismatch). Runs that never stored
    anything leave no engine record, so they do not use up their slot. Recovery of the SAME run is unaffected: this
    runs only at creation, and a restarted worker keeps working on the engines its own run already created.
    Runs inside the creating transaction, so it cannot interleave with another run being created (one active run only).
    """
    ids = engine_ids_for(req.scenario, req.replay_slot, req.engines)
    taken = {eid: origin for eid, origin in session.execute(select(Engine.id, Engine.origin).where(Engine.id.in_(ids)))}
    if not taken:
        return
    owners: dict[int, set[int]] = {}
    for eid, rid in session.execute(
            select(SensorReading.engine_id, SensorReading.simulation_run_id)
            .where(SensorReading.engine_id.in_(list(taken)), SensorReading.simulation_run_id.is_not(None)).distinct()):
        owners.setdefault(eid, set()).add(rid)
    # Which replay slots could serve this exact selection? (one query over at most 10 slots x 10 engines)
    candidates = {s: engine_ids_for(req.scenario, s, req.engines) for s in SLOTS}
    used = set(session.scalars(select(Engine.id).where(Engine.id.in_([i for c in candidates.values() for i in c]))))
    free = [s for s, c in candidates.items() if used.isdisjoint(c)]
    offset = ids[0] - req.engines[0]
    hint = (f"Replay slots that are free for this selection: {free}." if free
            else "No replay slot is free for this selection (each scenario/engine has 10 slots over the database's lifetime).")
    raise AppError(
        "engine_ids_in_use", 409,
        f"Engine ids {sorted(taken)} (replay_slot {req.replay_slot}) already hold stored readings from an earlier run, "
        f"an ad-hoc replay or ordinary ingestion. A new run never reuses existing engine records. {hint}",
        {"replay_slot": req.replay_slot, "free_slots": free,
         "engines": [{"engine_id": eid, "unit": eid - offset, "origin": taken[eid], "run_ids": sorted(owners.get(eid, ()))}
                     for eid in sorted(taken)]})


def stop_run(session: Session, settings: Settings, run_id: int) -> RunOut:
    with session.begin():
        now = _now(session)
        _reap(session, settings, now)
        run = session.scalar(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
        if run is None:
            raise NotFound(f"Simulation run {run_id}")
        if run.status in TERMINAL:
            raise AppError("run_not_active", 409, f"Run {run_id} is already {run.status}.", {"status": run.status})
        if run.status == "stopping":
            return to_out(session, run, now)  # idempotent
        run.stop_requested_at, run.updated_at = now, now
        if run.status == "queued" or not _lease_active(run, now):
            _end(run, now, "stopped")  # nobody is executing it: nothing to wait for
        else:
            run.status = "stopping"    # the worker confirms by finishing; until then it is NOT 'stopped'
        return to_out(session, run, now)


def patch_run(session: Session, settings: Settings, run_id: int, interval_seconds: float) -> RunOut:
    _check_interval(settings, interval_seconds)
    with session.begin():
        now = _now(session)
        _reap(session, settings, now)
        run = session.scalar(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
        if run is None:
            raise NotFound(f"Simulation run {run_id}")
        if run.status not in ACTIVE:
            raise AppError("run_not_active", 409, f"Run {run_id} is already {run.status}.", {"status": run.status})
        run.interval_seconds, run.updated_at = interval_seconds, now
        return to_out(session, run, now)


# ---- reads ---------------------------------------------------------------------------------------
def get_status(session: Session, settings: Settings) -> SimulationStatus:
    with session.begin():
        now = _now(session)
        _reap(session, settings, now)
        active = session.scalar(select(SimulationRun).where(SimulationRun.status.in_(ACTIVE)))
        last = session.scalar(select(SimulationRun).where(SimulationRun.status.in_(TERMINAL))
                              .order_by(SimulationRun.id.desc()).limit(1))
        online = session.scalar(select(func.count()).select_from(SimulationWorker).where(
            SimulationWorker.last_seen_at > now - timedelta(seconds=settings.sim_lease_seconds)))
        return SimulationStatus(
            worker_online=online > 0, workers_online=online,
            active_run=to_out(session, active, now) if active else None,
            last_run=to_out(session, last, now) if last else None, limits=limits(settings))


def list_runs(session: Session, settings: Settings, limit: int, offset: int) -> tuple[list[RunOut], int]:
    with session.begin():
        now = _now(session)
        _reap(session, settings, now)
        total = session.scalar(select(func.count()).select_from(SimulationRun))
        rows = session.scalars(select(SimulationRun).order_by(SimulationRun.id.desc()).limit(limit).offset(offset))
        return [to_out(session, r, now) for r in rows], total


def get_run(session: Session, settings: Settings, run_id: int) -> RunOut:
    with session.begin():
        now = _now(session)
        _reap(session, settings, now)
        run = session.get(SimulationRun, run_id)
        if run is None:
            raise NotFound(f"Simulation run {run_id}")
        return to_out(session, run, now)


# ---- worker protocol ------------------------------------------------------------------------------
def claim(session: Session, settings: Settings, worker_id: str) -> WorkerPlan | None:
    """Atomically take the active run if it is unclaimed, its lease expired, or this worker already holds it."""
    with session.begin():
        now = _now(session)
        _touch_worker(session, worker_id, now)
        _reap(session, settings, now)
        run = session.scalar(
            select(SimulationRun).where(or_(
                SimulationRun.status == "queued",
                and_(SimulationRun.status.in_(("running", "stopping")),
                     or_(SimulationRun.lease_owner == worker_id, SimulationRun.lease_owner.is_(None),
                         SimulationRun.lease_expires_at < now)),
            )).with_for_update(skip_locked=True))
        if run is None:
            return None
        if run.status == "queued":
            run.status = "running"
        run.started_at = run.started_at or now
        run.lease_owner, run.last_heartbeat_at, run.updated_at = worker_id, now, now
        run.lease_expires_at = now + timedelta(seconds=settings.sim_lease_seconds)
        return WorkerPlan(run_id=run.id, status=run.status, scenario=run.scenario,
                          engine_units=list(run.engine_units), id_offset=run.id_offset,
                          interval_seconds=run.interval_seconds, max_cycles=run.max_cycles)


def _held_run(session: Session, run_id: int, worker_id: str, *, allow_terminal: bool = False) -> SimulationRun:
    """Lock the run and check the caller may act on it.

    Only a running/stopping run can be acted on, and only by its lease holder. `allow_terminal` additionally lets
    already-finished runs through (for idempotent repeats); a `queued` run was never claimed, so nobody holds a
    lease on it and it is NEVER acceptable here.
    """
    run = session.scalar(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
    if run is None:
        raise NotFound(f"Simulation run {run_id}")
    if run.status not in ("running", "stopping") and not (allow_terminal and run.status in TERMINAL):
        raise AppError("run_not_active", 409, f"Run {run_id} is {run.status}.", {"status": run.status})
    if run.status in ("running", "stopping") and run.lease_owner != worker_id:
        raise AppError("lease_lost", 409, "This run is held by another worker (or the lease was reassigned).")
    return run


def heartbeat(session: Session, settings: Settings, run_id: int, body: HeartbeatIn) -> HeartbeatOut:
    with session.begin():
        now = _now(session)
        _touch_worker(session, body.worker_id, now)
        run = _held_run(session, run_id, body.worker_id)
        run.lease_expires_at = now + timedelta(seconds=settings.sim_lease_seconds)
        run.last_heartbeat_at, run.updated_at = now, now
        run.progress = body.progress
        run.readings_created, run.readings_duplicate, run.error_count = (
            body.readings_created, body.readings_duplicate, body.error_count)
        run.last_error = _clean(body.last_error)
        return HeartbeatOut(status=run.status, interval_seconds=run.interval_seconds)


def finish(session: Session, settings: Settings, run_id: int, body: FinishIn) -> RunOut:
    with session.begin():
        now = _now(session)
        run = _held_run(session, run_id, body.worker_id, allow_terminal=True)
        if run.status in TERMINAL:  # idempotent repeat of the same outcome only
            if run.status != body.outcome:
                raise AppError("run_not_active", 409, f"Run {run_id} already ended as {run.status}.",
                               {"status": run.status})
            return to_out(session, run, now)
        if body.outcome == "failed":
            _end(run, now, "failed", body.failure_code or "worker_error", body.failure_message or "Worker error.")
        else:
            _end(run, now, body.outcome)
        return to_out(session, run, now)
