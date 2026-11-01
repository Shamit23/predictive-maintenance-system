"""Alert lifecycle, evaluated inside the same transaction as the prediction that triggers it.

Per engine there is at most one unresolved alert (enforced by a partial unique index):
  * risk normal                          -> resolve the unresolved alert (reason 'recovered')
  * risk >= warning, no unresolved alert -> open one                      (action 'opened')
  * higher severity than current alert   -> open alert: raise it in place (action 'escalated')
                                            acknowledged alert: resolve it ('escalated') and open a new
                                            alert, so the operator must acknowledge the worse state
  * same or lower severity               -> nothing (repeat suppression)
Known limits: no hysteresis (RUL hovering at a threshold can resolve and reopen alerts); severity is never
lowered while an alert stays open. Thresholds are demonstration values.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.risk import RISK_ORDER
from app.models import Alert, Prediction


def _active_alert(session: Session, engine_id: int) -> Alert | None:
    # FOR UPDATE: serialises with a concurrent acknowledge so we never act on a stale status.
    return session.scalar(
        select(Alert).where(Alert.engine_id == engine_id, Alert.status.in_(("open", "acknowledged")))
        .with_for_update()
    )


def evaluate_alert(session: Session, prediction: Prediction) -> tuple[str, Alert | None]:
    """Returns (action, unresolved_alert_after). Caller owns the transaction."""
    now = datetime.now(timezone.utc)
    active = _active_alert(session, prediction.engine_id)
    risk = prediction.risk_category

    if risk == "normal":
        if active is None:
            return "none", None
        active.status, active.resolved_at, active.resolution_reason, active.updated_at = (
            "resolved", now, "recovered", now)
        return "resolved", None

    def open_new() -> Alert:
        alert = Alert(
            engine_id=prediction.engine_id, prediction_id=prediction.id,
            model_version_id=prediction.model_version_id, severity=risk, status="open",
            predicted_rul=prediction.predicted_rul, created_at=now, updated_at=now,
        )
        session.add(alert)
        session.flush()
        return alert

    if active is None:
        return "opened", open_new()

    if RISK_ORDER[risk] > RISK_ORDER[active.severity]:
        if active.status == "open":
            active.severity, active.prediction_id = risk, prediction.id
            active.model_version_id, active.predicted_rul, active.updated_at = (
                prediction.model_version_id, prediction.predicted_rul, now)
            return "escalated", active
        active.status, active.resolved_at, active.resolution_reason, active.updated_at = (
            "resolved", now, "escalated", now)
        session.flush()  # free the one-unresolved-per-engine slot before inserting the new alert
        return "escalated", open_new()

    return "none", active


def acknowledge(session: Session, alert_id: int) -> Alert | None:
    """Idempotent. Returns None if absent. Raises ValueError('resolved') if already resolved."""
    with session.begin():
        alert = session.scalar(select(Alert).where(Alert.id == alert_id).with_for_update())
        if alert is None:
            return None
        if alert.status == "resolved":
            raise ValueError("resolved")
        if alert.status == "open":
            now = datetime.now(timezone.utc)
            alert.status, alert.acknowledged_at, alert.updated_at = "acknowledged", now, now
        return alert
