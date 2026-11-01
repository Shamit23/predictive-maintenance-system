"""Read endpoints, dry-run prediction, model metadata, health/readiness and failure behaviour."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app
from conftest import API_KEY, StubPredictor, fill, post_reading, scalar


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "migrations": "ok", "model": "ok"}}


def test_ready_reports_database_outage_without_leaking_details(client):
    bad = create_engine("postgresql+psycopg://pm_app:TOPSECRET@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    from app.db.session import make_session_factory
    client.app.state.session_factory = make_session_factory(bad)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert r.json()["checks"]["database"] == "unavailable"
    assert "TOPSECRET" not in r.text and "127.0.0.1" not in r.text
    assert client.get("/health").status_code == 200  # liveness unaffected


def test_api_returns_503_envelope_when_database_down(client):
    bad = create_engine("postgresql+psycopg://pm_app:TOPSECRET@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    from app.db.session import make_session_factory
    client.app.state.session_factory = make_session_factory(bad)
    r = client.get("/api/v1/engines")
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    assert "TOPSECRET" not in r.text and "psycopg" not in r.text


def test_ready_flags_missing_model(client):
    client.app.state.model = None
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["model"] == "unavailable"
    assert post_reading(client).status_code == 503  # ingestion refuses rather than guessing


def test_startup_fails_fast_without_unambiguous_model(make_settings, tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    with pytest.raises(Exception, match="No model artifact"):
        with TestClient(create_app(make_settings(model_artifact_root=empty))):
            pass


def test_engine_list_detail_and_filters(client, db):
    fill(client, 1, 3)
    client.app.state.model.predictor = StubPredictor(20)
    fill(client, 12, 2)
    client.app.state.model.predictor = StubPredictor(150)
    fill(client, 2, 2)
    page = client.get("/api/v1/engines").json()
    assert page["total"] == 3 and [e["id"] for e in page["items"]] == [1, 2, 12]
    by_id = {e["id"]: e for e in page["items"]}
    assert by_id[12]["risk_category"] == "critical" and by_id[12]["alert_severity"] == "critical"
    assert by_id[2]["risk_category"] == "normal" and by_id[2]["alert_status"] is None
    assert by_id[1]["last_cycle"] == 3 and by_id[1]["latest_prediction"]["cycle"] == 3
    get = lambda **p: client.get("/api/v1/engines", params=p).json()  # noqa: E731
    assert [e["id"] for e in get(search="1")["items"]] == [1, 12]
    assert [e["id"] for e in get(risk="critical")["items"]] == [12]
    no_alert = [e["id"] for e in get(alert_status="none")["items"]]
    assert 2 in no_alert and 12 not in no_alert  # engine 1 uses the synthetic model; its risk is arbitrary
    assert [e["id"] for e in get(alert_status="open")["items"]] == [e["id"] for e in page["items"] if e["alert_status"] == "open"]
    assert get(limit=1)["total"] == 3 and len(get(limit=1)["items"]) == 1
    assert [e["id"] for e in get(limit=1, offset=1)["items"]] == [2]
    assert get(search="%")["total"] == 0  # LIKE wildcards are escaped
    d = client.get("/api/v1/engines/12").json()
    assert d["reading_count"] == 2 and d["prediction_count"] == 2 and d["created_at"]
    assert client.get("/api/v1/engines/999").status_code == 404
    assert client.get("/api/v1/engines/999").json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/engines", params={"limit": 1000}).status_code == 422
    assert client.get("/api/v1/engines", params={"risk": "bogus"}).status_code == 422


def test_readings_and_predictions_history(client, db):
    fill(client, 1, 6)
    r = client.get("/api/v1/engines/1/readings", params={"order": "asc"}).json()
    assert r["total"] == 6 and [x["cycle"] for x in r["items"]] == [1, 2, 3, 4, 5, 6]
    assert {"s_2", "setting_1", "event_time", "ingested_at"} <= set(r["items"][0])
    r = client.get("/api/v1/engines/1/readings", params={"since_cycle": 5}).json()
    assert [x["cycle"] for x in r["items"]] == [6, 5]  # default order is newest first
    p = client.get("/api/v1/engines/1/predictions", params={"order": "asc", "limit": 3}).json()
    assert p["total"] == 6 and [x["cycle"] for x in p["items"]] == [1, 2, 3]
    assert all(x["model_version"] == "test-model-a" for x in p["items"])
    assert client.get("/api/v1/engines/9/readings").status_code == 404
    assert client.get("/api/v1/engines/9/predictions").status_code == 404


def test_dry_run_predict_matches_stored_prediction_and_persists_nothing(client, db):
    fill(client, 1, 5)
    stored = client.get("/api/v1/engines/1/predictions", params={"limit": 1}).json()["items"][0]
    before = scalar(db, "SELECT count(*) FROM predictions")
    r = client.post("/api/v1/predict", json={"engine_id": 1})
    assert r.status_code == 200
    b = r.json()
    assert b["cycle"] == 5 and b["persisted"] is False and b["model_version"] == "test-model-a"
    assert b["predicted_rul"] == pytest.approx(stored["predicted_rul"], abs=1e-6)  # same code path, same answer
    assert b["history_len"] == 5 and b["predicted_at"]
    earlier = client.post("/api/v1/predict", json={"engine_id": 1, "cycle": 2}).json()
    assert earlier["cycle"] == 2 and earlier["history_len"] == 2  # uses only readings up to that cycle
    assert scalar(db, "SELECT count(*) FROM predictions") == before


def test_predict_errors(client, db, make_settings):
    assert client.post("/api/v1/predict", json={"engine_id": 5}).status_code == 404
    fill(client, 1, 2)
    assert client.post("/api/v1/predict", json={"engine_id": 1, "cycle": 3}).status_code == 404  # no such reading
    assert client.post("/api/v1/predict", json={"engine_id": 0}).status_code == 422
    assert client.post("/api/v1/predict", json={"engine_id": 1, "x": 1}).status_code == 422
    with TestClient(create_app(make_settings(min_history_readings=4)), raise_server_exceptions=False) as c:
        r = c.post("/api/v1/predict", json={"engine_id": 1})
        assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_history"
        assert r.json()["error"]["details"] == {"engine_id": 1, "available": 2, "required": 4}


def test_models_endpoints_expose_metadata_but_no_paths_or_secrets(client, db, artifact_root):
    active = client.get("/api/v1/models/active")
    assert active.status_code == 200
    m = active.json()
    assert m["version"] == "test-model-a" and m["status"] == "active" and m["model_type"] == "xgboost"
    assert m["rul_cap"] == 125 and m["failure_horizon"] == 30 and m["trained_at"] and m["metrics"]
    listing = client.get("/api/v1/models").json()
    assert listing["total"] == 1 and listing["items"][0]["version"] == "test-model-a"
    text_all = active.text + client.get("/api/v1/models").text
    for needle in (str(artifact_root), "artifacts", "directory_name", API_KEY, "postgresql"):
        assert needle not in text_all


def test_cors_allows_only_configured_origin(client):
    ok = client.options("/api/v1/engines", headers={"Origin": "http://localhost:5173",
                                                    "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/engines", headers={"Origin": "http://evil.example",
                                                     "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_error"
