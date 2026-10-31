import json
import shutil

import numpy as np
import pandas as pd
import pytest

from conftest import needs_data
from pm_ml.data import load_fd001
from pm_ml.features import FeatureConfig, build_features
from pm_ml.inference import ArtifactError, RULPredictor
from pm_ml.training import save_artifact, split_engines, train_and_evaluate


def test_engine_split_has_no_overlap_and_is_deterministic():
    units = list(range(1, 101))
    a = split_engines(units, 0.2, seed=1)
    b = split_engines(units, 0.2, seed=1)
    assert a == b
    tr, va = a
    assert len(va) == 20 and not set(tr) & set(va) and sorted(tr + va) == units
    assert split_engines(units, 0.2, seed=2) != a


@pytest.fixture(scope="module")
def trained():
    return train_and_evaluate(seed=42)


@needs_data
def test_training_is_reproducible(trained):
    again = train_and_evaluate(seed=42)
    assert again["metadata"]["metrics"] == trained["metadata"]["metrics"]
    _, test, _ = load_fd001()
    X = build_features(test.iloc[:500], FeatureConfig())
    np.testing.assert_array_equal(trained["regressor"].predict(X), again["regressor"].predict(X))


@needs_data
def test_split_metadata_is_leak_free(trained):
    m = trained["metadata"]
    assert not set(m["train_units"]) & set(m["validation_units"])
    assert len(m["train_units"]) + len(m["validation_units"]) == 100


@needs_data
def test_model_beats_baselines_on_validation(trained):
    v = trained["metadata"]["metrics"]["validation_all_cycles"]
    assert v["xgboost"]["mae"] < v["baseline_ridge"]["mae"] < v["baseline_constant"]["mae"]


@needs_data
def test_artifact_roundtrip_and_prediction_validity(trained, tmp_path):
    d = save_artifact(trained, tmp_path)
    assert {p.name for p in d.iterdir()} == {"regressor.json", "classifier.json", "metadata.json"}
    pred = RULPredictor.load(d)
    _, test, _ = load_fd001()
    cfg = FeatureConfig()
    for unit in (1, 50, 100):
        hist = test[test["unit"] == unit]
        out = pred.predict(hist)
        assert out["unit"] == unit and out["cycle"] == int(hist["cycle"].max())
        assert 0.0 <= out["predicted_rul"] <= 125.0
        assert 0.0 <= out["failure_score"] <= 1.0
        assert out["model_version"] == pred.version
        # online prediction == in-memory model on offline features (no training-serving skew)
        X = build_features(hist, cfg).iloc[[-1]]
        assert out["predicted_rul"] == pytest.approx(
            float(np.clip(trained["regressor"].predict(X)[0], 0, 125)), abs=1e-4)


@needs_data
def test_prediction_ignores_input_row_order_and_uses_only_given_history(trained, tmp_path):
    pred = RULPredictor.load(save_artifact(trained, tmp_path))
    _, test, _ = load_fd001()
    h = test[test["unit"] == 3].iloc[:60]
    shuffled = h.sample(frac=1, random_state=0)
    assert pred.predict(h)["predicted_rul"] == pred.predict(shuffled)["predicted_rul"]
    assert pred.predict(h)["cycle"] == 60  # later cycles in the file are not seen


@needs_data
def test_predictor_rejects_bad_input_and_bad_artifacts(trained, tmp_path):
    d = save_artifact(trained, tmp_path)
    pred = RULPredictor.load(d)
    _, test, _ = load_fd001()
    with pytest.raises(ValueError):
        pred.predict(test.iloc[:0])
    with pytest.raises(ValueError):  # two engines in one call
        pred.predict(test[test["unit"].isin([1, 2])])
    bad = tmp_path / "bad"
    shutil.copytree(d, bad)
    meta = json.loads((bad / "metadata.json").read_text())
    meta["feature_names"] = list(reversed(meta["feature_names"]))
    (bad / "metadata.json").write_text(json.dumps(meta))
    with pytest.raises(ArtifactError):
        RULPredictor.load(bad)
    (bad / "regressor.json").unlink()
    with pytest.raises(ArtifactError):
        RULPredictor.load(bad)
