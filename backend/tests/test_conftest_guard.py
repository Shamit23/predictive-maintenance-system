"""The database-test guard in conftest.py: invalid settings abort loudly, 'nothing configured' skips, and the
'_test' database-name protection is unchanged. Pure unit tests: the settings loader and the 'is anything configured'
probe are injected, so no database, no real .env and no environment is touched.

Note: an unexpected *skip* inside these tests must FAIL them (otherwise a regression to the old silent-skip behaviour
would just show up as skipped tests), hence the expect_* helpers below.
"""
import pytest

import conftest
from app.core.config import Settings
from conftest import API_KEY, resolve_test_database_url

DEV = "postgresql+psycopg://pm_app:DevPw-1234@localhost:5432/pm_dev"
TEST = "postgresql+psycopg://pm_app:TestPw-5678@localhost:5432/pm_test"
SKIP, EXIT = pytest.skip.Exception, pytest.exit.Exception


def valid(**over):
    kw = dict(database_url=DEV, test_database_url=TEST, api_key=API_KEY)
    kw.update(over)
    return lambda: Settings(_env_file=None, **kw)


def configured(flag=True):
    return lambda: flag


def expect_exit(loader, is_configured):
    with pytest.raises((SKIP, EXIT)) as ei:
        resolve_test_database_url(loader, is_configured)
    assert isinstance(ei.value, EXIT), f"expected the run to ABORT, but it was skipped: {ei.value}"
    assert ei.value.returncode == 2
    return str(ei.value)


def expect_skip(loader, is_configured):
    with pytest.raises((SKIP, EXIT)) as ei:
        resolve_test_database_url(loader, is_configured)
    assert isinstance(ei.value, SKIP), f"expected a skip, but the run was aborted: {ei.value}"
    return str(ei.value)


def test_valid_settings_return_the_test_url():
    assert resolve_test_database_url(valid(), configured()) == TEST


# ---- invalid settings must fail loudly, never skip ------------------------------------------------------------------
@pytest.mark.parametrize("bad,expected", [
    ({"api_key": "CHANGE_ME_TO_A_LONG_RANDOM_STRING"}, "API_KEY is still the .env.example placeholder"),
    ({"api_key": "too-short-key"}, "API_KEY must be at least 16 characters"),
    ({"control_api_key": "CHANGE_ME_placeholder_that_is_long_enough"}, "CONTROL_API_KEY is still a placeholder"),
    ({"control_api_key": API_KEY}, "CONTROL_API_KEY must differ from API_KEY"),
    ({"risk_warning_rul": 10, "risk_critical_rul": 20}, "RISK_CRITICAL_RUL must be lower"),
])
def test_configured_but_invalid_settings_abort_the_run_and_say_why(bad, expected):
    msg = expect_exit(valid(**bad), configured(True))
    assert expected in msg and "never silently skipped" in msg


def test_the_abort_message_never_contains_secret_values():
    msg = expect_exit(valid(api_key="CHANGE_ME_secret_looking_value_1"), configured(True))
    for secret in ("DevPw-1234", "TestPw-5678", "CHANGE_ME_secret_looking_value_1", API_KEY, "input_value"):
        assert secret not in msg


def test_a_configured_env_that_lacks_required_values_aborts_instead_of_skipping():
    def missing_api_key():
        return Settings(_env_file=None, database_url=DEV)  # no api_key at all

    msg = expect_exit(missing_api_key, configured(True))
    assert "api_key: Field required" in msg and "DevPw-1234" not in msg


# ---- nothing configured -> skip (documented behaviour) ---------------------------------------------------------------
def test_nothing_configured_skips_with_a_reason():
    def invalid():
        return Settings(_env_file=None, database_url=DEV)  # also invalid, but nobody configured anything

    assert ".env" in expect_skip(invalid, configured(False))


def test_missing_test_database_url_still_skips_with_a_reason():
    assert "TEST_DATABASE_URL is not set" in expect_skip(valid(test_database_url=None), configured())


# ---- the '_test' protection is unchanged ------------------------------------------------------------------------------
def test_database_name_must_end_with_test():
    assert "ends with '_test'" in expect_exit(valid(test_database_url=DEV), configured())  # pm_dev is not *_test
    assert "ends with '_test'" in expect_exit(
        valid(test_database_url=TEST.replace("pm_test", "pm_testing")), configured())


def test_test_database_must_differ_from_the_dev_database():
    same = "postgresql+psycopg://pm_app:Same-1234@localhost:5432/pm_test"
    assert "differs from DATABASE_URL" in expect_exit(valid(database_url=same, test_database_url=same), configured())


# ---- the 'is anything configured' probe (existence only; the real .env is never opened) -----------------------------------
def test_configured_probe_sees_settings_variables_and_a_dotenv_file(monkeypatch, tmp_path):
    for k in conftest._SETTING_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(conftest, "REPO_ROOT", tmp_path)          # a temp dir: no real .env involved
    assert conftest._db_settings_configured() is False
    monkeypatch.setenv("API_KEY", "x")
    assert conftest._db_settings_configured() is True
    monkeypatch.delenv("API_KEY")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert conftest._db_settings_configured() is True


def test_probe_checks_existence_only(monkeypatch, tmp_path):
    """It must not open the file: a directory named .env cannot be read as a file, and the probe still works."""
    for k in conftest._SETTING_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(conftest, "REPO_ROOT", tmp_path)
    (tmp_path / ".env").mkdir()
    assert conftest._db_settings_configured() is False
