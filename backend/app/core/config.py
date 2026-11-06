"""Application settings, read from environment variables / the repo-root `.env` file.

Secrets are `SecretStr`, so printing or logging a Settings object never reveals them.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
PLACEHOLDER_PREFIX = "CHANGE_ME"  # prefix of every secret placeholder in .env.example


def _is_placeholder(secret: SecretStr) -> bool:
    return secret.get_secret_value().strip().upper().startswith(PLACEHOLDER_PREFIX)


class Settings(BaseSettings):
    # hide_input_in_errors: validation errors must never echo input values (they would print DATABASE_URL / key
    # fragments to the console and logs when startup validation fails).
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    test_database_url: SecretStr | None = None
    # Length is enforced in `_thresholds_ordered` below (not with Field(min_length=...)): pydantic's field-level
    # errors echo the offending value, which would print a mistyped real key to the console/logs.
    api_key: SecretStr

    model_artifact_root: Path = REPO_ROOT / "ml" / "artifacts"
    active_model_version: str | None = None

    cors_origins: str = "http://localhost:5173"

    # Demonstration thresholds, not validated real-world safety limits.
    risk_warning_rul: float = Field(60.0, gt=0)
    risk_critical_rul: float = Field(30.0, gt=0)
    min_history_readings: int = Field(1, ge=1, le=20)

    log_level: str = "INFO"

    # Operator actions (start/stop/speed). Separate from api_key; unset => those endpoints are disabled (503).
    control_api_key: SecretStr | None = None

    # Simulation control plane limits (the server, not the client, decides what is allowed).
    sim_max_engines: int = Field(10, ge=1, le=100)
    sim_min_interval_seconds: float = Field(0.1, gt=0)
    sim_max_interval_seconds: float = Field(60.0, gt=0)
    sim_lease_seconds: int = Field(30, ge=1)           # worker lease; renewed by every heartbeat
    sim_queue_timeout_seconds: int = Field(60, ge=1)   # queued run with no worker claim -> failed(no_worker)
    sim_stale_timeout_seconds: int = Field(120, ge=1)  # running run without heartbeat -> failed(worker_lost)

    @field_validator("active_model_version", "control_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return v or None

    @field_validator("model_artifact_root")
    @classmethod
    def _resolve_root(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @model_validator(mode="after")
    def _thresholds_ordered(self):
        if self.risk_critical_rul >= self.risk_warning_rul:
            raise ValueError("RISK_CRITICAL_RUL must be lower than RISK_WARNING_RUL")
        # An unedited copy of .env.example must not start the API with publicly known keys.
        # (Messages name the variable only; secret values are never echoed.)
        if _is_placeholder(self.api_key):
            raise ValueError("API_KEY is still the .env.example placeholder (it starts with CHANGE_ME); "
                             "set a real random value")
        if len(self.api_key.get_secret_value()) < 16:
            raise ValueError("API_KEY must be at least 16 characters")
        if self.control_api_key is not None:
            if _is_placeholder(self.control_api_key):
                raise ValueError("CONTROL_API_KEY is still a placeholder (it starts with CHANGE_ME); set a real "
                                 "random value, or leave it blank to keep operator controls disabled")
            ck = self.control_api_key.get_secret_value()
            if len(ck) < 16:
                raise ValueError("CONTROL_API_KEY must be at least 16 characters")
            if ck == self.api_key.get_secret_value():
                raise ValueError("CONTROL_API_KEY must differ from API_KEY")
        if self.sim_min_interval_seconds >= self.sim_max_interval_seconds:
            raise ValueError("SIM_MIN_INTERVAL_SECONDS must be lower than SIM_MAX_INTERVAL_SECONDS")
        if self.sim_stale_timeout_seconds < self.sim_lease_seconds:
            raise ValueError("SIM_STALE_TIMEOUT_SECONDS must be >= SIM_LEASE_SECONDS")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
