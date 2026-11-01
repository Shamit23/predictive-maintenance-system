import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import HEADERS, StubPredictor, fill, post_reading, reading, scalar


def test_ingest_stores_reading_prediction_and_updates_engine(client, db):
    r = post_reading(client, 1, 1)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created" and body["prediction_status"] == "ok"
    p = body["prediction"]
    assert p["engine_id"] == 1 and p["cycle"] == 1 and p["model_version"] == "test-model-a"
    assert 0 <= p["predicted_rul"] <= 125 and p["risk_category"] in {"normal", "warning", "critical"}
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 1
    assert scalar(db, "SELECT count(*) FROM predictions") == 1
    assert scalar(db, "SELECT last_cycle FROM engines WHERE id=1") == 1
    fill(client, 1, 4, start=2)
    assert scalar(db, "SELECT last_cycle FROM engines WHERE id=1") == 5
    assert scalar(db, "SELECT max(history_len) FROM predictions") == 5


def test_api_key_required_for_ingestion(client, db):
    assert client.post("/api/v1/readings", json=reading()).status_code == 401
    assert post_reading(client, headers={"X-API-Key": "wrong-wrong-wrong-wrong"}).status_code == 401
    r = client.post("/api/v1/readings", json={"garbage": 1})  # auth is checked before validation
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0
    assert scalar(db, "SELECT count(*) FROM ingestion_errors") == 0  # unauthenticated noise is not recorded


@pytest.mark.parametrize("mutation", [
    {"cycle": 0}, {"engine_id": -1}, {"s_3": None}, {"s_3": "abc"}, {"s_3": 1e12}, {"extra": 1},
    {"event_time": "2024-05-01T10:00:00"},
])
def test_invalid_payloads_are_rejected_and_recorded(client, db, mutation):
    r = client.post("/api/v1/readings", json=reading(**mutation), headers=HEADERS)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["details"]
    assert "abc" not in r.text  # submitted values are not echoed back
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0
    assert scalar(db, "SELECT count(*) FROM ingestion_errors WHERE error_code='validation_error'") == 1


def test_missing_field_and_non_json_rejected(client, db):
    body = reading()
    del body["s_9"]
    assert client.post("/api/v1/readings", json=body, headers=HEADERS).status_code == 422
    r = client.post("/api/v1/readings", content="{not json", headers={**HEADERS, "Content-Type": "application/json"})
    assert r.status_code == 422
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0


def test_identical_duplicate_is_idempotent(client, db):
    first = post_reading(client, 1, 1)
    again = post_reading(client, 1, 1)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["status"] == "duplicate"
    assert again.json()["prediction"]["id"] == first.json()["prediction"]["id"]
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 1
    assert scalar(db, "SELECT count(*) FROM predictions") == 1


def test_duplicate_with_new_timestamp_is_idempotent(client, db):
    post_reading(client, 1, 1, event_time="2024-05-01T10:00:00Z")
    r = post_reading(client, 1, 1, event_time="2024-05-01T10:05:00Z")
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 1


def test_conflicting_duplicate_returns_409_and_keeps_original(client, db):
    post_reading(client, 1, 1)
    r = post_reading(client, 1, 1, s_2=12345.0)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflicting_duplicate"
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 1
    assert scalar(db, "SELECT s_2 FROM sensor_readings") != 12345.0
    assert scalar(db, "SELECT count(*) FROM ingestion_errors WHERE error_code='conflicting_duplicate'") == 1


def test_cycle_gap_returns_409_with_expected_cycle(client, db):
    r = post_reading(client, 7, 5)  # new engine must start at cycle 1
    assert r.status_code == 409
    e = r.json()["error"]
    assert e["code"] == "cycle_gap" and e["details"]["expected_cycle"] == 1
    assert scalar(db, "SELECT count(*) FROM engines") == 0  # failed attempt leaves no engine behind
    fill(client, 7, 3)
    r = post_reading(client, 7, 6)
    assert r.status_code == 409 and r.json()["error"]["details"]["expected_cycle"] == 4
    assert scalar(db, "SELECT count(*) FROM sensor_readings WHERE engine_id=7") == 3
    assert scalar(db, "SELECT count(*) FROM ingestion_errors WHERE error_code='cycle_gap'") == 2


def test_engines_are_independent(client, db):
    fill(client, 1, 3)
    fill(client, 2, 2)
    assert scalar(db, "SELECT last_cycle FROM engines WHERE id=1") == 3
    assert scalar(db, "SELECT last_cycle FROM engines WHERE id=2") == 2


def test_insufficient_history_stores_reading_but_defers_prediction(make_settings, db):
    with TestClient(create_app(make_settings(min_history_readings=3)), raise_server_exceptions=False) as c:
        r1, r2, r3 = (post_reading(c, 1, i) for i in (1, 2, 3))
        for r in (r1, r2):
            assert r.status_code == 201
            assert r.json()["prediction_status"] == "insufficient_history" and r.json()["prediction"] is None
        assert r3.json()["prediction_status"] == "ok"
        assert scalar(db, "SELECT count(*) FROM sensor_readings") == 3
        assert scalar(db, "SELECT count(*) FROM predictions") == 1


def test_prediction_failure_rolls_back_everything(client, db):
    class Boom(StubPredictor):
        def predict(self, history):
            raise RuntimeError("model exploded at C:\\secret\\path")

    client.app.state.model.predictor = Boom(50)
    r = post_reading(client, 1, 1)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    for table in ("sensor_readings", "predictions", "alerts", "engines"):
        assert scalar(db, f"SELECT count(*) FROM {table}") == 0, table  # all-or-nothing
    assert scalar(db, "SELECT count(*) FROM ingestion_errors WHERE error_code='internal_error'") == 1
    assert "secret" not in scalar(db, "SELECT message FROM ingestion_errors")


def test_alert_failure_rolls_back_reading_and_prediction(client, db, monkeypatch):
    from app.services import alerts

    def boom(*a, **k):
        raise RuntimeError("alert evaluation failed")

    monkeypatch.setattr(alerts, "evaluate_alert", boom)
    assert post_reading(client, 1, 1).status_code == 500
    for table in ("sensor_readings", "predictions", "engines"):
        assert scalar(db, f"SELECT count(*) FROM {table}") == 0, table
