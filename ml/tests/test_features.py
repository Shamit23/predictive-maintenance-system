import numpy as np
import pandas as pd
import pytest

from conftest import make_engine
from pm_ml.features import FeatureConfig, build_features, feature_names

CFG = FeatureConfig()


def test_columns_and_order_match_canonical(two_engines):
    X = build_features(two_engines, CFG)
    assert list(X.columns) == feature_names(CFG)
    assert len(X) == len(two_engines) and X.index.equals(two_engines.index)
    assert len(set(feature_names(CFG))) == len(feature_names(CFG))  # no duplicate names
    assert np.isfinite(X.to_numpy()).all()


def test_early_cycles_handle_insufficient_history(two_engines):
    X = build_features(two_engines, CFG)
    first = X[two_engines["cycle"] == 1].iloc[0]
    assert first["history_len"] == 1
    assert first["s_2_std_5"] == 0.0 and first["s_2_slope_10"] == 0.0
    assert first["s_2_mean_20"] == first["s_2"] == first["s_2_min_5"] == first["s_2_max_20"]
    # history_len grows to the largest window and then saturates
    e1 = X[two_engines["unit"] == 1]["history_len"]
    assert e1.iloc[:3].tolist() == [1, 2, 3] and e1.max() == CFG.max_window


def test_rolling_values_are_correct_on_known_series():
    df = make_engine(1, 12)
    df["s_2"] = np.arange(1, 13, dtype=float)  # 1..12
    X = build_features(df, CFG)
    row = X.iloc[9]  # cycle 10, last-5 window = 6..10
    assert row["s_2_mean_5"] == pytest.approx(8.0)
    assert row["s_2_min_5"] == 6 and row["s_2_max_5"] == 10
    assert row["s_2_std_5"] == pytest.approx(np.std([6, 7, 8, 9, 10]))  # population std
    assert row["s_2_slope_10"] == pytest.approx(1.0)  # perfectly linear -> slope 1 per cycle


def test_no_future_leakage(two_engines):
    """Changing readings at cycles > k must not change any feature at cycles <= k."""
    k = 15
    base = build_features(two_engines, CFG)
    mutated = two_engines.copy()
    future = (mutated["unit"] == 1) & (mutated["cycle"] > k)
    mutated.loc[future, [c for c in CFG.sensors]] += 1e6
    after = build_features(mutated, CFG)
    past = (two_engines["unit"] == 1) & (two_engines["cycle"] <= k)
    pd.testing.assert_frame_equal(base[past], after[past])
    # and the other engine is untouched entirely
    other = two_engines["unit"] == 2
    pd.testing.assert_frame_equal(base[other], after[other])


def test_engines_do_not_bleed_into_each_other():
    a, b = make_engine(1, 25), make_engine(2, 25)
    joint = build_features(pd.concat([a, b], ignore_index=True), CFG)
    alone = build_features(b, CFG)
    np.testing.assert_allclose(joint.iloc[25:].to_numpy(), alone.to_numpy())


def test_online_tail_equals_offline_last_row():
    """Serving only has to look at the last max_window readings; result must equal offline."""
    df = make_engine(1, 80)
    offline = build_features(df, CFG)
    for n in (1, 2, 7, 20, 21, 80):
        online = build_features(df.iloc[:n].tail(CFG.max_window), CFG).iloc[-1]
        pd.testing.assert_series_equal(online, offline.iloc[n - 1], check_names=False)


@pytest.mark.parametrize("breaker", ["unsorted", "duplicate", "nan", "missing_col", "empty"])
def test_invalid_input_rejected(breaker):
    df = make_engine(1, 10)
    if breaker == "unsorted":
        df = df.iloc[::-1]
    elif breaker == "duplicate":
        df = pd.concat([df, df.iloc[[3]]]).sort_values(["unit", "cycle"])
    elif breaker == "nan":
        df.loc[2, "s_2"] = np.nan
    elif breaker == "missing_col":
        df = df.drop(columns=["s_7"])
    elif breaker == "empty":
        df = df.iloc[:0]
    with pytest.raises(ValueError):
        build_features(df, CFG)
