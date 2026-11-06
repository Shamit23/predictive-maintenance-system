"""ORM tables. The Alembic migration (migrations/versions/0001_initial.py) is the schema source of truth;
tests check that migration and these models agree on table names.

Sensor/setting columns are generated from `pm_ml.data` so the DB schema and the ML code cannot drift apart.
"""
from __future__ import annotations

from datetime import datetime

from pm_ml.data import SENSOR_COLS, SETTING_COLS
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

VALUE_COLUMNS: list[str] = [*SETTING_COLS, *SENSOR_COLS]  # 3 settings + 21 sensors


class Engine(Base):
    """A monitored engine. `id` is the engine number from the data (no surrogate key)."""
    __tablename__ = "engines"
    __table_args__ = (CheckConstraint("origin IN ('api','simulation')", name="origin_valid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_cycle: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set once when the engine is first created and never changed: 'simulation' only if its first reading carried a
    # valid simulation-run marker. Ordinary ingestion and simulated ingestion can never be mixed on one engine.
    origin: Mapped[str] = mapped_column(String(12), nullable=False, server_default="api")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        UniqueConstraint("engine_id", "cycle"),  # idempotency + ordering guarantee
        CheckConstraint("cycle >= 1", name="cycle_positive"),
        Index("ix_sensor_readings_simulation_run", "simulation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    engine_id: Mapped[int] = mapped_column(ForeignKey("engines.id"), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL for ordinary ingestion; the run id when the reading was sent by a validated simulation run.
    simulation_run_id: Mapped[int | None] = mapped_column(ForeignKey("simulation_runs.id"))
    # setting_1..3, s_1..s_21
    for _c in VALUE_COLUMNS:
        vars()[_c] = Column(Float, nullable=False)
    del _c


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint("status IN ('active','candidate','archived')", name="status_valid"),
        # At most one active model at any time.
        Index("uq_model_versions_single_active", "status", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    directory_name: Mapped[str] = mapped_column(String(200), nullable=False)  # name only, never a path
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(100))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rul_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feature_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("reading_id", "model_version_id"),
        Index("ix_predictions_engine_cycle", "engine_id", "cycle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    engine_id: Mapped[int] = mapped_column(ForeignKey("engines.id"), nullable=False)
    reading_id: Mapped[int] = mapped_column(ForeignKey("sensor_readings.id"), nullable=False)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_rul: Mapped[float] = mapped_column(Float, nullable=False)
    failure_score: Mapped[float] = mapped_column(Float, nullable=False)  # uncalibrated, not a probability
    risk_category: Mapped[str] = mapped_column(String(10), nullable=False)
    history_len: Mapped[int] = mapped_column(Integer, nullable=False)  # readings that backed the features
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("severity IN ('warning','critical')", name="severity_valid"),
        CheckConstraint("status IN ('open','acknowledged','resolved')", name="status_valid"),
        # At most one unresolved alert per engine -> repeat alerts are suppressed by construction.
        Index("uq_alerts_one_unresolved_per_engine", "engine_id", unique=True,
              postgresql_where=text("status IN ('open','acknowledged')")),
        Index("ix_alerts_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    engine_id: Mapped[int] = mapped_column(ForeignKey("engines.id"), nullable=False)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), nullable=False)  # triggering prediction
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False)
    predicted_rul: Mapped[float] = mapped_column(Float, nullable=False)  # at trigger time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reason: Mapped[str | None] = mapped_column(String(20))  # recovered | escalated


class IngestionError(Base):
    """Rejected ingestion attempts (validation failures, conflicts, gaps, internal failures)."""
    __tablename__ = "ingestion_errors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    engine_id: Mapped[int | None] = mapped_column(Integer)  # no FK: the engine may not exist
    cycle: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload_excerpt: Mapped[str | None] = mapped_column(Text)  # truncated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimulationRun(Base):
    """Desired + observed state of a replay run. The API owns it; a separate worker process executes it.

    At most one run may be active (queued/running/stopping) at any time (partial unique index on a constant).
    A run is only 'running' after a worker has claimed it (lease_owner set); 'queued' means nobody has.
    """
    __tablename__ = "simulation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','stopping','stopped','completed','failed')",
                        name="status_valid"),
        CheckConstraint("scenario IN ('test','train')", name="scenario_valid"),
        CheckConstraint("replay_slot BETWEEN 0 AND 9", name="slot_valid"),
        CheckConstraint("interval_seconds > 0 AND interval_seconds <= 3600", name="interval_valid"),
        Index("uq_simulation_runs_one_active", text("(1)"), unique=True,
              postgresql_where=text("status IN ('queued','running','stopping')")),
        Index("ix_simulation_runs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    scenario: Mapped[str] = mapped_column(String(10), nullable=False)
    engine_units: Mapped[list] = mapped_column(JSONB, nullable=False)      # source engine numbers, 1..100
    replay_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    id_offset: Mapped[int] = mapped_column(Integer, nullable=False)        # backend id = unit + id_offset (server-derived)
    interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)  # current desired pacing
    max_cycles: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))  # worker-reported
    readings_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    readings_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    failure_code: Mapped[str | None] = mapped_column(String(50))
    failure_message: Mapped[str | None] = mapped_column(String(500))


class SimulationWorker(Base):
    """Presence of worker processes (upserted by each claim poll); lets the API say whether any worker is online."""
    __tablename__ = "simulation_workers"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
