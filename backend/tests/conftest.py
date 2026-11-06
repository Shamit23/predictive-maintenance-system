"""Test fixtures.

Integration tests run against a REAL PostgreSQL database (TEST_DATABASE_URL, e.g. pm_test). There is no SQLite
fallback: if nothing is configured, or TEST_DATABASE_URL is missing, the DB tests are skipped with a clear reason.
A configured-but-INVALID setup (e.g. a placeholder or too-short key in .env) aborts the run with the problem
instead of silently skipping (see `resolve_test_database_url`).
Safety: the test database name MUST end with "_test" (otherwise the run aborts) because the suite
drops and recreates the schema via Alembic and truncates tables between tests.

Models used here are tiny synthetic XGBoost artifacts in the real artifact format, so the suite needs neither
the NASA data nor a trained model. `test_real_model.py` additionally checks the real artifact when present.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pm_ml.data import SENSOR_COLS, SETTING_COLS
from pm_ml.features import FeatureConfig, feature_names
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_ROOT, REPO_ROOT, Settings, get_settings
from app.main import create_app

API_KEY = "test-api-key-0123456789abcdef"
HEADERS = {"X-API-Key": API_KEY}
CONTROL_KEY = "test-control-key-fedcba9876543210"
CONTROL_HEADERS = {"X-Control-Key": CONTROL_KEY}
VALUE_COLS = [*SETTING_COLS, *SENSOR_COLS]
TABLES = ("ingestion_errors, alerts, predictions, model_versions, sensor_readings, simulation_runs, "
          "simulation_workers, engines")


# ---- model artifacts --------------------------------------------------------------------------
def make_artifact(root: Path, version: str) -> Path:
    cfg = FeatureConfig()
    names = feature_names(cfg)
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(200, len(names))), columns=names)
    y = rng.uniform(0, 125, 200)
    reg = xgb.XGBRegressor(n_estimators=5, max_depth=2, n_jobs=1, random_state=0).fit(X, y)
    clf = xgb.XGBClassifier(n_estimators=5, max_depth=2, n_jobs=1, random_state=0).fit(X, (y <= 30).astype(int))
    d = root / version
    d.mkdir(parents=True)
    reg.save_model(d / "regressor.json")
    clf.save_model(d / "classifier.json")
    (d / "metadata.json").write_text(json.dumps({
        "version": version, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "synthetic-test", "rul_cap": 125, "failure_horizon": 30,
        "feature_config": cfg.to_dict(), "feature_names": names,
        "metrics": {"note": "synthetic test model"},
    }))
    return d


class StubPredictor:
    """Returns a fixed RUL so alert-lifecycle tests are deterministic. Mimics RULPredictor's interface."""

    def __init__(self, rul: float, version: str = "stub-model"):
        self.rul, self.version, self.cfg = rul, version, FeatureConfig()
        self.calls = 0

    def predict(self, history: pd.DataFrame) -> dict:
        self.calls += 1
        return {"unit": int(history["unit"].iloc[0]), "cycle": int(history["cycle"].max()),
                "predicted_rul": float(self.rul), "failure_score": 0.5, "failure_horizon": 30,
                "model_version": self.version}


# ---- database ---------------------------------------------------------------------------------
_SETTING_ENV_VARS = ("DATABASE_URL", "TEST_DATABASE_URL", "API_KEY", "CONTROL_API_KEY")


def _db_settings_configured() -> bool:
    """True if the developer configured anything: a .env file exists or a settings variable is set.

    Only the file's existence is checked; it is never opened or read here.
    """
    return (REPO_ROOT / ".env").is_file() or any(k in os.environ for k in _SETTING_ENV_VARS)


