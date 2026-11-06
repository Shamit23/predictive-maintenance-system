"""Simulation control-plane routes (mounted under /api/v1/simulation).

Auth: operator actions need X-Control-Key (CONTROL_API_KEY); worker protocol calls need X-API-Key (API_KEY);
reads are open like the other GET endpoints. See docs/api.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.security import require_api_key, require_control_key
from app.db.session import get_session
from app.schemas.api import (
    ErrorResponse, FinishIn, HeartbeatIn, HeartbeatOut, Page, RunCreate, RunOut, RunPatch, SimulationStatus,
    WorkerClaimIn, WorkerClaimOut,
)
from app.services import simulation as sim

router = APIRouter(prefix="/simulation", tags=["simulation"])
ERR = {401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
       422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def _settings(request: Request):
    return request.app.state.settings


# ---- reads ---------------------------------------------------------------------------------------
@router.get("", response_model=SimulationStatus)
def status(request: Request, session: Session = Depends(get_session)):
    """Worker presence, the active run (if any), the last finished run, and the server-enforced limits."""
    return sim.get_status(session, _settings(request))


@router.get("/runs", response_model=Page[RunOut])
def runs(request: Request, session: Session = Depends(get_session), limit: int = Query(20, ge=1, le=200),
         offset: int = Query(0, ge=0)):
    items, total = sim.list_runs(session, _settings(request), limit, offset)
    return Page[RunOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunOut, responses=ERR)
def run(run_id: int, request: Request, session: Session = Depends(get_session)):
    return sim.get_run(session, _settings(request), run_id)


# ---- operator actions (X-Control-Key) ---------------------------------------------------------------
@router.post("/runs", response_model=RunOut, status_code=201, dependencies=[Depends(require_control_key)],
             responses=ERR)
def start(body: RunCreate, request: Request, session: Session = Depends(get_session)):
    """Queue a run. It stays `queued` until a worker claims it; it is never reported `running` before that."""
    return sim.create_run(session, _settings(request), body)


@router.post("/runs/{run_id}/stop", response_model=RunOut, dependencies=[Depends(require_control_key)],
             responses=ERR)
def stop(run_id: int, request: Request, session: Session = Depends(get_session)):
    """Request a stop. `stopping` until the worker confirms; `stopped` immediately if no worker holds the run."""
    return sim.stop_run(session, _settings(request), run_id)


@router.patch("/runs/{run_id}", response_model=RunOut, dependencies=[Depends(require_control_key)], responses=ERR)
def change_speed(run_id: int, body: RunPatch, request: Request, session: Session = Depends(get_session)):
    """Change the replay interval; the worker picks it up on its next heartbeat."""
    return sim.patch_run(session, _settings(request), run_id, body.interval_seconds)


# ---- worker protocol (X-API-Key) ---------------------------------------------------------------------
@router.post("/worker/claim", response_model=WorkerClaimOut, dependencies=[Depends(require_api_key)], responses=ERR)
def worker_claim(body: WorkerClaimIn, request: Request, session: Session = Depends(get_session)):
    """Poll for work. Atomic: at most one worker holds a run; also records that this worker is online."""
    return WorkerClaimOut(run=sim.claim(session, _settings(request), body.worker_id))


@router.post("/runs/{run_id}/heartbeat", response_model=HeartbeatOut, dependencies=[Depends(require_api_key)],
             responses=ERR)
def worker_heartbeat(run_id: int, body: HeartbeatIn, request: Request, session: Session = Depends(get_session)):
    """Renew the lease, report progress; returns the desired status and interval. 409 lease_lost => stop working."""
    return sim.heartbeat(session, _settings(request), run_id, body)


@router.post("/runs/{run_id}/finish", response_model=RunOut, dependencies=[Depends(require_api_key)], responses=ERR)
def worker_finish(run_id: int, body: FinishIn, request: Request, session: Session = Depends(get_session)):
    return sim.finish(session, _settings(request), run_id, body)
