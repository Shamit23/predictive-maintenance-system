"""Sensor-reading ingestion: store reading -> predict -> persist prediction -> evaluate alert.

All of it happens in ONE database transaction, so a reading never exists without its prediction/alert
outcome (or an explicit 'insufficient_history' status), and a failure anywhere rolls everything back.
A row lock on the engine serialises concurrent ingestion for the same engine, which makes the gap check,
the duplicate check and the alert lifecycle race-free.

Duplicate / ordering policy:
  * same (engine, cycle) with identical sensor/setting values -> idempotent no-op, reported as 'duplicate'
    (event_time is ignored: a retry may carry a new timestamp)
  * same (engine, cycle) with different values               -> 409 conflicting_duplicate
  * cycle != last stored cycle + 1                           -> 409 cycle_gap (window features need no holes)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models import Alert, Engine, IngestionError, Prediction, SensorReading
from app.models.tables import VALUE_COLUMNS
from app.services import alerts as alert_service
from app.services.errors import ConflictingDuplicate, CycleGap
from app.services.model_registry import LoadedModel
from app.services.prediction import load_history, predict_rows

log = logging.getLogger("app.ingestion")


@dataclass
class IngestResult:
    status: str  # created | duplicate
    engine_id: int
    cycle: int
    prediction: Prediction | None
    prediction_status: str  # ok | insufficient_history
    alert_action: str
    active_alert: Alert | None
    model_version: str


def ingest_reading(session: Session, model: LoadedModel, settings: Settings, data) -> IngestResult:
    engine_id, cycle = data.engine_id, data.cycle
    values = {c: float(getattr(data, c)) for c in VALUE_COLUMNS}
    now = datetime.now(timezone.utc)

    with session.begin():
        session.execute(pg_insert(Engine).values(id=engine_id).on_conflict_do_nothing())
        engine = session.execute(select(Engine).where(Engine.id == engine_id).with_for_update()).scalar_one()

        existing = session.scalar(
            select(SensorReading).where(SensorReading.engine_id == engine_id, SensorReading.cycle == cycle))
        if existing is not None:
            if any(getattr(existing, c) != values[c] for c in VALUE_COLUMNS):
                raise ConflictingDuplicate(engine_id, cycle)
            pred = session.scalar(select(Prediction).where(
                Prediction.reading_id == existing.id, Prediction.model_version_id == model.model_version_id))
            alert = session.scalar(select(Alert).where(
                Alert.engine_id == engine_id, Alert.status.in_(("open", "acknowledged"))))
            return IngestResult("duplicate", engine_id, cycle, pred,
                                "ok" if pred else "insufficient_history", "none", alert, model.version)

        expected = engine.last_cycle + 1
        if cycle != expected:
            raise CycleGap(engine_id, cycle, expected)

        reading = SensorReading(engine_id=engine_id, cycle=cycle, event_time=data.event_time or now, **values)
        session.add(reading)
        session.flush()
        engine.last_cycle, engine.last_reading_at = cycle, reading.event_time

        rows = load_history(session, model, engine_id, cycle)
        if len(rows) < settings.min_history_readings:
            log.info("reading stored, prediction deferred", extra={"ctx": {
                "engine_id": engine_id, "cycle": cycle, "history": len(rows)}})
            return IngestResult("created", engine_id, cycle, None, "insufficient_history", "none", None,
                                model.version)

        out = predict_rows(model, settings, rows)
        prediction = Prediction(
            engine_id=engine_id, reading_id=reading.id, model_version_id=model.model_version_id, cycle=cycle,
            predicted_rul=out["predicted_rul"], failure_score=out["failure_score"],
            risk_category=out["risk_category"], history_len=out["history_len"],
        )
        session.add(prediction)
        session.flush()
        action, alert = alert_service.evaluate_alert(session, prediction)

    log.info("reading ingested", extra={"ctx": {
        "engine_id": engine_id, "cycle": cycle, "risk": prediction.risk_category,
        "alert_action": action, "model_version": model.version}})
    return IngestResult("created", engine_id, cycle, prediction, "ok", action, alert, model.version)


def record_ingestion_error(factory: sessionmaker[Session], *, code: str, message: str,
                           engine_id: int | None = None, cycle: int | None = None, payload=None) -> None:
    """Persist a rejected attempt in its own transaction. Never raises (best effort, logged on failure)."""
    try:
        excerpt = None if payload is None else json.dumps(payload, default=str)[:2000]
        with factory() as s, s.begin():
            s.add(IngestionError(engine_id=engine_id, cycle=cycle, error_code=code,
                                 message=message[:500], payload_excerpt=excerpt))
    except Exception:  # noqa: BLE001 - recording must not mask the original error
        log.exception("could not record ingestion error")
