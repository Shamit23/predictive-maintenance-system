"""Parse and validate NASA C-MAPSS files.

Files are whitespace-delimited, headerless, with trailing spaces. 26 columns:
unit, cycle, 3 operational settings, 21 sensors.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SETTING_COLS = [f"setting_{i}" for i in range(1, 4)]
SENSOR_COLS = [f"s_{i}" for i in range(1, 22)]
COLUMNS = ["unit", "cycle", *SETTING_COLS, *SENSOR_COLS]

# Repo-relative default: <repo>/ml/data/raw
DEFAULT_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


class DatasetError(RuntimeError):
    pass


def _require(path: Path) -> Path:
    if not path.is_file():
        raise DatasetError(
            f"Missing dataset file: {path}\n"
            "Download CMAPSSData.zip from https://data.nasa.gov/docs/legacy/CMAPSSData.zip "
            "and extract it into ml/data/raw/ (see docs/data_dictionary.md)."
        )
    return path


def validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Structural checks shared by every loader."""
    if list(df.columns) != COLUMNS:
        raise DatasetError(f"Unexpected columns: {list(df.columns)}")
    if df.isna().any().any():
        raise DatasetError("Dataset contains missing values")
    if not np.isfinite(df[SENSOR_COLS + SETTING_COLS].to_numpy()).all():
        raise DatasetError("Dataset contains non-finite values")
    for unit, g in df.groupby("unit", sort=False):
        c = g["cycle"].to_numpy()
        if c[0] != 1 or not (np.diff(c) == 1).all():
            raise DatasetError(f"Engine {unit}: cycles must be consecutive starting at 1")
    return df


def load_cmapss(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(_require(Path(path)), sep=r"\s+", header=None, names=COLUMNS)
    df[["unit", "cycle"]] = df[["unit", "cycle"]].astype("int64")
    return validate_frame(df)


def load_fd001(raw_dir: str | Path = DEFAULT_RAW_DIR):
    """Return (train_df, test_df, test_rul) for FD001.

    test_rul is a Series indexed by engine id (1..100): the true RUL *after the last
    observed test cycle*. It is kept separate from model inputs.
    """
    raw = Path(raw_dir)
    train = load_cmapss(raw / "train_FD001.txt")
    test = load_cmapss(raw / "test_FD001.txt")
    rul = pd.read_csv(_require(raw / "RUL_FD001.txt"), header=None, names=["rul"])["rul"]
    units = sorted(test["unit"].unique())
    if len(rul) != len(units):
        raise DatasetError(f"RUL file has {len(rul)} rows but test has {len(units)} engines")
    # Row i of RUL_FD001.txt belongs to the i-th engine (ids 1..N, in order).
    return train, test, pd.Series(rul.to_numpy(), index=pd.Index(units, name="unit"), name="rul")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
