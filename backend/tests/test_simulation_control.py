"""Simulation control plane: operator actions, worker protocol, leases, expiry. PostgreSQL via the guarded fixtures.

Time-based behaviour is driven by shifting timestamps in `simulation_runs` through the guarded `db` fixture
(the server uses the database clock), so no test sleeps.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.services import simulation as sim
from conftest import API_KEY, CONTROL_HEADERS, CONTROL_KEY, HEADERS, post_reading, reading, scalar

W1, W2 = "worker-aaaaaaaa", "worker-bbbbbbbb"
BASE = "/api/v1/simulation"


# ---- helpers ------------------------------------------------------------------------------------
def start(client, headers=CONTROL_HEADERS, **over):
    body = {"scenario": "test", "engines": [1], "interval_seconds": 1.0}
    body.update(over)
    return client.post(f"{BASE}/runs", json=body, headers=headers)


def claim(client, wid=W1, headers=HEADERS):
    return client.post(f"{BASE}/worker/claim", json={"worker_id": wid}, headers=headers)


def beat(client, run_id, wid=W1, headers=HEADERS, **kw):
    return client.post(f"{BASE}/runs/{run_id}/heartbeat", json={"worker_id": wid, **kw}, headers=headers)


def finish(client, run_id, outcome, wid=W1, headers=HEADERS, **kw):
    return client.post(f"{BASE}/runs/{run_id}/finish", json={"worker_id": wid, "outcome": outcome, **kw},
                       headers=headers)


def stop(client, run_id, headers=CONTROL_HEADERS):
    return client.post(f"{BASE}/runs/{run_id}/stop", headers=headers)


def status(client):
    return client.get(BASE).json()


def sql(db, statement, **params):
    with db.begin() as c:
        c.execute(text(statement), params)


def running(client, wid=W1, **over) -> int:
    rid = start(client, **over).json()["id"]
    assert claim(client, wid).json()["run"]["run_id"] == rid
    return rid


# ---- authentication: two separate keys -----------------------------------------------------------
def test_operator_actions_need_the_control_key_not_the_api_key(client, db):
    assert start(client, headers={}).status_code == 401
    assert start(client, headers=HEADERS).status_code == 401                       # ingestion key is not enough
    assert start(client, headers={"X-Control-Key": "wrong-wrong-wrong-wrong"}).status_code == 401
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 0
    rid = start(client).json()["id"]
    for call in (lambda h: stop(client, rid, h),
                 lambda h: client.patch(f"{BASE}/runs/{rid}", json={"interval_seconds": 2}, headers=h)):
        assert call({}).status_code == 401 and call(HEADERS).status_code == 401
    assert status(client)["active_run"]["status"] == "queued"


def test_worker_protocol_needs_the_api_key_not_the_control_key(client, db):
    rid = start(client).json()["id"]
    for r in (claim(client, headers=CONTROL_HEADERS), claim(client, headers={}),
              beat(client, rid, headers=CONTROL_HEADERS), finish(client, rid, "completed", headers=CONTROL_HEADERS)):
        assert r.status_code == 401
    assert claim(client).status_code == 200


def test_operator_controls_fail_closed_when_no_control_key_is_configured(make_settings, db):
    with TestClient(create_app(make_settings(control_api_key=None)), raise_server_exceptions=False) as c:
        r = start(c)
        assert r.status_code == 503 and r.json()["error"]["code"] == "control_disabled"
        assert start(c, headers=HEADERS).status_code == 503  # nothing can be used in place of the missing key
        assert c.get(BASE).status_code == 200                # reads and the worker protocol still work
        assert claim(c).status_code == 200


def test_no_secret_ever_appears_in_responses(client, db):
    rid = start(client).json()["id"]
    texts = [start(client, headers={}).text, start(client).text, claim(client).text, beat(client, rid).text,
             client.get(BASE).text, client.get(f"{BASE}/runs").text, stop(client, rid).text,
             client.post(f"{BASE}/runs", json={"bad": 1}, headers=CONTROL_HEADERS).text]
    assert not any(API_KEY in t or CONTROL_KEY in t for t in texts)


# ---- start / validation / one active run -----------------------------------------------------------
def test_start_returns_queued_and_never_claims_to_be_running(client, db):
    r = start(client, engines=[1, 2])
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "queued" and run["lease_active"] is False and run["started_at"] is None
    assert run["engine_ids"] == [1, 2] and run["readings_stored"] == 0
    st = status(client)
    assert st["active_run"]["status"] == "queued" and st["worker_online"] is False and st["workers_online"] == 0


@pytest.mark.parametrize("scenario,slot,ids", [("test", 0, [1, 24]), ("train", 0, [1001, 1024]),
                                                ("test", 2, [20001, 20024]), ("train", 9, [91001, 91024])])
def test_server_derives_engine_ids_from_scenario_and_slot(client, db, scenario, slot, ids):
    run = start(client, scenario=scenario, engines=[1, 24], replay_slot=slot).json()
    assert run["engine_ids"] == ids and run["engine_units"] == [1, 24]


@pytest.mark.parametrize("over", [
    {"scenario": "prod"}, {"engines": []}, {"engines": [0]}, {"engines": [101]}, {"engines": [1, 1]},
    {"engines": ["1"]}, {"engines": "1"}, {"interval_seconds": 0}, {"interval_seconds": -1},
    {"max_cycles": 0}, {"max_cycles": 10_000_000}, {"replay_slot": 10}, {"replay_slot": -1},
    {"id_offset": 5}, {"engine_ids": [5000]}, {"worker_id": "x"},
])
def test_invalid_requests_are_rejected_and_nothing_is_created(client, db, over):
    r = start(client, **over)
    assert r.status_code == 422, r.text
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 0


def test_server_enforced_limits(client, db):
    r = start(client, engines=list(range(1, 12)))  # default max 10
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_engines"
    for iv in (0.05, 61):
        r = start(client, interval_seconds=iv)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_interval"
    assert start(client, engines=list(range(1, 11)), interval_seconds=0.1, max_cycles=5).status_code == 201


def test_only_one_run_can_be_active(client, db):
    first = start(client).json()
    r = start(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_already_active"
    assert r.json()["error"]["details"]["active_run_id"] == first["id"]
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 1
    assert stop(client, first["id"]).json()["status"] == "stopped"
    assert start(client).status_code == 201


def test_concurrent_starts_create_exactly_one_run(client, db):
    codes, lock = [], threading.Lock()

    def go():
        with client.app.state.session_factory() as s:
            try:
                from app.schemas.api import RunCreate
                sim.create_run(s, client.app.state.settings, RunCreate(scenario="test", engines=[1]))
                code = 201
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "status", 500)
        with lock:
            codes.append(code)

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [201, 409, 409, 409, 409, 409]
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 1


# ---- stop / speed --------------------------------------------------------------------------------
def test_stop_queued_run_is_immediate_and_repeat_is_rejected(client, db):
    rid = start(client).json()["id"]
    r = stop(client, rid)
    assert r.status_code == 200 and r.json()["status"] == "stopped" and r.json()["ended_at"]
    again = stop(client, rid)
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_not_active"
    assert stop(client, 9999).status_code == 404


def test_stopping_a_running_run_waits_for_the_worker_to_confirm(client, db):
    rid = running(client)
    r = stop(client, rid)
    assert r.json()["status"] == "stopping" and r.json()["stop_requested_at"]   # NOT 'stopped' yet
    assert stop(client, rid).json()["status"] == "stopping"                     # idempotent
    assert beat(client, rid).json()["status"] == "stopping"                     # worker learns it on its heartbeat
    done = finish(client, rid, "stopped")
    assert done.json()["status"] == "stopped" and done.json()["lease_active"] is False
    assert scalar(db, "SELECT lease_owner FROM simulation_runs") is None


def test_stop_with_a_dead_worker_does_not_wait_forever(client, db):
    rid = running(client)
    sql(db, "UPDATE simulation_runs SET lease_expires_at = now() - interval '5 seconds'")
    assert stop(client, rid).json()["status"] == "stopped"
    assert beat(client, rid).status_code == 409  # the late worker is told to stop


def test_stopping_run_whose_worker_disappears_is_finalised_on_next_read(client, db):
    rid = running(client)
    stop(client, rid)
    assert status(client)["active_run"]["status"] == "stopping"
    sql(db, "UPDATE simulation_runs SET lease_expires_at = now() - interval '1 second'")
    st = status(client)
    assert st["active_run"] is None and st["last_run"]["id"] == rid and st["last_run"]["status"] == "stopped"


def test_speed_change_reaches_the_worker_through_claim_and_heartbeat(client, db):
    rid = start(client, interval_seconds=1.0).json()["id"]
    r = client.patch(f"{BASE}/runs/{rid}", json={"interval_seconds": 0.5}, headers=CONTROL_HEADERS)
    assert r.status_code == 200 and r.json()["interval_seconds"] == 0.5
    assert claim(client).json()["run"]["interval_seconds"] == 0.5
    client.patch(f"{BASE}/runs/{rid}", json={"interval_seconds": 0.2}, headers=CONTROL_HEADERS)
    assert beat(client, rid).json() == {"status": "running", "interval_seconds": 0.2}


def test_speed_change_validation_and_state_rules(client, db):
    rid = start(client).json()["id"]
    patch = lambda body, h=CONTROL_HEADERS, i=None: client.patch(  # noqa: E731
        f"{BASE}/runs/{i or rid}", json=body, headers=h)
    assert patch({"interval_seconds": 0.01}).status_code == 422
    assert patch({"interval_seconds": 500}).status_code == 422
    assert patch({"interval_seconds": 2, "extra": 1}).status_code == 422
    assert patch({"interval_seconds": 2}, h={}).status_code == 401
    assert patch({"interval_seconds": 2}, i=9999).status_code == 404
    stop(client, rid)
    r = patch({"interval_seconds": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_not_active"


# ---- worker claim: atomic, leased, never 'running' without a worker -----------------------------------
def test_claim_with_no_work_returns_null_and_records_the_worker_as_online(client, db):
    assert claim(client).json() == {"run": None}
    st = status(client)
    assert st["worker_online"] is True and st["workers_online"] == 1 and st["active_run"] is None
    sql(db, "UPDATE simulation_workers SET last_seen_at = now() - interval '5 minutes'")
    assert status(client)["worker_online"] is False  # presence expires with the lease window


def test_claim_turns_queued_into_running_with_a_lease(client, db):
    rid = start(client, scenario="train", engines=[3, 4], interval_seconds=2.0, max_cycles=7, replay_slot=1).json()["id"]
    plan = claim(client).json()["run"]
    assert plan == {"run_id": rid, "status": "running", "scenario": "train", "engine_units": [3, 4],
                    "id_offset": 11000, "interval_seconds": 2.0, "max_cycles": 7}
    run = status(client)["active_run"]
    assert run["status"] == "running" and run["lease_active"] is True and run["started_at"] and run["last_heartbeat_at"]


def test_second_worker_gets_nothing_while_the_lease_is_valid_and_owner_can_renew(client, db):
    rid = running(client, W1)
    assert claim(client, W2).json() == {"run": None}
    assert claim(client, W1).json()["run"]["run_id"] == rid  # same worker: renew
    assert status(client)["workers_online"] == 2


def test_claim_validates_worker_id(client, db):
    for bad in ("short", "has space in it", "x" * 65, "semi;colon-id"):
        assert claim(client, bad).status_code == 422


def test_concurrent_claims_give_the_run_to_exactly_one_worker(client, db):
    rid = start(client).json()["id"]
    results, errors, barrier = [], [], threading.Barrier(8)

    def go(i):
        try:
            barrier.wait()
            with client.app.state.session_factory() as s:
                results.append((f"worker-{i:08d}", sim.claim(s, client.app.state.settings, f"worker-{i:08d}")))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    winners = [w for w, plan in results if plan is not None]
    assert len(winners) == 1
    assert scalar(db, "SELECT lease_owner FROM simulation_runs WHERE id=:i", i=rid) == winners[0]


# ---- heartbeat ------------------------------------------------------------------------------------
def test_heartbeat_stores_progress_renews_the_lease_and_counts_come_from_the_database(client, db):
    rid = running(client)
    sql(db, "UPDATE simulation_runs SET lease_expires_at = now() + interval '1 second'")
    r = beat(client, rid, progress={"1": {"last_cycle": 5}}, readings_created=5, readings_duplicate=1, error_count=2,
             last_error="HTTP 503\x00\x1b[31m")
    assert r.status_code == 200 and r.json() == {"status": "running", "interval_seconds": 1.0}
    run = client.get(f"{BASE}/runs/{rid}").json()
    assert run["progress"] == {"1": {"last_cycle": 5}} and run["readings_created"] == 5
    assert run["error_count"] == 2 and "\x00" not in run["last_error"] and "\x1b" not in run["last_error"]
    assert run["readings_stored"] == 0  # authoritative DB count, independent of what the worker claims
    assert scalar(db, "SELECT lease_expires_at > now() + interval '10 seconds' FROM simulation_runs") is True


@pytest.mark.parametrize("body", [{"readings_created": -1}, {"error_count": -5}, {"extra": 1},
                                   {"last_error": "x" * 501}, {"progress": {"k": "v" * 25_000}}])
def test_heartbeat_payload_validation(client, db, body):
    rid = running(client)
    assert beat(client, rid, **body).status_code == 422


def test_heartbeat_ownership_and_state_errors(client, db):
    rid = running(client, W1)
    r = beat(client, rid, W2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "lease_lost"
    assert beat(client, 9999).status_code == 404
    finish(client, rid, "completed")
    r = beat(client, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_not_active"


def test_a_restarted_worker_takes_over_an_expired_lease_and_the_old_one_is_fenced_out(client, db):
    rid = running(client, W1)
    sql(db, "UPDATE simulation_runs SET lease_expires_at = now() - interval '5 seconds'")
    plan = claim(client, W2).json()["run"]
    assert plan["run_id"] == rid and plan["status"] == "running"
    assert scalar(db, "SELECT lease_owner FROM simulation_runs") == W2
    assert beat(client, rid, W1).json()["error"]["code"] == "lease_lost"
    assert beat(client, rid, W2).status_code == 200


def test_the_same_worker_can_resume_after_an_outage_shorter_than_the_stale_timeout(client, db):
    rid = running(client, W1)
    sql(db, "UPDATE simulation_runs SET lease_expires_at = now() - interval '60 seconds', "
            "last_heartbeat_at = now() - interval '60 seconds'")  # lease lapsed, but < 120 s stale timeout
    assert status(client)["active_run"]["status"] == "running"
    assert status(client)["active_run"]["lease_active"] is False  # honest: no live lease
    assert beat(client, rid, W1).status_code == 200


# ---- lazy expiry ------------------------------------------------------------------------------------
def test_queued_run_without_a_worker_fails_with_no_worker(client, db):
    rid = start(client).json()["id"]
    sql(db, "UPDATE simulation_runs SET created_at = now() - interval '61 seconds'")
    st = status(client)
    assert st["active_run"] is None
    assert st["last_run"]["id"] == rid and st["last_run"]["status"] == "failed"
    assert st["last_run"]["failure_code"] == "no_worker" and "pm_simulator worker" in st["last_run"]["failure_message"]
    assert start(client).status_code == 201  # slot is free again


def test_running_run_without_heartbeats_fails_with_worker_lost(client, db):
    rid = running(client)
    sql(db, "UPDATE simulation_runs SET last_heartbeat_at = now() - interval '121 seconds', "
            "lease_expires_at = now() - interval '90 seconds'")
    st = status(client)
    assert st["active_run"] is None and st["last_run"]["status"] == "failed"
    assert st["last_run"]["failure_code"] == "worker_lost"
    r = beat(client, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_not_active"


# ---- finish ---------------------------------------------------------------------------------------------
def test_finish_completed_then_a_new_run_can_start(client, db):
    rid = running(client)
    r = finish(client, rid, "completed")
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["ended_at"]
    assert r.json()["failure_code"] is None and r.json()["lease_active"] is False
    assert finish(client, rid, "completed").status_code == 200       # idempotent repeat
    again = finish(client, rid, "failed")
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_not_active"
    assert start(client).status_code == 201


def test_finish_failed_records_a_sanitised_reason(client, db):
    rid = running(client)
    r = finish(client, rid, "failed", failure_code="id_conflict", failure_message="engine\x00 1\nholds other data\x1b")
    run = r.json()
    assert run["status"] == "failed" and run["failure_code"] == "id_conflict"
    assert not any(ch in run["failure_message"] for ch in "\x00\n\x1b") and "holds other data" in run["failure_message"]
    rid2 = running(client)
    assert finish(client, rid2, "failed").json()["failure_code"] == "worker_error"


@pytest.mark.parametrize("kw", [{"failure_code": "Bad Code"}, {"failure_code": "x" * 51},
                                 {"failure_message": "m" * 501}, {"outcome": "exploded"}])
def test_finish_payload_validation(client, db, kw):
    rid = running(client)
    body = {"worker_id": W1, "outcome": "failed", **kw}
    assert client.post(f"{BASE}/runs/{rid}/finish", json=body, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("outcome", ["completed", "stopped", "failed"])
def test_finish_cannot_end_an_unclaimed_queued_run(client, db, outcome):
    """Regression: a queued run has no lease holder; no caller (not even an API-key holder) may finish it."""
    rid = start(client).json()["id"]
    for wid in (W1, W2, "w-neverclaimed001"):  # any worker identity: none of them holds a lease
        r = finish(client, rid, outcome, wid, failure_code="id_conflict" if outcome == "failed" else None)
        assert r.status_code == 409 and r.json()["error"]["code"] == "run_not_active", (wid, r.text)
        assert r.json()["error"]["details"] == {"status": "queued"}
    run = client.get(f"{BASE}/runs/{rid}").json()
    assert run["status"] == "queued" and run["ended_at"] is None and run["failure_code"] is None
    assert run["lease_active"] is False and scalar(db, "SELECT lease_owner FROM simulation_runs") is None
    # the run is untouched and still works through the legitimate path
    assert claim(client, W1).json()["run"]["run_id"] == rid
    assert finish(client, rid, "completed", W1).json()["status"] == "completed"


def test_failed_finish_attempts_do_not_free_the_active_slot_or_shorten_the_queue_timeout(client, db):
    rid = start(client).json()["id"]
    assert finish(client, rid, "failed", W1, failure_code="worker_error").status_code == 409
    r = start(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_already_active"
    assert r.json()["error"]["details"]["active_run_id"] == rid


def test_safe_idempotent_repeats_on_finished_runs_are_unchanged(client, db):
    rid = running(client, W1)
    assert finish(client, rid, "completed", W1).status_code == 200
    assert finish(client, rid, "completed", W1).status_code == 200      # same worker repeats
    assert finish(client, rid, "completed", W2).status_code == 200      # anyone repeating the same outcome: no-op
    assert finish(client, rid, "failed", W2).status_code == 409         # a different outcome is refused
    assert client.get(f"{BASE}/runs/{rid}").json()["status"] == "completed"
    # a queued run stopped by the operator is terminal too: repeating its outcome is a harmless no-op
    rid2 = start(client).json()["id"]
    stop(client, rid2)
    assert finish(client, rid2, "stopped", W2).status_code == 200
    assert finish(client, rid2, "completed", W2).status_code == 409


def test_only_the_lease_holder_can_finish(client, db):
    rid = running(client, W1)
    assert finish(client, rid, "completed", W2).json()["error"]["code"] == "lease_lost"
    assert finish(client, 9999, "completed").status_code == 404
    assert status(client)["active_run"]["status"] == "running"


# ---- history & CORS ----------------------------------------------------------------------------------------
def test_history_listing_and_lookup(client, db):
    ids = []
    for _ in range(3):
        rid = start(client).json()["id"]
        stop(client, rid)
        ids.append(rid)
    page = client.get(f"{BASE}/runs", params={"limit": 2}).json()
    assert page["total"] == 3 and [r["id"] for r in page["items"]] == [ids[2], ids[1]]
    assert [r["id"] for r in client.get(f"{BASE}/runs", params={"limit": 2, "offset": 2}).json()["items"]] == [ids[0]]
    assert client.get(f"{BASE}/runs/{ids[0]}").json()["status"] == "stopped"
    assert client.get(f"{BASE}/runs/9999").status_code == 404
    assert client.get(f"{BASE}/runs", params={"limit": 0}).status_code == 422
    st = status(client)
    assert st["limits"]["max_engines"] == 10 and st["limits"]["scenarios"] == ["test", "train"]


def test_cors_preflight_allows_the_control_and_marker_headers(client):
    r = client.options(f"{BASE}/runs/1", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "x-control-key,content-type"})
    assert r.status_code == 200 and "PATCH" in r.headers["access-control-allow-methods"]
    assert "x-control-key" in r.headers["access-control-allow-headers"].lower()
    bad = client.options(f"{BASE}/runs", headers={"Origin": "http://evil.example",
                                                  "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


# ---- engine-id allocation: a new run never reuses existing engine records ------------------------------------------------------
def test_engine_id_allocation_is_collision_free_and_inside_the_backend_limits():
    from pydantic import ValidationError

    from app.schemas.api import ReadingIn

    seen = {}
    for scenario in ("test", "train"):
        for slot in sim.SLOTS:
            for eid in sim.engine_ids_for(scenario, slot, range(1, 101)):
                assert eid not in seen, f"id {eid}: {(scenario, slot)} collides with {seen[eid]}"
                seen[eid] = (scenario, slot)
    assert len(seen) == 2 * 10 * 100 and min(seen) == 1 and max(seen) == 91_100
    ReadingIn(**reading(engine_id=max(seen)))                  # the ingestion contract accepts the largest allocated id
    with pytest.raises(ValidationError):
        ReadingIn(**reading(engine_id=1_000_001))             # ... whose own limit is 1,000,000


def test_start_is_refused_when_an_engine_id_already_holds_ordinary_readings(client, db):
    """Also covers ids created by an ad-hoc `pm_simulator replay` (origin 'api'): refused up front, not later in the worker."""
    assert post_reading(client, 3, 1).status_code == 201       # engine 3 exists, created by ordinary ingestion
    r = start(client, engines=[2, 3])                          # 3 collides, 2 does not
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "engine_ids_in_use" and "never reuses existing engine records" in err["message"]
    assert err["details"] == {"replay_slot": 0, "free_slots": list(range(1, 10)),
                              "engines": [{"engine_id": 3, "unit": 3, "origin": "api", "run_ids": []}]}
    assert API_KEY not in r.text and CONTROL_KEY not in r.text
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 0       # nothing created, no slot consumed
    assert status(client)["active_run"] is None
    ok = start(client, engines=[2, 3], replay_slot=1)                    # the suggested slot works
    assert ok.status_code == 201 and ok.json()["engine_ids"] == [10002, 10003]


def test_an_active_run_is_reported_before_engine_id_reuse(client, db):
    assert post_reading(client, 3, 1).status_code == 201
    first = start(client, engines=[7]).json()                  # now active (queued)
    r = start(client, engines=[3])                             # both problems at once
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_already_active"
    assert r.json()["error"]["details"]["active_run_id"] == first["id"]


def test_a_run_that_stored_nothing_does_not_use_up_its_slot(client, db):
    rid = start(client, engines=[3, 4]).json()["id"]
    stop(client, rid)                                          # never claimed: no engine record exists
    again = start(client, engines=[3, 4])
    assert again.status_code == 201 and again.json()["engine_ids"] == [3, 4]


def test_the_free_slot_hint_lists_only_slots_that_are_free_for_the_whole_selection(client, db):
    for eid in (3, 10004, 20003):                              # engine 3 in slot 0, unit 4 in slot 1, unit 3 in slot 2
        assert post_reading(client, eid, 1).status_code == 201
    r = start(client, engines=[3, 4], replay_slot=0)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["free_slots"] == [3, 4, 5, 6, 7, 8, 9]    # slots 0, 1 and 2 each have a collision
