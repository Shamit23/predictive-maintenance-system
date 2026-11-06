"""Schema produced by the Alembic migration (the suite upgrades from base to head before running)."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
import app.models  # noqa: F401
from conftest import VALUE_COLS


def test_migration_creates_expected_tables_and_matches_models(db):
    tables = set(inspect(db).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    cols = {c["name"] for c in inspect(db).get_columns("sensor_readings")}
    assert set(VALUE_COLS) <= cols and len(VALUE_COLS) == 24


def _engine(c, i=1):
    c.execute(text("INSERT INTO engines (id) VALUES (:i)"), {"i": i})


def _reading(c, engine=1, cycle=1):
    vals = ", ".join(f"{k}" for k in VALUE_COLS)
    ph = ", ".join("0" for _ in VALUE_COLS)
    c.execute(text(f"INSERT INTO sensor_readings (engine_id, cycle, event_time, {vals}) "
                   f"VALUES (:e, :c, now(), {ph})"), {"e": engine, "c": cycle})


def test_reading_unique_per_engine_cycle_and_fk(db):
    with db.begin() as c:
        _engine(c)
        _reading(c)
    with pytest.raises(IntegrityError), db.begin() as c:
        _reading(c)  # same (engine, cycle)
    with pytest.raises(IntegrityError), db.begin() as c:
        _reading(c, engine=99)  # unknown engine -> FK violation
    with pytest.raises(IntegrityError), db.begin() as c:
        _reading(c, cycle=0)  # cycle >= 1 check


def _model(c, version, status):
    c.execute(text("INSERT INTO model_versions (version, directory_name, model_type, trained_at, rul_cap, "
                   "failure_horizon, metrics, feature_config, status) VALUES (:v, :v, 'xgboost', now(), 125, 30, "
                   "'{}', '{}', :s)"), {"v": version, "s": status})


def test_only_one_active_model_and_valid_status(db):
    with db.begin() as c:
        _model(c, "m1", "active")
        _model(c, "m2", "archived")
        _model(c, "m3", "archived")
    with pytest.raises(IntegrityError), db.begin() as c:
        _model(c, "m4", "active")
    with pytest.raises(IntegrityError), db.begin() as c:
        _model(c, "m5", "bogus")


def test_one_unresolved_alert_per_engine(db):
    with db.begin() as c:
        _engine(c)
        _reading(c)
        _model(c, "m1", "active")
        c.execute(text("INSERT INTO predictions (engine_id, reading_id, model_version_id, cycle, predicted_rul, "
                       "failure_score, risk_category, history_len) VALUES (1, 1, 1, 1, 50, .5, 'warning', 1)"))

    def alert(c, status):
        c.execute(text("INSERT INTO alerts (engine_id, prediction_id, model_version_id, severity, status, "
                       "predicted_rul) VALUES (1, 1, 1, 'warning', :s, 50)"), {"s": status})

    with db.begin() as c:
        alert(c, "resolved")
        alert(c, "resolved")  # any number of resolved alerts
        alert(c, "open")
    with pytest.raises(IntegrityError), db.begin() as c:
        alert(c, "acknowledged")  # second unresolved for the same engine


# ---- migration 0002: simulation control plane --------------------------------------------------
def _run(c, status="queued", **over):
    vals = dict(status=status, scenario="test", units='[1]', slot=0, off=0, iv=1.0)
    vals.update(over)
    c.execute(text("INSERT INTO simulation_runs (status, scenario, engine_units, replay_slot, id_offset, "
                   "interval_seconds) VALUES (:status, :scenario, CAST(:units AS jsonb), :slot, :off, :iv)"), vals)


def test_orm_and_migrations_agree_no_drift(migrated):
    """`alembic check`: ORM metadata and the migrations describe the same schema (uses the guarded pm_test URL)."""
    from alembic import command
    from alembic.config import Config
    from app.core.config import BACKEND_ROOT

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.attributes["url"] = migrated
    command.check(cfg)  # raises CommandError on drift


def test_only_one_active_simulation_run_at_a_time(db):
    with db.begin() as c:
        _run(c, "stopped")
        _run(c, "failed")
        _run(c, "completed")  # any number of finished runs
        _run(c, "running")
    for other in ("queued", "running", "stopping"):
        with pytest.raises(IntegrityError), db.begin() as c:
            _run(c, other)  # second active run, whatever its state
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET status='stopped' WHERE status='running'"))
        _run(c, "queued")  # slot free again


@pytest.mark.parametrize("over", [
    {"status": "paused"}, {"scenario": "prod"}, {"slot": 10}, {"slot": -1}, {"iv": 0}, {"iv": -1.0}, {"iv": 99999},
])
def test_simulation_run_check_constraints(db, over):
    with pytest.raises(IntegrityError), db.begin() as c:
        _run(c, **over)


def test_engine_origin_defaults_to_api_and_is_constrained(db):
    with db.begin() as c:
        _engine(c)
    with db.connect() as c:
        assert c.execute(text("SELECT origin FROM engines")).scalar() == "api"
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(text("INSERT INTO engines (id, origin) VALUES (2, 'robot')"))


def test_reading_run_link_is_nullable_and_enforced(db):
    with db.begin() as c:
        _engine(c)
        _reading(c)  # ordinary ingestion: no run
    with db.connect() as c:
        assert c.execute(text("SELECT simulation_run_id FROM sensor_readings")).scalar() is None
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(text("UPDATE sensor_readings SET simulation_run_id = 999"))  # FK violation
