"""Causal feature engineering — the ONE implementation used by training and serving.

Every feature at cycle t depends only on readings at cycles <= t of the same engine.
Both offline (whole run) and online (history up to now) call `build_features`.

Early cycles: windows use whatever history exists (min_periods=1). std and slope are 0 when
fewer than 2 points exist. `history_len` (readings available, capped at the largest window)
tells the model how much history backed each row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

# Constant in FD001 train+test (setting_3, s_1, s_5, s_10, s_16, s_18, s_19) or 2-valued (s_6),
# and operating settings 1/2 (single operating condition in FD001). See docs/data_dictionary.md.
INFORMATIVE_SENSORS = [
    "s_2", "s_3", "s_4", "s_7", "s_8", "s_9", "s_11",
    "s_12", "s_13", "s_14", "s_15", "s_17", "s_20", "s_21",
]


@dataclass(frozen=True)
class FeatureConfig:
    sensors: tuple[str, ...] = tuple(INFORMATIVE_SENSORS)
    windows: tuple[int, ...] = (5, 10, 20)
    slope_windows: tuple[int, ...] = (10,)

    @property
    def max_window(self) -> int:
        return max(self.windows + self.slope_windows)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: list(v) for k, v in d.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureConfig":
        return cls(**{k: tuple(v) for k, v in d.items()})


def feature_names(cfg: FeatureConfig) -> list[str]:
    """Canonical column order. Models are trained and served with exactly this order."""
    names = ["cycle", "history_len"]
    for s in cfg.sensors:
        names.append(s)
        for w in cfg.windows:
            names += [f"{s}_mean_{w}", f"{s}_std_{w}", f"{s}_min_{w}", f"{s}_max_{w}"]
        for w in cfg.slope_windows:
            names.append(f"{s}_slope_{w}")
    return names


def _window_slope(x: np.ndarray) -> float:
    """Least-squares slope per cycle over the available points (0 if < 2 points)."""
    n = len(x)
    if n < 2:
        return 0.0
    t = np.arange(n, dtype=float)
    t -= t.mean()
    return float((t * (x - x.mean())).sum() / (t * t).sum())


def build_features(df: pd.DataFrame, cfg: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    """Features for every row of `df` (columns: unit, cycle, sensors...).

    Input must be sorted by (unit, cycle) with unique cycles per unit; this is validated.
    Returns a frame indexed like `df` with columns exactly `feature_names(cfg)`.
    """
    missing = [c for c in ("unit", "cycle", *cfg.sensors) if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if df.empty:
        raise ValueError("Cannot build features from an empty frame")
    if not np.isfinite(df[list(cfg.sensors)].to_numpy(dtype=float)).all():
        raise ValueError("Sensor values must be finite")
    key = df[["unit", "cycle"]]
    if key.duplicated().any():
        raise ValueError("Duplicate (unit, cycle) rows")
    if not key.equals(key.sort_values(["unit", "cycle"])):
        raise ValueError("Input must be sorted by (unit, cycle)")

    g = df.groupby("unit", sort=False)
    cols: dict[str, pd.Series] = {
        "cycle": df["cycle"].astype(float),
        "history_len": g.cumcount().add(1).clip(upper=cfg.max_window).astype(float),
    }
    for s in cfg.sensors:
        cols[s] = df[s].astype(float)
        gs = g[s]
        for w in cfg.windows:
            r = gs.rolling(w, min_periods=1)
            cols[f"{s}_mean_{w}"] = r.mean().reset_index(level=0, drop=True)
            cols[f"{s}_std_{w}"] = r.std(ddof=0).reset_index(level=0, drop=True)
            cols[f"{s}_min_{w}"] = r.min().reset_index(level=0, drop=True)
            cols[f"{s}_max_{w}"] = r.max().reset_index(level=0, drop=True)
        for w in cfg.slope_windows:
            cols[f"{s}_slope_{w}"] = (
                gs.rolling(w, min_periods=1).apply(_window_slope, raw=True).reset_index(level=0, drop=True)
            )
    out = pd.DataFrame(cols, index=df.index)[feature_names(cfg)]
    return out.fillna(0.0)
