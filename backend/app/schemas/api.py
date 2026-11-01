"""All request/response schemas, defined centrally (documented in docs/api.md)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Generic, Literal, TypeVar

from pm_ml.data import SENSOR_COLS, SETTING_COLS
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model, field_validator

T = TypeVar("T")
Risk = Literal["normal", "warning", "critical"]

# Every timestamp the API returns is normalised to UTC, whatever time zone the DB session used.
UtcDatetime = Annotated[datetime, AfterValidator(lambda v: v.astimezone(timezone.utc))]

# Finite and within a generous physical-plausibility bound; real C-MAPSS values are well inside this.
Value = Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]
_VALUE_FIELDS = {c: (Value, Field(...)) for c in [*SETTING_COLS, *SENSOR_COLS]}


class _ReadingBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_id: int = Field(ge=1, le=1_000_000)
    cycle: int = Field(ge=1, le=100_000)
    # Optional. If omitted the server records its receipt time (this is NOT a sensor value).
    event_time: datetime | None = None

    @field_validator("event_time")
    @classmethod
    def _tz_and_not_future(cls, v: datetime | None):
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("event_time must include a timezone offset (e.g. 'Z')")
        if v > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("event_time is too far in the future")
        return v


# Request: engine_id, cycle, optional event_time + 3 operating settings + 21 sensors, all required.
ReadingIn = create_model("ReadingIn", __base__=_ReadingBase, **_VALUE_FIELDS)  # type: ignore[call-overload]
ReadingOut = create_model(
    "ReadingOut", __base__=BaseModel,
    __config__=ConfigDict(from_attributes=True),
    id=(int, ...), engine_id=(int, ...), cycle=(int, ...),
    event_time=(UtcDatetime, ...), ingested_at=(UtcDatetime, ...),
    **{c: (float, ...) for c in [*SETTING_COLS, *SENSOR_COLS]},  # type: ignore[arg-type]
)


class PredictionOut(BaseModel):
    id: int
    engine_id: int
    cycle: int
    predicted_rul: float
    failure_score: float = Field(description="Uncalibrated model score in [0,1]; NOT a validated probability.")
    risk_category: Risk
    model_version: str
    history_len: int
    created_at: UtcDatetime


class DryRunPrediction(BaseModel):
    engine_id: int
    cycle: int
    predicted_rul: float
    failure_score: float
    risk_category: Risk
    model_version: str
    history_len: int
    predicted_at: UtcDatetime
    persisted: Literal[False] = False


class AlertOut(BaseModel):
    id: int
    engine_id: int
    severity: Literal["warning", "critical"]
    status: Literal["open", "acknowledged", "resolved"]
    predicted_rul: float
    prediction_id: int
    model_version: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    acknowledged_at: UtcDatetime | None
    resolved_at: UtcDatetime | None
    resolution_reason: str | None


class IngestResponse(BaseModel):
    status: Literal["created", "duplicate"]
    engine_id: int
    cycle: int
    prediction_status: Literal["ok", "insufficient_history"]
    prediction: PredictionOut | None
    alert_action: Literal["opened", "escalated", "resolved", "none"]
    active_alert: AlertOut | None


class EngineSummary(BaseModel):
    id: int
    last_cycle: int
    last_reading_at: UtcDatetime | None
    latest_prediction: PredictionOut | None
    risk_category: Risk | None
    alert_status: Literal["open", "acknowledged"] | None
    alert_severity: Literal["warning", "critical"] | None


class EngineDetail(EngineSummary):
    created_at: UtcDatetime
    reading_count: int
    prediction_count: int


class ModelOut(BaseModel):
    version: str
    model_type: str
    status: Literal["active", "candidate", "archived"]
    dataset: str | None
    trained_at: UtcDatetime
    activated_at: UtcDatetime | None
    rul_cap: int
    failure_horizon: int
    metrics: dict
    feature_config: dict


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict | list = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: Literal["ok"]


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_id: int = Field(ge=1, le=1_000_000)
    cycle: int | None = Field(None, ge=1, le=100_000, description="Predict at this cycle; default latest stored.")
