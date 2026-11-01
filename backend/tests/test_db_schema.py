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
