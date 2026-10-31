import numpy as np
import pandas as pd
import pytest

from pm_ml.data import COLUMNS, DEFAULT_RAW_DIR
from pm_ml.features import INFORMATIVE_SENSORS

HAVE_DATA = (DEFAULT_RAW_DIR / "train_FD001.txt").is_file()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="FD001 files not in ml/data/raw")


def make_engine(unit: int, n: int, seed: int = 0) -> pd.DataFrame:
    """Synthetic engine with drifting sensors, in raw C-MAPSS column layout."""
    rng = np.random.default_rng(seed + unit)
    d = {"unit": unit, "cycle": np.arange(1, n + 1)}
    for c in COLUMNS[2:]:
        d[c] = rng.normal(size=n) + (np.arange(n) * 0.05 if c in INFORMATIVE_SENSORS else 0)
    return pd.DataFrame(d)[COLUMNS]


@pytest.fixture
def two_engines() -> pd.DataFrame:
    return pd.concat([make_engine(1, 40), make_engine(2, 30)], ignore_index=True)
