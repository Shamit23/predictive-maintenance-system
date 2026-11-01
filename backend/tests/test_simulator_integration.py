"""Simulator -> ingestion API -> PostgreSQL -> features -> prediction -> alerts -> read API.

Database safety: this module uses ONLY the shared `db` / `client` / `make_settings` fixtures, which are built on the
session `db_url` guard in conftest.py (database name must end with '_test' and differ from DATABASE_URL, else the run
aborts). It never creates a database URL or engine of its own, and the autouse fixture below re-asserts the target.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pm_ml.data import DEFAULT_RAW_DIR, load_fd001
from pm_ml.inference import RULPredictor
from sqlalchemy import text

from app.core.config import REPO_ROOT
from app.main import create_app
from conftest import API_KEY, VALUE_COLS, StubPredictor, scalar

sim = pytest.importorskip("pm_simulator.replay", reason="install the simulator first: pip install -e simulator")

ART_ROOT = REPO_ROOT / "ml" / "artifacts"
ARTS = sorted(p for p in ART_ROOT.glob("*") if (p / "metadata.json").is_file()) if ART_ROOT.is_dir() else []


@pytest.fixture(autouse=True)
def only_test_database(db):
    assert (db.url.database or "").endswith("_test"), "integration tests must run against a *_test database"


def frame(unit: int, n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + unit)
    d = {"unit": unit, "cycle": np.arange(1, n + 1)}
    for c in VALUE_COLS:
        d[c] = np.round(rng.normal(size=n), 6)
    return pd.DataFrame(d)


def source(unit=3, n=8, offset=0, scenario="test", ref=None, seed=0):
    return sim.EngineSource.from_frame(scenario, unit, unit + offset, frame(unit, n, seed), ref)


def api(client):
    """Simulator client that talks to the in-process FastAPI app (a TestClient is an httpx.Client)."""
    return sim.ApiClient(client, API_KEY, sleep=lambda _s: None)


def replay(client, plan, **kw):
    kw.setdefault("interval", 0)
    return sim.run_replay(plan, api(client), **kw)


def stored_values(db, engine_id):
    cols = ", ".join(VALUE_COLS)
    with db.connect() as c:
        rows = c.execute(text(f"SELECT cycle, {cols} FROM sensor_readings WHERE engine_id=:e ORDER BY cycle"),
                         {"e": engine_id}).all()
    return {r[0]: dict(zip(VALUE_COLS, r[1:])) for r in rows}


def test_end_to_end_replay_persists_exact_values_predictions_and_event_time(client, db):
    src = source(n=8)
    before = datetime.now(timezone.utc)
    events = []
    res = replay(client, [src], on_event=events.append)
    after = datetime.now(timezone.utc)

    assert res.engines[3].complete and res.engines[3].created == 8 and not res.interrupted
    # source values reached PostgreSQL bit-for-bit, in cycle order
    stored = stored_values(db, 3)
    assert list(stored) == list(range(1, 9))
    assert all(stored[c] == src.values[c - 1] for c in stored)
    # one prediction per reading, from the registered model, matching what the simulator saw
    assert scalar(db, "SELECT count(*) FROM predictions") == 8
    hist = client.get("/api/v1/engines/3/predictions", params={"order": "asc"}).json()["items"]
    assert [p["cycle"] for p in hist] == list(range(1, 9)) and {p["model_version"] for p in hist} == {"test-model-a"}
    assert [e.prediction["predicted_rul"] for e in events] == [p["predicted_rul"] for p in hist]
    assert client.get("/api/v1/engines/3").json()["last_cycle"] == 8
    # event_time is the UTC send time only
    with db.connect() as c:
        times = [r[0] for r in c.execute(text("SELECT event_time FROM sensor_readings WHERE engine_id=3"))]
    assert all(before <= t <= after for t in times)


def test_alerts_flow_from_replay_to_the_alert_api(client, db):
    client.app.state.model.predictor = StubPredictor(20)  # deterministic RUL -> critical
    events = []
    res = replay(client, [source(n=4)], on_event=events.append)
    assert [e.alert_action for e in events] == ["opened", "none", "none", "none"]
    assert events[0].active_alert["severity"] == "critical"
    assert [(a["severity"], a["status"]) for a in res.engines[3].unresolved_alerts] == [("critical", "open")]
    assert client.get("/api/v1/alerts", params={"engine_id": 3}).json()["total"] == 1
    assert scalar(db, "SELECT count(*) FROM alerts") == 1


def test_rerun_resume_and_idempotency_against_the_real_backend(client, db):
    src = source(n=8)
    res = replay(client, [src], max_cycles=3)
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 3 and not res.engines[3].complete
    res = replay(client, [src])
    assert res.engines[3].resumed_after == 3 and res.engines[3].created == 5 and res.engines[3].complete
    res = replay(client, [src])  # fully replayed already: handshake only
    assert res.engines[3].created == 0 and res.engines[3].complete
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 8
    assert scalar(db, "SELECT count(*) FROM predictions") == 8
    assert scalar(db, "SELECT count(*) FROM ingestion_errors") == 0  # no spurious rejected attempts


def test_replay_is_deterministic_for_different_engine_ids(client, db):
    replay(client, [source(offset=0), source(offset=5000)])
    ruls = lambda eid: [p["predicted_rul"] for p in client.get(  # noqa: E731
        f"/api/v1/engines/{eid}/predictions", params={"order": "asc"}).json()["items"]]
    assert ruls(3) == ruls(5003) and len(ruls(3)) == 8


def test_id_collision_with_other_data_is_stopped_by_the_handshake(client, db):
    replay(client, [source(seed=0)], max_cycles=3)
    with pytest.raises(sim.ConflictError, match="--id-offset"):
        replay(client, [source(seed=99)])  # same engine id, different values
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 3  # nothing overwritten or added


def test_simulator_gap_contract_matches_the_real_cycle_gap_response(client, db):
    """The simulator's gap handling depends on `details.expected_cycle` (int); verify the real API provides it."""
    src = source(n=8)
    with pytest.raises(sim._Gap) as ei:
        sim._send(api(client), src, 5, lambda: datetime.now(timezone.utc))  # new engine must start at cycle 1
    assert ei.value.details["expected_cycle"] == 1 and isinstance(ei.value.details["expected_cycle"], int)


