"""Load a saved model artifact and predict from raw sensor history.

Artifacts use XGBoost's native JSON format plus metadata.json — no pickle, so loading an
artifact never executes arbitrary code.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .features import FeatureConfig, build_features, feature_names


class ArtifactError(RuntimeError):
    pass


class RULPredictor:
    def __init__(self, metadata: dict, regressor: xgb.XGBRegressor, classifier: xgb.XGBClassifier):
        self.metadata = metadata
        self.version: str = metadata["version"]
        self.cfg = FeatureConfig.from_dict(metadata["feature_config"])
        self.rul_cap: int = metadata["rul_cap"]
        self.horizon: int = metadata["failure_horizon"]
        self.regressor = regressor
        self.classifier = classifier
        # Training-serving skew guard: stored feature order must equal current code's order.
        if metadata["feature_names"] != feature_names(self.cfg):
            raise ArtifactError("Artifact feature names do not match current feature code")

    @classmethod
    def load(cls, artifact_dir: str | Path) -> "RULPredictor":
        d = Path(artifact_dir)
        for name in ("metadata.json", "regressor.json", "classifier.json"):
            if not (d / name).is_file():
                raise ArtifactError(f"Incomplete model artifact in {d}: missing {name}")
        try:
            metadata = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            reg = xgb.XGBRegressor()
            reg.load_model(d / "regressor.json")
            clf = xgb.XGBClassifier()
            clf.load_model(d / "classifier.json")
        except (ValueError, xgb.core.XGBoostError) as e:  # corrupt JSON / model file
            raise ArtifactError(f"Unreadable model artifact in {d}") from e
        return cls(metadata, reg, clf)

    def _features_for_last_row(self, history: pd.DataFrame) -> tuple[int, int, pd.DataFrame]:
        if history.empty:
            raise ValueError("history is empty")
        if history["unit"].nunique() != 1:
            raise ValueError("history must contain exactly one engine")
        h = history.sort_values("cycle", kind="stable").reset_index(drop=True)
        unit, cycle = int(h["unit"].iloc[0]), int(h["cycle"].iloc[-1])
        # Windows are causal and at most max_window long, so the tail is sufficient.
        feats = build_features(h.tail(self.cfg.max_window), self.cfg)
        return unit, cycle, feats.iloc[[-1]]

    def predict(self, history: pd.DataFrame) -> dict:
        """Predict for the most recent cycle in `history` (one engine, readings up to now)."""
        unit, cycle, X = self._features_for_last_row(history)
        rul = float(np.clip(self.regressor.predict(X)[0], 0.0, self.rul_cap))
        score = float(self.classifier.predict_proba(X)[0, 1])
        return {
            "unit": unit,
            "cycle": cycle,
            "predicted_rul": rul,
            # Uncalibrated model score, NOT a validated probability.
            "failure_score": score,
            "failure_horizon": self.horizon,
            "model_version": self.version,
        }