def _validation_problems(e: ValidationError) -> str:
    """'field: problem' pairs for a settings error. Never includes input values (they may be secrets)."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: {err['msg']}"
        for err in e.errors(include_input=False, include_url=False, include_context=False))


def resolve_test_database_url(load_settings=get_settings, configured=_db_settings_configured) -> str:
    """The guarded TEST_DATABASE_URL, or a pytest outcome explaining why database tests cannot run.

    * nothing configured at all (no .env, no settings variables)  -> skip, with the reason (fresh checkout, no database)
    * configured but the settings are INVALID (placeholder/short key, missing required value, bad thresholds, ...)
                                                                   -> abort the run with the problems (never a silent skip)
    * TEST_DATABASE_URL not set                                    -> skip, with the reason (no SQLite fallback)
    * database name not ending in '_test' or equal to DATABASE_URL -> abort (unchanged safety guard)
    """
    try:
        s = load_settings()
    except ValidationError as e:
        if not configured():
            pytest.skip("No .env file or DATABASE_URL/API_KEY in the environment: PostgreSQL integration tests are "
                        "skipped (create .env from .env.example to run them)")
        pytest.exit("Refusing to run: the settings are invalid, so the PostgreSQL tests cannot run "
                    f"({_validation_problems(e)}). Fix .env (see .env.example). A configured but invalid setup is "
                    "never silently skipped.", returncode=2)
    if s.test_database_url is None:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration tests need it (no SQLite fallback)")
    url = s.test_database_url.get_secret_value()
    name = make_url(url).database or ""
    if not name.endswith("_test") or url == s.database_url.get_secret_value():
        pytest.exit("Refusing to run: TEST_DATABASE_URL must point to a database whose name ends with '_test' "
                    "and differs from DATABASE_URL.", returncode=2)
    return url


@pytest.fixture(scope="session")
def db_url() -> str:
    return resolve_test_database_url()


@pytest.fixture(scope="session")
def migrated(db_url):
    """Recreate the schema from migrations: downgrade to base, upgrade to head (also tests both directions)."""
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.attributes["url"] = db_url
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return db_url


@pytest.fixture(scope="session")
def sa_engine(migrated):
    eng = create_engine(migrated)
    yield eng
    eng.dispose()


@pytest.fixture
def db(sa_engine):
    """Clean tables for every test; returns the SQLAlchemy engine."""
    with sa_engine.begin() as c:
        # Fail fast (instead of hanging) if an earlier test leaked an open transaction.
        c.execute(text("SET LOCAL lock_timeout = '10s'"))
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    return sa_engine


def scalar(db, sql: str, **params):
    with db.connect() as c:
        return c.execute(text(sql), params).scalar()


# ---- app --------------------------------------------------------------------------------------
@pytest.fixture
def artifact_root(tmp_path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    make_artifact(root, "test-model-a")
    return root


@pytest.fixture
def make_settings(db, artifact_root):
    def _make(**overrides) -> Settings:
        kw = dict(database_url=str(db.url.render_as_string(hide_password=False)), api_key=API_KEY,
                  control_api_key=CONTROL_KEY, model_artifact_root=artifact_root)
        kw.update(overrides)
        return Settings(_env_file=None, **kw)
    return _make


@pytest.fixture
def client(make_settings):
    with TestClient(create_app(make_settings()), raise_server_exceptions=False) as c:
        yield c


def reading(engine_id: int = 1, cycle: int = 1, **over) -> dict:
    seed = engine_id * 1000 + cycle if isinstance(engine_id, int) and isinstance(cycle, int) else 0
    rng = np.random.default_rng(abs(seed))
    body = {"engine_id": engine_id, "cycle": cycle, **{c: round(float(rng.normal()), 4) for c in VALUE_COLS}}
    body.update(over)
    return body


def post_reading(client, engine_id=1, cycle=1, headers=HEADERS, **over):
    return client.post("/api/v1/readings", json=reading(engine_id, cycle, **over), headers=headers)


def fill_from_frame(client, frame) -> dict:
    """Post every row of a C-MAPSS frame (columns unit, cycle, settings, sensors) in order; return last response."""
    last = {}
    for row in frame.to_dict("records"):
        body = {"engine_id": int(row["unit"]), "cycle": int(row["cycle"]), **{c: float(row[c]) for c in VALUE_COLS}}
        r = client.post("/api/v1/readings", json=body, headers=HEADERS)
        assert r.status_code == 201, r.text
        last = r.json()
    return last


def fill(client, engine_id: int, n: int, start: int = 1):
    for c in range(start, start + n):
        r = post_reading(client, engine_id, c)
        assert r.status_code == 201, r.text
