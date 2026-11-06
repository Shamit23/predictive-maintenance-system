"""Simulation control plane: simulation_runs, simulation_workers, engine origin, reading->run link

Additive only (new tables; new columns are nullable or defaulted), so Phase 1/2/4a code keeps working against this
schema and application code can be rolled back without downgrading. Downgrade drops run history and origin labels.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("scenario", sa.String(10), nullable=False),
        sa.Column("engine_units", postgresql.JSONB, nullable=False),
        sa.Column("replay_slot", sa.Integer, nullable=False),
        sa.Column("id_offset", sa.Integer, nullable=False),
        sa.Column("interval_seconds", sa.Float, nullable=False),
        sa.Column("max_cycles", sa.Integer),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TS),
        sa.Column("ended_at", TS),
        sa.Column("stop_requested_at", TS),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_expires_at", TS),
        sa.Column("last_heartbeat_at", TS),
        sa.Column("progress", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("readings_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("readings_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500)),
        sa.Column("failure_code", sa.String(50)),
        sa.Column("failure_message", sa.String(500)),
        sa.CheckConstraint("status IN ('queued','running','stopping','stopped','completed','failed')",
                           name="ck_simulation_runs_status_valid"),
        sa.CheckConstraint("scenario IN ('test','train')", name="ck_simulation_runs_scenario_valid"),
        sa.CheckConstraint("replay_slot BETWEEN 0 AND 9", name="ck_simulation_runs_slot_valid"),
        sa.CheckConstraint("interval_seconds > 0 AND interval_seconds <= 3600",
                           name="ck_simulation_runs_interval_valid"),
    )
    # At most one active run: a unique index over a constant, restricted to active rows.
    op.create_index("uq_simulation_runs_one_active", "simulation_runs", [sa.text("(1)")], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running','stopping')"))
    op.create_index("ix_simulation_runs_created", "simulation_runs", ["created_at"])

    op.create_table(
        "simulation_workers",
        sa.Column("worker_id", sa.String(64), primary_key=True),
        sa.Column("first_seen_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, nullable=False, server_default=sa.func.now()),
    )

    # Existing engines keep origin 'api': their provenance is unknown, so they are not claimed to be simulated.
    op.add_column("engines", sa.Column("origin", sa.String(12), nullable=False, server_default="api"))
    op.create_check_constraint("ck_engines_origin_valid", "engines", "origin IN ('api','simulation')")

    op.add_column("sensor_readings", sa.Column("simulation_run_id", sa.BigInteger, nullable=True))
    op.create_foreign_key("fk_sensor_readings_simulation_run_id_simulation_runs", "sensor_readings",
                          "simulation_runs", ["simulation_run_id"], ["id"])
    op.create_index("ix_sensor_readings_simulation_run", "sensor_readings", ["simulation_run_id"])


def downgrade() -> None:
    op.drop_index("ix_sensor_readings_simulation_run", table_name="sensor_readings")
    op.drop_constraint("fk_sensor_readings_simulation_run_id_simulation_runs", "sensor_readings", type_="foreignkey")
    op.drop_column("sensor_readings", "simulation_run_id")
    op.drop_constraint("ck_engines_origin_valid", "engines", type_="check")
    op.drop_column("engines", "origin")
    op.drop_table("simulation_workers")
    op.drop_index("ix_simulation_runs_created", table_name="simulation_runs")
    op.drop_index("uq_simulation_runs_one_active", table_name="simulation_runs")
    op.drop_table("simulation_runs")
