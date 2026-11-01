"""History retrieval and prediction. All feature math lives in `pm_ml` (shared with training)."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.risk import risk_category
from app.models import Engine, SensorReading
from app.models.tables import VALUE_COLUMNS
from app.schemas.api import DryRunPrediction
from app.services.errors import InsufficientHistory, NotFound
from app.services.model_registry import LoadedModel


def load_history(session: Session, model: LoadedModel, engine_id: int, upto_cycle: int) -> list[SensorReading]:
    """The most recent readings (cycle <= upto_cycle) in ascending cycle order.

    Only the last `max_window` readings can influence a causal feature, so no more are fetched.
    Only stored readings are used; nothing is ever synthesised.
    """
    n = model.predictor.cfg.max_window
    rows = session.scalars(
        select(SensorReading)
        .where(SensorReading.engine_id == engine_id, SensorReading.cycle <= upto_cycle)
        .order_by(SensorReading.cycle.desc())
        .limit(n)
    ).all()
    return list(reversed(rows))


def to_frame(rows: list[SensorReading]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"unit": r.engine_id, "cycle": r.cycle, **{c: getattr(r, c) for c in VALUE_COLUMNS}} for r in rows]
    )


def predict_rows(model: LoadedModel, settings: Settings, rows: list[SensorReading]) -> dict:
    out = model.predictor.predict(to_frame(rows))
    out["risk_category"] = risk_category(
        out["predicted_rul"], settings.risk_warning_rul, settings.risk_critical_rul)
    out["history_len"] = len(rows)
    return out


def dry_run(session: Session, model: LoadedModel, settings: Settings,
            engine_id: int, cycle: int | None) -> DryRunPrediction:
    """Predict from stored history without persisting anything."""
    with session.begin():
        engine = session.get(Engine, engine_id)
        if engine is None:
            raise NotFound(f"Engine {engine_id}")
        target = cycle if cycle is not None else engine.last_cycle
        if target > engine.last_cycle:
            raise NotFound(f"Reading for engine {engine_id} at cycle {target}")
        rows = load_history(session, model, engine_id, target)
    if len(rows) < settings.min_history_readings:
        raise InsufficientHistory(engine_id, len(rows), settings.min_history_readings)
    out = predict_rows(model, settings, rows)
    return DryRunPrediction(
        engine_id=engine_id, cycle=out["cycle"], predicted_rul=out["predicted_rul"],
        failure_score=out["failure_score"], risk_category=out["risk_category"],
        model_version=out["model_version"], history_len=out["history_len"],
        predicted_at=datetime.now(timezone.utc),
    )
