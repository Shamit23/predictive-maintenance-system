"""HTTP routes. Thin: validate -> call a service -> shape the response. See docs/api.md."""
from __future__ import annotations

import logging
from typing import Literal

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import BACKEND_ROOT
from app.api.simulation import router as simulation_router
from app.core.security import require_api_key
from app.db.session import get_session
from app.schemas.api import (
    AlertOut, DryRunPrediction, EngineDetail, EngineSummary, ErrorResponse, HealthOut, IngestResponse,
    ModelOut, Page, PredictionOut, PredictRequest, ReadingIn, ReadingOut, ReadyOut, Risk,
)
from app.services import alerts as alert_service
from app.services import ingestion, prediction, queries
from app.services.errors import AppError, ModelUnavailable, NotFound
from app.services.model_registry import LoadedModel

log = logging.getLogger("app.api")
router = APIRouter()
v1 = APIRouter(prefix="/api/v1")

Limit = Query(50, ge=1, le=200)
Offset = Query(0, ge=0)
ERR = {401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
       422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def get_model(request: Request) -> LoadedModel:
    model = getattr(request.app.state, "model", None)
    if model is None:
        raise ModelUnavailable()
    return model


# ---- health ---------------------------------------------------------------------------------
@router.get("/health", response_model=HealthOut, tags=["health"])
def health():
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


def _alembic_head() -> str:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


@router.get("/ready", response_model=ReadyOut, tags=["health"], responses={503: {"model": ReadyOut}})
def ready(request: Request):
    """Readiness: database reachable, schema at the latest migration, model loaded."""
    checks: dict[str, str] = {}
    try:
        with request.app.state.session_factory() as s:
            s.execute(text("SELECT 1"))
            checks["database"] = "ok"
            current = MigrationContext.configure(s.connection()).get_current_revision()
            checks["migrations"] = "ok" if current == _alembic_head() else "outdated"
    except Exception:  # noqa: BLE001
        log.error("readiness: database check failed")
        checks.setdefault("database", "unavailable")
        checks.setdefault("migrations", "unknown")
    checks["model"] = "ok" if getattr(request.app.state, "model", None) else "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


# ---- engines --------------------------------------------------------------------------------
@v1.get("/engines", response_model=Page[EngineSummary], tags=["engines"])
def list_engines(session: Session = Depends(get_session), search: str | None = Query(None, max_length=20),
                 risk: Risk | None = None, alert_status: Literal["open", "acknowledged", "none"] | None = None,
                 limit: int = Limit, offset: int = Offset):
    with session.begin():
        items, total = queries.list_engines(session, search=search, risk=risk, alert_status=alert_status,
                                            limit=limit, offset=offset)
    return Page[EngineSummary](items=items, total=total, limit=limit, offset=offset)


@v1.get("/engines/{engine_id}", response_model=EngineDetail, tags=["engines"], responses=ERR)
def get_engine(engine_id: int, session: Session = Depends(get_session)):
    with session.begin():
        return queries.get_engine(session, engine_id)


@v1.get("/engines/{engine_id}/readings", response_model=Page[ReadingOut], tags=["engines"], responses=ERR)
def engine_readings(engine_id: int, session: Session = Depends(get_session), since_cycle: int | None = Query(None, ge=1),
                    order: Literal["asc", "desc"] = "desc", limit: int = Limit, offset: int = Offset):
    with session.begin():
        rows, total = queries.list_readings(session, engine_id, since_cycle=since_cycle, order=order,
                                            limit=limit, offset=offset)
        items = [ReadingOut.model_validate(r) for r in rows]
    return Page[ReadingOut](items=items, total=total, limit=limit, offset=offset)


@v1.get("/engines/{engine_id}/predictions", response_model=Page[PredictionOut], tags=["engines"], responses=ERR)
def engine_predictions(engine_id: int, session: Session = Depends(get_session),
                       order: Literal["asc", "desc"] = "desc", limit: int = Limit, offset: int = Offset):
    with session.begin():
        items, total = queries.list_predictions(session, engine_id, order=order, limit=limit, offset=offset)
    return Page[PredictionOut](items=items, total=total, limit=limit, offset=offset)


# ---- ingestion & prediction -----------------------------------------------------------------
@v1.post("/readings", response_model=IngestResponse, status_code=201, tags=["ingestion"],
         dependencies=[Depends(require_api_key)], responses=ERR)
def ingest(body: ReadingIn, request: Request, session: Session = Depends(get_session),
           model: LoadedModel = Depends(get_model),
           x_simulation_run: int | None = Header(None, alias="X-Simulation-Run", ge=1)):
    """Store one reading, predict RUL, persist the prediction and update alerts (one transaction).

    201 = new reading stored. 200 = identical duplicate (idempotent no-op). 409 = conflicting duplicate or cycle gap.

    Optional header `X-Simulation-Run: <run id>` marks the reading as simulated. The server validates it (run is
    running/stopping and owns this engine) and locks the engine's origin at creation, so simulated and ordinary
    data are never mixed (409 engine_origin_mismatch). Ordinary callers simply omit the header.
    """
    settings = request.app.state.settings
    try:
        result = ingestion.ingest_reading(session, model, settings, body, simulation_run_id=x_simulation_run)
    except AppError as e:
        ingestion.record_ingestion_error(
            request.app.state.session_factory, code=e.code, message=e.message,
            engine_id=body.engine_id, cycle=body.cycle, payload=body.model_dump(mode="json"))
        raise
    except Exception:
        ingestion.record_ingestion_error(
            request.app.state.session_factory, code="internal_error",
            message="Unexpected failure while ingesting; the reading was not stored.",
            engine_id=body.engine_id, cycle=body.cycle, payload=body.model_dump(mode="json"))
        raise
    out = IngestResponse(
        status=result.status, engine_id=result.engine_id, cycle=result.cycle,
        prediction_status=result.prediction_status,
        prediction=queries.prediction_out(result.prediction, result.model_version) if result.prediction else None,
        alert_action=result.alert_action,
        active_alert=queries.alert_out(result.active_alert, result.model_version) if result.active_alert else None,
    )
    return JSONResponse(out.model_dump(mode="json"), status_code=201 if result.status == "created" else 200)


@v1.post("/predict", response_model=DryRunPrediction, tags=["prediction"], responses=ERR)
def predict(body: PredictRequest, request: Request, session: Session = Depends(get_session),
            model: LoadedModel = Depends(get_model)):
    """Dry-run prediction from the engine's STORED readings. Nothing is persisted; no readings are invented."""
    return prediction.dry_run(session, model, request.app.state.settings, body.engine_id, body.cycle)


# ---- models ---------------------------------------------------------------------------------
@v1.get("/models", response_model=Page[ModelOut], tags=["models"])
def models(session: Session = Depends(get_session), limit: int = Limit, offset: int = Offset):
    with session.begin():
        items, total = queries.list_models(session, limit, offset)
    return Page[ModelOut](items=items, total=total, limit=limit, offset=offset)


@v1.get("/models/active", response_model=ModelOut, tags=["models"], responses=ERR)
def active_model(session: Session = Depends(get_session)):
    with session.begin():
        return queries.active_model(session)


# ---- alerts ---------------------------------------------------------------------------------
@v1.get("/alerts", response_model=Page[AlertOut], tags=["alerts"])
def alerts(session: Session = Depends(get_session),
           status: Literal["open", "acknowledged", "resolved", "unresolved"] | None = None,
           severity: Literal["warning", "critical"] | None = None,
           engine_id: int | None = Query(None, ge=1), limit: int = Limit, offset: int = Offset):
    with session.begin():
        items, total = queries.list_alerts(session, status=status, severity=severity, engine_id=engine_id,
                                           limit=limit, offset=offset)
    return Page[AlertOut](items=items, total=total, limit=limit, offset=offset)


@v1.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut, tags=["alerts"],
         dependencies=[Depends(require_api_key)], responses=ERR)
def acknowledge_alert(alert_id: int, session: Session = Depends(get_session)):
    """Idempotent. 404 if unknown; 409 if the alert is already resolved."""
    try:
        alert = alert_service.acknowledge(session, alert_id)
    except ValueError:
        raise AppError("alert_resolved", 409, "This alert is already resolved and cannot be acknowledged.")
    if alert is None:
        raise NotFound(f"Alert {alert_id}")
    with session.begin():
        return queries.alert_out(alert, queries.version_of(session, alert.model_version_id))


v1.include_router(simulation_router)
router.include_router(v1)
