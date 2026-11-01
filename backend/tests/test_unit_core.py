"""No database needed."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.risk import risk_category
from app.schemas.api import ReadingIn
from conftest import API_KEY, reading


@pytest.mark.parametrize("rul,expected", [
    (200, "normal"), (60.01, "normal"), (60, "warning"), (45, "warning"), (30.01, "warning"),
    (30, "critical"), (5, "critical"), (0, "critical"),
])
def test_risk_boundaries(rul, expected):
    assert risk_category(rul, warning=60, critical=30) == expected


def test_risk_thresholds_are_configurable():
    assert risk_category(80, warning=100, critical=90) == "critical"


def _settings(**kw):
    return Settings(_env_file=None, database_url="postgresql+psycopg://u:SUPERSECRET@h/db", api_key=API_KEY, **kw)


def test_settings_validation():
    with pytest.raises(ValidationError):
        _settings(risk_warning_rul=30, risk_critical_rul=60)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", api_key="short")
    assert _settings(active_model_version="").active_model_version is None
    assert _settings(cors_origins="http://a.test, http://b.test").cors_origin_list == ["http://a.test", "http://b.test"]


def test_settings_never_reveal_secrets():
    s = _settings()
    assert "SUPERSECRET" not in repr(s) and API_KEY not in repr(s) and API_KEY not in str(s)


def test_reading_schema_accepts_valid_payload():
    r = ReadingIn(**reading())
    assert r.engine_id == 1 and r.event_time is None


@pytest.mark.parametrize("mutation", [
    {"cycle": 0}, {"cycle": -3}, {"engine_id": 0}, {"engine_id": "abc"}, {"cycle": 1.5},
    {"s_2": float("nan")}, {"s_2": float("inf")}, {"s_2": 1e9}, {"s_2": "high"}, {"unexpected": 1},
    {"event_time": "2024-01-01T00:00:00"},  # naive timestamp
    {"event_time": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
])
def test_reading_schema_rejects_invalid(mutation):
    with pytest.raises(ValidationError):
        ReadingIn(**reading(**mutation))


def test_reading_schema_requires_every_sensor():
    body = reading()
    del body["s_7"]
    with pytest.raises(ValidationError):
        ReadingIn(**body)
