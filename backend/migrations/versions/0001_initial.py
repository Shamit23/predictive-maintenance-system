"""Initial schema: engines, readings, model versions, predictions, alerts, ingestion errors

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen copy of the C-MAPSS value columns (3 settings + 21 sensors). Migrations must not import app code.
VALUE_COLUMNS = [f"setting_{i}" for i in range(1, 4)] + [f"s_{i}" for i in range(1, 22)]
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "engines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("last_cycle", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_reading_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("engine_id", sa.Integer,
                  sa.ForeignKey("engines.id", name="fk_sensor_readings_engine_id_engines"), nullable=False),
        sa.Column("cycle", sa.Integer, nullable=False),
        sa.Column("event_time", TS, nullable=False),
        sa.Column("ingested_at", TS, nullable=False, server_default=sa.func.now()),
        *[sa.Column(c, sa.Float, nullable=False) for c in VALUE_COLUMNS],
        sa.UniqueConstraint("engine_id", "cycle", name="uq_sensor_readings_engine_id"),
        sa.CheckConstraint("cycle >= 1", name="ck_sensor_readings_cycle_positive"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("directory_name", sa.String(200), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("dataset", sa.String(100)),
        sa.Column("trained_at", TS, nullable=False),
        sa.Column("rul_cap", sa.Integer, nullable=False),
        sa.Column("failure_horizon", sa.Integer, nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=False),
        sa.Column("feature_config", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("activated_at", TS),
        sa.UniqueConstraint("version", name="uq_model_versions_version"),
        sa.CheckConstraint("status IN ('active','candidate','archived')", name="ck_model_versions_status_valid"),
    )
    op.create_index("uq_model_versions_single_active", "model_versions", ["status"], unique=True,
                    postgresql_where=sa.text("status = 'active'"))

    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("engine_id", sa.Integer,
                  sa.ForeignKey("engines.id", name="fk_predictions_engine_id_engines"), nullable=False),
        sa.Column("reading_id", sa.BigInteger,
                  sa.ForeignKey("sensor_readings.id", name="fk_predictions_reading_id_sensor_readings"),
                  nullable=False),
        sa.Column("model_version_id", sa.Integer,
                  sa.ForeignKey("model_versions.id", name="fk_predictions_model_version_id_model_versions"),
                  nullable=False),
        sa.Column("cycle", sa.Integer, nullable=False),
        sa.Column("predicted_rul", sa.Float, nullable=False),
        sa.Column("failure_score", sa.Float, nullable=False),
        sa.Column("risk_category", sa.String(10), nullable=False),
        sa.Column("history_len", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reading_id", "model_version_id", name="uq_predictions_reading_id"),
    )
    op.create_index("ix_predictions_engine_cycle", "predictions", ["engine_id", "cycle"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("engine_id", sa.Integer,
                  sa.ForeignKey("engines.id", name="fk_alerts_engine_id_engines"), nullable=False),
        sa.Column("prediction_id", sa.BigInteger,
                  sa.ForeignKey("predictions.id", name="fk_alerts_prediction_id_predictions"), nullable=False),
        sa.Column("model_version_id", sa.Integer,
                  sa.ForeignKey("model_versions.id", name="fk_alerts_model_version_id_model_versions"),
                  nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("predicted_rul", sa.Float, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_at", TS),
        sa.Column("resolved_at", TS),
        sa.Column("resolution_reason", sa.String(20)),
        sa.CheckConstraint("severity IN ('warning','critical')", name="ck_alerts_severity_valid"),
        sa.CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_alerts_status_valid"),
    )
    op.create_index("uq_alerts_one_unresolved_per_engine", "alerts", ["engine_id"], unique=True,
                    postgresql_where=sa.text("status IN ('open','acknowledged')"))
    op.create_index("ix_alerts_status_created", "alerts", ["status", "created_at"])

    op.create_table(
        "ingestion_errors",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("engine_id", sa.Integer),
        sa.Column("cycle", sa.Integer),
        sa.Column("error_code", sa.String(50), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("payload_excerpt", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for t in ("ingestion_errors", "alerts", "predictions", "model_versions", "sensor_readings", "engines"):
        op.drop_table(t)
