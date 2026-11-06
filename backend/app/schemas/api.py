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
    event_time=(UtcDatetime, ...), ingested_at=(UtcDatetime, ...), simulation_run_id=(int | None, None),
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
    origin: Literal["api", "simulation"] = Field(
        description="'simulation' only if the engine was created by a validated simulation run; fixed at creation.")
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


# ---- simulation control plane --------------------------------------------------------------------
Scenario = Literal["test", "train"]
RunStatus = Literal["queued", "running", "stopping", "stopped", "completed", "failed"]
UnitNumber = Annotated[int, Field(ge=1, le=100, strict=True)]  # FD001 engine numbers; no "1" -> 1 coercion
Seconds = Annotated[float, Field(allow_inf_nan=False)]
WorkerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
Counter = Annotated[int, Field(ge=0, le=1_000_000_000)]


class RunCreate(BaseModel):
    """Operator request. The server derives the backend engine ids; clients never supply raw ids."""
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario
    engines: list[UnitNumber] = Field(min_length=1, max_length=100)
    interval_seconds: Seconds = Field(1.0, description="Seconds between replay ticks (server-enforced min/max).")
    max_cycles: int | None = Field(None, ge=1, le=100_000, strict=True, description="Optional cap on cycles sent per engine.")
    replay_slot: int = Field(
        0, ge=0, le=9, strict=True,
        description="Selects the engine-id block of the run. A (scenario, engine, slot) can be used by ONE run only: "
                    "if any of its engine ids already holds readings the request fails with 409 engine_ids_in_use "
                    "(listing the free slots). Use another slot to replay an engine again as fresh data.")

    @field_validator("engines")
    @classmethod
    def _unique(cls, v: list[int]):
        if len(set(v)) != len(v):
            raise ValueError("engines must not contain duplicates")
        return v


class RunPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_seconds: Seconds


class RunOut(BaseModel):
    id: int
    status: RunStatus
    scenario: Scenario
    engine_units: list[int]
    engine_ids: list[int] = Field(description="Backend engine ids = unit + id_offset (server-derived).")
    replay_slot: int
    interval_seconds: float
    max_cycles: int | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    ended_at: UtcDatetime | None
    stop_requested_at: UtcDatetime | None
    last_heartbeat_at: UtcDatetime | None
    lease_active: bool = Field(description="True only while a worker holds an unexpired lease on this run.")
    progress: dict = Field(description="Worker-reported per-engine progress (informational).")
    readings_created: int
    readings_duplicate: int
    readings_stored: int = Field(description="Authoritative count of readings stored in the database for this run.")
    error_count: int
    last_error: str | None
    failure_code: str | None
    failure_message: str | None


class SimulationLimits(BaseModel):
    scenarios: list[str]
    units: str
    max_engines: int
    min_interval_seconds: float
    max_interval_seconds: float
    max_cycles: int
    replay_slots: str


class SimulationStatus(BaseModel):
    worker_online: bool = Field(description="At least one worker polled or heartbeated within the lease window.")
    workers_online: int
    active_run: RunOut | None
    last_run: RunOut | None
    limits: SimulationLimits


class WorkerClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: WorkerId


class WorkerPlan(BaseModel):
    run_id: int
    status: Literal["running", "stopping"]
    scenario: Scenario
    engine_units: list[int]
    id_offset: int
    interval_seconds: float
    max_cycles: int | None


class WorkerClaimOut(BaseModel):
    run: WorkerPlan | None


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: WorkerId
    progress: dict = Field(default_factory=dict)
    readings_created: Counter = 0
    readings_duplicate: Counter = 0
    error_count: Counter = 0
    last_error: str | None = Field(None, max_length=500)

    @field_validator("progress")
    @classmethod
    def _bounded(cls, v: dict):
        import json

        if len(json.dumps(v, default=str)) > 20_000:
            raise ValueError("progress is too large")
        return v


class HeartbeatOut(BaseModel):
    status: Literal["running", "stopping"]
    interval_seconds: float


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: WorkerId
    outcome: Literal["completed", "stopped", "failed"]
    failure_code: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,49}$")
    failure_message: str | None = Field(None, max_length=500)
