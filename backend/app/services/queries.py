"""Read-side queries and ORM -> schema conversion."""
from __future__ import annotations

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.orm import Session

from app.models import Alert, Engine, ModelVersion, Prediction, SensorReading
from app.schemas.api import AlertOut, EngineDetail, EngineSummary, ModelOut, PredictionOut
from app.services.errors import NotFound

UNRESOLVED = ("open", "acknowledged")


def prediction_out(p: Prediction, version: str) -> PredictionOut:
    return PredictionOut(
        id=p.id, engine_id=p.engine_id, cycle=p.cycle, predicted_rul=p.predicted_rul,
        failure_score=p.failure_score, risk_category=p.risk_category, model_version=version,
        history_len=p.history_len, created_at=p.created_at,
    )


def alert_out(a: Alert, version: str) -> AlertOut:
    return AlertOut(
        id=a.id, engine_id=a.engine_id, severity=a.severity, status=a.status, predicted_rul=a.predicted_rul,
        prediction_id=a.prediction_id, model_version=version, created_at=a.created_at, updated_at=a.updated_at,
        acknowledged_at=a.acknowledged_at, resolved_at=a.resolved_at, resolution_reason=a.resolution_reason,
    )


def model_out(m: ModelVersion) -> ModelOut:
    return ModelOut(
        version=m.version, model_type=m.model_type, status=m.status, dataset=m.dataset, trained_at=m.trained_at,
        activated_at=m.activated_at, rul_cap=m.rul_cap, failure_horizon=m.failure_horizon,
        metrics=m.metrics, feature_config=m.feature_config,
    )


def version_of(session: Session, model_version_id: int) -> str:
    return session.scalar(select(ModelVersion.version).where(ModelVersion.id == model_version_id)) or "unknown"


def _engine_rows(search: str | None, risk: str | None, alert_status: str | None):
    latest = (
        select(Prediction.id).where(Prediction.engine_id == Engine.id)
        .order_by(Prediction.cycle.desc(), Prediction.id.desc()).limit(1).correlate(Engine).scalar_subquery()
    )
    q = (
        select(Engine, Prediction, ModelVersion.version, Alert)
        .outerjoin(Prediction, Prediction.id == latest)
        .outerjoin(ModelVersion, ModelVersion.id == Prediction.model_version_id)
        .outerjoin(Alert, and_(Alert.engine_id == Engine.id, Alert.status.in_(UNRESOLVED)))
    )
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(cast(Engine.id, String).like(f"%{escaped}%", escape="\\"))
    if risk:
        q = q.where(Prediction.risk_category == risk)
    if alert_status == "none":
        q = q.where(Alert.id.is_(None))
    elif alert_status:
        q = q.where(Alert.status == alert_status)
    return q


def _summary(e: Engine, p: Prediction | None, version: str | None, a: Alert | None) -> dict:
    return dict(
        id=e.id, last_cycle=e.last_cycle, last_reading_at=e.last_reading_at,
        latest_prediction=prediction_out(p, version) if p else None,
        risk_category=p.risk_category if p else None,
        alert_status=a.status if a else None, alert_severity=a.severity if a else None,
    )


def list_engines(session: Session, *, search, risk, alert_status, limit, offset):
    q = _engine_rows(search, risk, alert_status)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.execute(q.order_by(Engine.id).limit(limit).offset(offset)).all()
    return [EngineSummary(**_summary(*r)) for r in rows], total


def get_engine(session: Session, engine_id: int) -> EngineDetail:
    row = session.execute(_engine_rows(None, None, None).where(Engine.id == engine_id)).first()
    if row is None:
        raise NotFound(f"Engine {engine_id}")
    e = row[0]
    return EngineDetail(
        **_summary(*row), created_at=e.created_at,
        reading_count=session.scalar(select(func.count()).where(SensorReading.engine_id == engine_id)),
        prediction_count=session.scalar(select(func.count()).where(Prediction.engine_id == engine_id)),
    )


def require_engine(session: Session, engine_id: int) -> None:
    if session.get(Engine, engine_id) is None:
        raise NotFound(f"Engine {engine_id}")


def list_readings(session: Session, engine_id, *, since_cycle, order, limit, offset):
    require_engine(session, engine_id)
    q = select(SensorReading).where(SensorReading.engine_id == engine_id)
    if since_cycle is not None:
        q = q.where(SensorReading.cycle >= since_cycle)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    col = SensorReading.cycle.asc() if order == "asc" else SensorReading.cycle.desc()
    return list(session.scalars(q.order_by(col).limit(limit).offset(offset))), total


def list_predictions(session: Session, engine_id, *, order, limit, offset):
    require_engine(session, engine_id)
    q = (select(Prediction, ModelVersion.version)
         .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
         .where(Prediction.engine_id == engine_id))
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    col = Prediction.cycle.asc() if order == "asc" else Prediction.cycle.desc()
    rows = session.execute(q.order_by(col, Prediction.id).limit(limit).offset(offset)).all()
    return [prediction_out(p, v) for p, v in rows], total


def list_alerts(session: Session, *, status, severity, engine_id, limit, offset):
    q = select(Alert, ModelVersion.version).join(ModelVersion, ModelVersion.id == Alert.model_version_id)
    if status == "unresolved":
        q = q.where(Alert.status.in_(UNRESOLVED))
    elif status:
        q = q.where(Alert.status == status)
    if severity:
        q = q.where(Alert.severity == severity)
    if engine_id is not None:
        q = q.where(Alert.engine_id == engine_id)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.execute(q.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).offset(offset)).all()
    return [alert_out(a, v) for a, v in rows], total


def list_models(session: Session, limit, offset):
    total = session.scalar(select(func.count()).select_from(ModelVersion))
    rows = session.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
                           .limit(limit).offset(offset))
    return [model_out(m) for m in rows], total


def active_model(session: Session) -> ModelOut:
    m = session.scalar(select(ModelVersion).where(ModelVersion.status == "active"))
    if m is None:
        raise NotFound("Active model")
    return model_out(m)
