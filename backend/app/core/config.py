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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: SecretStr
    test_database_url: SecretStr | None = None
    api_key: SecretStr = Field(min_length=16)

    model_artifact_root: Path = REPO_ROOT / "ml" / "artifacts"
    active_model_version: str | None = None

    cors_origins: str = "http://localhost:5173"

    # Demonstration thresholds, not validated real-world safety limits.
    risk_warning_rul: float = Field(60.0, gt=0)
    risk_critical_rul: float = Field(30.0, gt=0)
    min_history_readings: int = Field(1, ge=1, le=20)

    log_level: str = "INFO"

    @field_validator("active_model_version", mode="before")
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
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