def test_wrong_api_key_is_rejected_and_nothing_is_written(client, db):
    bad = sim.ApiClient(client, "wrong-key-wrong-key-wrong", sleep=lambda _s: None)
    with pytest.raises(sim.AuthError) as ei:
        sim.run_replay([source()], bad, interval=0)
    assert "wrong-key" not in str(ei.value)
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0


@pytest.mark.skipif(
    not (DEFAULT_RAW_DIR / "test_FD001.txt").is_file() or len(ARTS) != 1,
    reason="needs FD001 data in ml/data/raw and exactly one trained artifact in ml/artifacts",
)
def test_real_fd001_replay_matches_the_offline_predictor(make_settings, db):
    """Real model + real FD001 test engine 24 (186 cycles, true RUL after the last cycle = 20)."""
    (src,) = sim.build_plan("test", [24])
    _, test_df, rul = load_fd001()
    hist = test_df[test_df["unit"] == 24]
    with TestClient(create_app(make_settings(model_artifact_root=ART_ROOT)), raise_server_exceptions=False) as c:
        events = []
        res = replay(c, [src], on_event=events.append)
        assert res.engines[24].complete and res.engines[24].created == 186 == len(hist)
        assert stored_values(db, 24)[186] == src.values[185]
        expected = RULPredictor.load(ARTS[0]).predict(hist)
        last = events[-1].prediction
        assert last["predicted_rul"] == pytest.approx(expected["predicted_rul"], abs=1e-4)
        assert last["model_version"] == expected["model_version"] and events[-1].cycle == 186
        assert src.reference_rul == int(rul[24]) == 20  # evaluation-only reference, never sent
        assert all(e.true_rul is None for e in events)  # test scenario: no per-cycle ground truth
        assert "EVALUATION ONLY" in sim.format_summary(res)
