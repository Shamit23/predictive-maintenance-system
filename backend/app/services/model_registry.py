"""Choose, validate and register the active model at startup.

Rules (fail loudly, never guess):
  1. ACTIVE_MODEL_VERSION set          -> that artifact must exist; it becomes active.
  2. else a model is 'active' in the DB -> its artifact must still exist.
  3. else exactly one artifact exists  -> it becomes active.
  4. else (zero, or several)           -> ModelStartupError.
Every candidate is loaded through `pm_ml.inference.RULPredictor`, which verifies the artifact files and that
its stored feature order equals the current feature code (training-serving skew guard).
Artifacts use XGBoost JSON + metadata.json (no pickle), so loading cannot execute code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pm_ml.inference import ArtifactError, RULPredictor
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ModelVersion
from app.services.errors import ModelStartupError

log = logging.getLogger("app.model_registry")


@dataclass
class LoadedModel:
    predictor: RULPredictor
    version: str
    model_version_id: int


def discover_artifacts(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if (p / "metadata.json").is_file()}


def _choose(session: Session, settings: Settings, artifacts: dict[str, Path]) -> str:
    explicit = settings.active_model_version
    if explicit:
        if explicit not in artifacts:
            raise ModelStartupError(
                f"ACTIVE_MODEL_VERSION='{explicit}' does not match any artifact in MODEL_ARTIFACT_ROOT. "
                f"Available: {sorted(artifacts) or 'none'}."
            )
        return explicit
    active = session.scalar(select(ModelVersion).where(ModelVersion.status == "active"))
    if active is not None:
        if active.version not in artifacts:
            raise ModelStartupError(
                f"The database marks model '{active.version}' active but its artifact is missing from "
                "MODEL_ARTIFACT_ROOT. Restore it or set ACTIVE_MODEL_VERSION explicitly."
            )
        return active.version
    if not artifacts:
        raise ModelStartupError(
            "No model artifact found in MODEL_ARTIFACT_ROOT. Train one: "
            "`cd ml && python -m pm_ml.training`."
        )
    if len(artifacts) > 1:
        raise ModelStartupError(
            f"{len(artifacts)} model artifacts found ({sorted(artifacts)}) and none is marked active. "
            "Set ACTIVE_MODEL_VERSION to choose one explicitly."
        )
    return next(iter(artifacts))


def load_active_model(session: Session, settings: Settings) -> LoadedModel:
    artifacts = discover_artifacts(settings.model_artifact_root)
    now = datetime.now(timezone.utc)
    with session.begin():  # one transaction: choose, validate, register, activate
        chosen = _choose(session, settings, artifacts)
        try:
            predictor = RULPredictor.load(artifacts[chosen])
        except (ArtifactError, KeyError, ValueError) as e:
            log.error("model artifact failed validation", extra={"ctx": {"version": chosen, "cause": str(e)}})
            raise ModelStartupError(f"Model artifact '{chosen}' failed validation (see server log).") from e
        if predictor.version != chosen:
            raise ModelStartupError(f"Artifact folder '{chosen}' declares a different version in its metadata.")
        meta = predictor.metadata
        mv = session.scalar(select(ModelVersion).where(ModelVersion.version == chosen))
        if mv is None:
            mv = ModelVersion(
                version=chosen, directory_name=chosen, model_type="xgboost",
                dataset=meta.get("dataset"),
                trained_at=datetime.fromisoformat(meta["created_utc"]),
                rul_cap=meta["rul_cap"], failure_horizon=meta["failure_horizon"],
                metrics=meta["metrics"], feature_config=meta["feature_config"],
                status="candidate",
            )
            session.add(mv)
            session.flush()
        if mv.status != "active":
            # Deactivate first: the partial unique index allows only one 'active' row.
            session.execute(update(ModelVersion).where(ModelVersion.status == "active").values(status="archived"))
            mv.status = "active"
            mv.activated_at = now
        model_version_id = mv.id
    log.info("active model loaded", extra={"ctx": {"version": chosen}})
    return LoadedModel(predictor=predictor, version=chosen, model_version_id=model_version_id)
