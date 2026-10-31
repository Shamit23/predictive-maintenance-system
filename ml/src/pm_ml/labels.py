"""RUL and failure-horizon labels. Only valid for run-to-failure data (training engines)."""
from __future__ import annotations

import pandas as pd

RUL_CAP = 125
FAILURE_HORIZON = 30


def add_rul(df: pd.DataFrame) -> pd.DataFrame:
    """rul = failure_cycle - cycle, where failure_cycle is the engine's last recorded cycle.

    Uncapped. The last row of each engine therefore has rul == 0.
    """
    out = df.copy()
    out["rul"] = out.groupby("unit")["cycle"].transform("max") - out["cycle"]
    return out


def cap_rul(rul, cap: int = RUL_CAP):
    """Piecewise-linear target: early life is treated as 'healthy' at `cap` cycles."""
    return rul.clip(upper=cap) if hasattr(rul, "clip") else min(rul, cap)


def failure_within(rul, horizon: int = FAILURE_HORIZON):
    """Positive class (1) = failure expected within `horizon` cycles, i.e. true RUL <= horizon.

    Always derived from the *uncapped* RUL.
    """
    return (rul <= horizon).astype("int64")
