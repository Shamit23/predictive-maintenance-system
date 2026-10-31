import math

import numpy as np
import pandas as pd
import pytest

from conftest import needs_data
from pm_ml.data import COLUMNS, DatasetError, SENSOR_COLS, load_cmapss, load_fd001
from pm_ml.features import INFORMATIVE_SENSORS
from pm_ml.labels import add_rul, cap_rul, failure_within
from pm_ml.metrics import classification_report, mae, nasa_score, regression_report, rmse


# ---- parsing ---------------------------------------------------------------------------
def test_columns_layout():
    assert len(COLUMNS) == 26
    assert COLUMNS[:5] == ["unit", "cycle", "setting_1", "setting_2", "setting_3"]
    assert COLUMNS[5] == "s_1" and COLUMNS[-1] == "s_21"


def test_parse_whitespace_file_with_trailing_spaces(tmp_path):
    rows = []
    for cyc in (1, 2):
        rows.append(f"1 {cyc} " + " ".join(["0.5"] * 24) + "  ")  # trailing double space like NASA files
    p = tmp_path / "t.txt"
    p.write_text("\n".join(rows) + "\n")
    df = load_cmapss(p)
    assert list(df.columns) == COLUMNS and df.shape == (2, 26)
    assert df["unit"].dtype == "int64"


def test_missing_file_gives_helpful_error(tmp_path):
    with pytest.raises(DatasetError, match="data.nasa.gov"):
        load_cmapss(tmp_path / "nope.txt")


def test_non_consecutive_cycles_rejected(tmp_path):
    rows = [f"1 {c} " + " ".join(["0.5"] * 24) for c in (1, 3)]
    p = tmp_path / "t.txt"
    p.write_text("\n".join(rows))
    with pytest.raises(DatasetError, match="consecutive"):
        load_cmapss(p)


@needs_data
def test_fd001_shapes_and_alignment():
    train, test, rul = load_fd001()
    assert train.shape == (20631, 26) and test.shape == (13096, 26)
    assert train["unit"].nunique() == 100 and test["unit"].nunique() == 100
    assert len(rul) == 100 and rul.index.tolist() == list(range(1, 101))
    assert rul.loc[1] == 112  # first line of RUL_FD001.txt belongs to test engine 1


@needs_data
def test_dropped_columns_really_are_uninformative():
    train, test, _ = load_fd001()
    both = pd.concat([train, test])
    dropped = [c for c in SENSOR_COLS if c not in INFORMATIVE_SENSORS]
    assert {"s_1", "s_5", "s_10", "s_16", "s_18", "s_19"} <= set(dropped)
    for c in ["setting_3", "s_1", "s_5", "s_10", "s_16", "s_18", "s_19"]:
        assert both[c].nunique() == 1, c
    assert both["s_6"].nunique() == 2


# ---- labels ----------------------------------------------------------------------------
def test_rul_is_failure_cycle_minus_cycle():
    df = pd.DataFrame({"unit": [1, 1, 1, 2, 2], "cycle": [1, 2, 3, 1, 2]})
    out = add_rul(df)
    assert out["rul"].tolist() == [2, 1, 0, 1, 0]
    assert "rul" not in df.columns  # input not mutated


def test_cap_and_failure_label_use_uncapped_rul():
    rul = pd.Series([0, 29, 30, 31, 125, 126, 300])
    assert cap_rul(rul, 125).tolist() == [0, 29, 30, 31, 125, 125, 125]
    assert failure_within(rul, 30).tolist() == [1, 1, 1, 0, 0, 0, 0]  # RUL<=30 is positive


# ---- metrics ---------------------------------------------------------------------------
def test_nasa_score_formula():
    assert nasa_score([10], [10]) == 0.0
    assert nasa_score([100], [87]) == pytest.approx(math.exp(1) - 1)   # d=-13, early
    assert nasa_score([100], [110]) == pytest.approx(math.exp(1) - 1)  # d=+10, late
    # asymmetry: same |d|, late is penalised more
    assert nasa_score([100], [110]) > nasa_score([100], [90])
    assert nasa_score([50, 50], [40, 60]) == pytest.approx((math.exp(10 / 13) - 1) + (math.exp(1) - 1))


def test_mae_rmse():
    assert mae([0, 0], [3, -1]) == 2.0
    assert rmse([0, 0], [3, 4]) == pytest.approx(math.sqrt(12.5))
    assert regression_report([1, 2], [1, 2])["n"] == 2


def test_classification_report_layout():
    y = [1, 1, 0, 0, 0]
    p = [1, 0, 1, 0, 0]
    r = classification_report(y, p)
    assert r["confusion_matrix"] == [[2, 1], [1, 1]]  # [[TN,FP],[FN,TP]]
    assert r["precision"] == 0.5 and r["recall"] == 0.5 and r["f1"] == 0.5 and r["positives"] == 2
