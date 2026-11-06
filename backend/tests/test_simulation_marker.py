"""Simulated-data marker (X-Simulation-Run) and the immutable engine origin.

Runs are inserted with SQL through the guarded `db` fixture, so these tests do not depend on the control routes.
"""
import pytest
from sqlalchemy import text

from conftest import CONTROL_HEADERS, HEADERS, post_reading, reading, scalar


def make_run(db, status="running", units=(1, 2), scenario="test", offset=0) -> int:
    import json

    with db.begin() as c:
        return c.execute(text(
            "INSERT INTO simulation_runs (status, scenario, engine_units, replay_slot, id_offset, interval_seconds) "
            "VALUES (:s, :sc, CAST(:u AS jsonb), 0, :o, 1.0) RETURNING id"),
            {"s": status, "sc": scenario, "u": json.dumps(list(units)), "o": offset}).scalar()


def sim_post(client, run_id, engine_id=1, cycle=1, headers=HEADERS, **over):
    h = {**headers, "X-Simulation-Run": str(run_id)}
    return client.post("/api/v1/readings", json=reading(engine_id, cycle, **over), headers=h)


def origin(db, engine_id):
    return scalar(db, "SELECT origin FROM engines WHERE id=:e", e=engine_id)


def test_ordinary_ingestion_is_unchanged_and_unmarked(client, db):
    r = post_reading(client, 1, 1)
    assert r.status_code == 201
    assert origin(db, 1) == "api" and scalar(db, "SELECT simulation_run_id FROM sensor_readings") is None
    assert client.get("/api/v1/engines/1").json()["origin"] == "api"


def test_valid_marker_creates_a_simulation_engine_and_links_the_reading(client, db):
    run = make_run(db, units=(1, 2))
    r = sim_post(client, run, engine_id=2, cycle=1)
    assert r.status_code == 201 and r.json()["prediction_status"] == "ok"
    assert origin(db, 2) == "simulation"
    assert scalar(db, "SELECT simulation_run_id FROM sensor_readings WHERE engine_id=2") == run
    assert client.get("/api/v1/engines/2").json()["origin"] == "simulation"
    assert {e["id"]: e["origin"] for e in client.get("/api/v1/engines").json()["items"]} == {2: "simulation"}
    assert client.get("/api/v1/engines/2/readings").json()["items"][0]["simulation_run_id"] == run


def test_marker_uses_the_runs_id_offset(client, db):
    run = make_run(db, units=(5,), scenario="train", offset=1000)
    assert sim_post(client, run, engine_id=1005, cycle=1).status_code == 201
    assert sim_post(client, run, engine_id=5, cycle=1).status_code == 409  # un-offset id is not part of this run


@pytest.mark.parametrize("status", ["queued", "stopped", "completed", "failed"])
def test_marker_is_rejected_unless_the_run_is_running_or_stopping(client, db, status):
    run = make_run(db, status=status)
    r = sim_post(client, run)
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_run_not_active"
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0
    assert scalar(db, "SELECT count(*) FROM engines") == 0  # nothing created by a rejected marker


def test_stopping_run_still_accepts_in_flight_readings(client, db):
    run = make_run(db, status="stopping")
    assert sim_post(client, run).status_code == 201


def test_unknown_run_and_bad_header_values(client, db):
    assert sim_post(client, 99999).json()["error"]["code"] == "simulation_run_not_active"
    for bad in ("abc", "0", "-3", "1.5"):
        r = sim_post(client, bad)
        assert r.status_code == 422, bad
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == 0


def test_engine_outside_the_run_is_rejected(client, db):
    run = make_run(db, units=(1,))
    r = sim_post(client, run, engine_id=7)
    assert r.status_code == 409 and r.json()["error"]["code"] == "engine_not_in_run"
    assert scalar(db, "SELECT count(*) FROM engines") == 0


def test_origin_is_immutable_ordinary_engine_cannot_receive_simulated_data(client, db):
    assert post_reading(client, 1, 1).status_code == 201  # engine 1 is 'api'
    run = make_run(db, units=(1,))
    r = sim_post(client, run, engine_id=1, cycle=2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "engine_origin_mismatch"
    assert origin(db, 1) == "api" and scalar(db, "SELECT count(*) FROM sensor_readings") == 1


def test_origin_is_immutable_simulated_engine_cannot_receive_ordinary_data(client, db):
    run = make_run(db, units=(1,))
    assert sim_post(client, run, engine_id=1, cycle=1).status_code == 201
    r = post_reading(client, 1, 2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "engine_origin_mismatch"
    # even an identical duplicate of a stored simulated reading is not accepted without the marker
    dup = post_reading(client, 1, 1)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "engine_origin_mismatch"
    assert origin(db, 1) == "simulation" and scalar(db, "SELECT count(*) FROM sensor_readings") == 1


def test_a_later_run_can_continue_a_simulated_engine(client, db):
    r1 = make_run(db, units=(1,))
    assert sim_post(client, r1, cycle=1).status_code == 201
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET status='stopped'"))
    r2 = make_run(db, units=(1,))
    assert sim_post(client, r2, cycle=2).status_code == 201
    assert sim_post(client, r2, cycle=2).status_code == 200  # identical duplicate stays idempotent
    with db.connect() as c:
        runs = [r[0] for r in c.execute(text("SELECT simulation_run_id FROM sensor_readings ORDER BY cycle"))]
    assert runs == [r1, r2]


def test_marker_does_not_replace_authentication(client, db):
    run = make_run(db)
    assert client.post("/api/v1/readings", json=reading(1, 1), headers={"X-Simulation-Run": str(run)}).status_code == 401
    # the operator control key cannot ingest, with or without a marker
    r = sim_post(client, run, headers=CONTROL_HEADERS)
    assert r.status_code == 401 and scalar(db, "SELECT count(*) FROM sensor_readings") == 0


def test_rejected_markers_are_recorded_as_ingestion_errors(client, db):
    run = make_run(db, status="stopped")
    sim_post(client, run)
    assert scalar(db, "SELECT count(*) FROM ingestion_errors WHERE error_code='simulation_run_not_active'") == 1
