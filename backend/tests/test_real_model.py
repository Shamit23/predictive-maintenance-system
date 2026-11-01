"""Training-serving parity through the real API, using the real trained artifact and FD001 test data.

Skipped (with a reason) when the dataset or a trained artifact is not available locally.
"""
import pytest
from fastapi.testclient import TestClient
from pm_ml.data import DEFAULT_RAW_DIR, load_fd001
from pm_ml.inference import RULPredictor

from app.core.config import REPO_ROOT
from app.main import create_app
from conftest import fill_from_frame

ART_ROOT = REPO_ROOT / "ml" / "artifacts"
ARTS = sorted(p for p in ART_ROOT.glob("*") if (p / "metadata.json").is_file()) if ART_ROOT.is_dir() else []

pytestmark = pytest.mark.skipif(
    not (DEFAULT_RAW_DIR / "test_FD001.txt").is_file() or len(ARTS) != 1,
    reason="needs FD001 data in ml/data/raw and exactly one trained artifact in ml/artifacts",
)


def test_api_prediction_equals_offline_predictor_on_real_data(make_settings, db):
    _, test_df, _ = load_fd001()
    lengths = test_df.groupby("unit").size()
    unit = int(lengths[lengths >= 60].index[0])  # an engine with at least 60 observed cycles
    hist = test_df[test_df["unit"] == unit].iloc[:60]
    settings = make_settings(model_artifact_root=ART_ROOT)
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        last = fill_from_frame(c, hist)
        expected = RULPredictor.load(ARTS[0]).predict(hist)
        got = last["prediction"]
        assert got["model_version"] == expected["model_version"]
        assert got["predicted_rul"] == pytest.approx(expected["predicted_rul"], abs=1e-4)
        assert got["failure_score"] == pytest.approx(expected["failure_score"], abs=1e-6)
        assert got["cycle"] == expected["cycle"] == 60
        assert c.post("/api/v1/predict", json={"engine_id": unit}).json()["predicted_rul"] == pytest.approx(
            expected["predicted_rul"], abs=1e-4)
