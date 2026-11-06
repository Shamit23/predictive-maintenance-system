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


# ---- control key & simulation limits -----------------------------------------------------------
def test_control_key_is_optional_secret_distinct_from_api_key():
    assert _settings().control_api_key is None
    assert _settings(control_api_key="").control_api_key is None  # blank env var => disabled
    ck = "control-key-abcdefghijklmnop"
    s = _settings(control_api_key=ck)
    assert ck not in repr(s) and ck not in str(s)
    with pytest.raises(ValidationError, match="differ"):
        _settings(control_api_key=API_KEY)
    with pytest.raises(ValidationError, match="16"):
        _settings(control_api_key="short")


def test_simulation_limit_validation():
    with pytest.raises(ValidationError):
        _settings(sim_min_interval_seconds=5, sim_max_interval_seconds=1)
    with pytest.raises(ValidationError):
        _settings(sim_lease_seconds=60, sim_stale_timeout_seconds=30)
    with pytest.raises(ValidationError):
        _settings(sim_max_engines=0)
    s = _settings()
    assert (s.sim_max_engines, s.sim_min_interval_seconds, s.sim_lease_seconds) == (10, 0.1, 30)


# ---- placeholder keys are rejected; blank control key stays disabled ------------------------------------------------
_PLACEHOLDERS = ["CHANGE_ME_TO_A_LONG_RANDOM_STRING", "CHANGE_ME_TO_A_DIFFERENT_LONG_RANDOM_STRING",
                 "change_me_in_lowercase_and_long_enough", "  CHANGE_ME_with_leading_spaces_to_be_long_enough",
                 "CHANGE_ME"]


@pytest.mark.parametrize("value", _PLACEHOLDERS)
def test_placeholder_api_key_is_rejected(value):
    with pytest.raises(ValidationError, match="API_KEY is still the .env.example placeholder"):
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db", api_key=value)


@pytest.mark.parametrize("value", _PLACEHOLDERS)
def test_placeholder_control_key_is_rejected(value):
    with pytest.raises(ValidationError, match="CONTROL_API_KEY is still a placeholder"):
        _settings(control_api_key=value)


def test_blank_control_key_is_accepted_and_means_disabled():
    for blank in ("", None):
        assert _settings(control_api_key=blank).control_api_key is None


def test_valid_distinct_keys_are_accepted_and_not_flagged_as_placeholders():
    s = _settings(control_api_key="a-real-control-key-0123456789")
    assert s.control_api_key is not None and s.control_api_key.get_secret_value() != API_KEY
    # a key that merely contains the words later in the string is fine
    assert _settings(control_api_key="prefix-CHANGE_ME-but-not-at-the-start").control_api_key is not None


def test_existing_length_and_distinctness_rules_still_apply():
    with pytest.raises(ValidationError, match="differ"):
        _settings(control_api_key=API_KEY)
    with pytest.raises(ValidationError, match="16"):
        _settings(control_api_key="short-key")


def test_validation_errors_never_echo_a_secret_value():
    secret_looking = "CHANGE_ME_but_this_pretends_to_be_a_real_secret_12345"
    with pytest.raises(ValidationError) as ei:
        _settings(control_api_key=secret_looking)
    assert secret_looking not in str(ei.value) and API_KEY not in str(ei.value)


def test_an_unedited_copy_of_env_example_cannot_start_the_api(tmp_path):
    """The footgun: `Copy-Item .env.example .env` and forgetting to edit it must fail loudly, not run with public keys."""
    from pathlib import Path

    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    copy = tmp_path / "copied.env"
    copy.write_text(example, encoding="utf-8")
    with pytest.raises(ValidationError, match="API_KEY is still the .env.example placeholder"):
        Settings(_env_file=copy)


def test_env_example_with_only_the_api_key_edited_loads_with_operator_controls_disabled(tmp_path):
    from pathlib import Path

    lines = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8").splitlines()
    edited = [("API_KEY=" + "x" * 8 + "-a-real-api-key-123456") if l.startswith("API_KEY=") else l for l in lines]
    copy = tmp_path / "edited.env"
    copy.write_text("\n".join(edited), encoding="utf-8")
    s = Settings(_env_file=copy)
    assert s.control_api_key is None  # blank in the example => operator controls fail closed (503 control_disabled)


def test_too_short_api_key_is_rejected_without_echoing_its_value():
    """A mistyped real key must not be printed in the startup error (pydantic field errors would echo it)."""
    typo = "hunter2-realish"  # 15 characters
    with pytest.raises(ValidationError, match="API_KEY must be at least 16 characters") as ei:
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db", api_key=typo)
    assert typo not in str(ei.value) and "input_value" not in str(ei.value)
    assert Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db", api_key=typo + "x").api_key


@pytest.mark.parametrize("bad", [
    {"api_key": "short"},                                   # model-level check
    {"api_key": API_KEY, "risk_warning_rul": "not-a-number"},  # field-level type error
    {"api_key": API_KEY, "sim_max_engines": 0},              # field-level range error
    {"api_key": API_KEY, "control_api_key": "CHANGE_ME_placeholder_that_is_long_enough"},
])
def test_no_validation_error_ever_echoes_the_database_password_or_keys(bad):
    """Startup validation errors go to the console/logs: they must name the problem, never print input values."""
    pw = "SuperSecretDbPassword-9876"
    with pytest.raises(ValidationError) as ei:
        Settings(_env_file=None, database_url=f"postgresql+psycopg://pm_app:{pw}@localhost/pm_dev", **bad)
    text = str(ei.value)
    assert pw not in text and "input_value" not in text
    assert API_KEY not in text and "not-a-number" not in text and "placeholder_that_is_long_enough" not in text


def test_worker_heartbeat_defaults_leave_a_safety_margin_below_the_server_lease():
    """Keeps two packages' defaults consistent: the worker's heartbeat interval + grace must stay well below the lease.

    Measured (docs/simulator.md): ~12.5 s (HTTP 5xx) to ~17.5 s (unreachable backend) without a confirmed heartbeat
    with these defaults. The guard demands >= 10 s of headroom for a slow attempt or a long tick; it is a consistency
    check, not a proof of a hard bound.
    """
    import inspect

    worker = pytest.importorskip("pm_simulator.worker")
    interval = inspect.signature(worker.run_worker).parameters["heartbeat_interval"].default
    lease = _settings().sim_lease_seconds
    assert interval + worker.DEFAULT_HEARTBEAT_GRACE + 10 <= lease, (
        f"heartbeat_interval ({interval}) + DEFAULT_HEARTBEAT_GRACE ({worker.DEFAULT_HEARTBEAT_GRACE}) + 10 s headroom "
        f"exceeds the default SIM_LEASE_SECONDS ({lease})")
