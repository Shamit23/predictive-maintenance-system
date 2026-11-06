"""Operator control API -> PostgreSQL -> simulation worker -> ingestion -> prediction, end to end.

Database safety: only the shared `client` / `db` / `make_settings` fixtures are used (all built on the guarded
`db_url` fixture: the database name must end with '_test' and differ from DATABASE_URL). No database URL or engine
is created here, and the autouse fixture re-asserts the target. The worker talks to the in-process app through an
httpx client, exactly like the real worker talks to a running API.
"""
import time

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pm_ml.data import DEFAULT_RAW_DIR
from sqlalchemy import text

from app.main import create_app
from conftest import API_KEY, CONTROL_HEADERS, CONTROL_KEY, HEADERS, VALUE_COLS, scalar

sim = pytest.importorskip("pm_simulator.replay", reason="install the simulator first: pip install -e simulator")
worker = pytest.importorskip("pm_simulator.worker")

BASE = "/api/v1/simulation"
W1, W2 = "w-testworker00001", "w-testworker00002"
N = 8


@pytest.fixture(autouse=True)
def only_test_database(db):
    assert (db.url.database or "").endswith("_test"), "integration tests must run against a *_test database"


@pytest.fixture
def synthetic(monkeypatch):
    """Synthetic FD001-shaped sources (N cycles) instead of the NASA files."""
    def build(scenario, units, offset, **kw):
        out = []
        for u in units:
            rng = np.random.default_rng(u)
            f = pd.DataFrame({"unit": u, "cycle": np.arange(1, N + 1),
                              **{c: np.round(rng.normal(size=N), 6) for c in VALUE_COLS}})
            out.append(sim.EngineSource.from_frame(scenario, u, u + offset, f, 20 if scenario == "test" else None))
        return out

    monkeypatch.setattr(worker, "build_plan", build)


def start(client, **over):
    body = {"scenario": "test", "engines": [3], "interval_seconds": 0.1}
    body.update(over)
    r = client.post(f"{BASE}/runs", json=body, headers=CONTROL_HEADERS)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run_state(client, rid):
    return client.get(f"{BASE}/runs/{rid}").json()


def work(http, *, wid=W1, hook=None, **kw):
    """Run one worker pass. `hook(n)` is called on every wait (the worker's sleep); waits are not real."""
    n, logs = [0], []

    def sleep(s):
        n[0] += 1
        slept.append(s)
        if hook:
            hook(n[0])

    slept: list = []
    api = sim.ApiClient(http, API_KEY, sleep=lambda s: None)
    code = worker.run_worker(api, worker_id=wid, sleep=sleep, monotonic=time.monotonic, heartbeat_interval=0,
                             once=True, log=logs.append, **kw)
    return code, logs, slept


# ---- the happy path ----------------------------------------------------------------------------------------------
def test_queued_run_is_executed_by_the_worker_and_everything_is_marked_simulated(client, db, synthetic):
    rid = start(client, engines=[3, 4])
    assert run_state(client, rid)["status"] == "queued"                    # nobody has claimed it yet
    code, logs, _ = work(client)
    run = run_state(client, rid)
    assert code == 0 and run["status"] == "completed" and run["failure_code"] is None
    assert run["readings_stored"] == 2 * N == scalar(db, "SELECT count(*) FROM sensor_readings WHERE simulation_run_id=:r", r=rid)
    assert run["readings_created"] == 2 * N and run["lease_active"] is False and run["ended_at"]
    assert run["progress"]["3"]["last_cycle"] == N and run["progress"]["4"]["complete"] is True
    assert {e["id"]: e["origin"] for e in client.get("/api/v1/engines").json()["items"]} == {3: "simulation", 4: "simulation"}
    assert scalar(db, "SELECT count(*) FROM predictions") == 2 * N
    st = client.get(BASE).json()
    assert st["active_run"] is None and st["last_run"]["id"] == rid and st["worker_online"] is True
    assert API_KEY not in str(run) + str(st) and CONTROL_KEY not in str(run) + str(st)


def test_train_scenario_uses_the_server_derived_ids(client, db, synthetic):
    rid = start(client, scenario="train", engines=[5], replay_slot=1)
    work(client)
    assert run_state(client, rid)["engine_ids"] == [11005]
    assert scalar(db, "SELECT count(*) FROM sensor_readings WHERE engine_id=11005") == N


# ---- operator controls reach the worker -------------------------------------------------------------------------------
def test_stop_is_confirmed_by_the_worker_not_assumed(client, db, synthetic):
    rid = start(client)
    seen = {}

    def hook(n):
        if n == 3:
            r = client.post(f"{BASE}/runs/{rid}/stop", headers=CONTROL_HEADERS)
            seen["right_after_stop"] = r.json()["status"]

    work(client, hook=hook)
    assert seen["right_after_stop"] == "stopping"          # the API did not claim 'stopped' on its own
    run = run_state(client, rid)
    assert run["status"] == "stopped" and 0 < run["readings_stored"] < N and run["lease_active"] is False
    # the active-run slot is free again after the stop; engine 3 now holds data, so a new run needs other engines/slot
    assert client.post(f"{BASE}/runs", json={"scenario": "test", "engines": [4]}, headers=CONTROL_HEADERS).status_code == 201


def test_speed_change_is_applied_by_the_running_worker(client, db, synthetic):
    rid = start(client, interval_seconds=0.5)

    def hook(n):
        if n == 2:
            assert client.patch(f"{BASE}/runs/{rid}", json={"interval_seconds": 0.2}, headers=CONTROL_HEADERS).status_code == 200

    _, _, slept = work(client, hook=hook)
    assert slept[0] == 0.5 and slept[-1] == 0.2 and run_state(client, rid)["interval_seconds"] == 0.2
    assert run_state(client, rid)["status"] == "completed"


# ---- marker integrity ------------------------------------------------------------------------------------------------------
def test_a_run_cannot_take_over_an_engine_created_by_ordinary_ingestion(client, db, synthetic):
    from conftest import post_reading

    # The run is created while engine 3 does not exist yet (creating it afterwards over an existing engine is refused
    # up front: see test_start_is_refused_when_an_engine_id_already_holds_ordinary_readings). Then ordinary ingestion
    # creates engine 3 before the worker gets there, and the origin lock must still protect it at ingestion time.
    rid = start(client)
    assert post_reading(client, 3, 1).status_code == 201     # real/ordinary data for engine 3
    work(client)
    run = run_state(client, rid)
    assert run["status"] == "failed" and run["failure_code"] == "rejected"
    assert "engine_origin_mismatch" in run["failure_message"]
    assert scalar(db, "SELECT origin FROM engines WHERE id=3") == "api"
    assert scalar(db, "SELECT count(*) FROM sensor_readings WHERE engine_id=3") == 1  # untouched


# ---- reliability: crash, restart, API outage -------------------------------------------------------------------------------------
class Crash(BaseException):
    """Simulates the worker process being killed (nothing is reported, no cleanup)."""


def test_a_crashed_worker_is_replaced_and_the_run_resumes_without_duplicates(client, db, synthetic):
    rid = start(client)

    def die(n):
        if n == 3:
            raise Crash

    with pytest.raises(Crash):
        work(client, wid=W1, hook=die)
    mid = run_state(client, rid)
    assert mid["status"] == "running" and 0 < mid["readings_stored"] < N         # the server cannot know yet
    assert run_state(client, rid)["lease_active"] is True
    # a second worker cannot take it while the lease is valid
    assert work(client, wid=W2)[0] == 0 and run_state(client, rid)["readings_stored"] == mid["readings_stored"]
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET lease_expires_at = now() - interval '5 seconds'"))
    assert run_state(client, rid)["lease_active"] is False                         # honest about the dead lease
    work(client, wid=W2)
    run = run_state(client, rid)
    assert run["status"] == "completed" and run["readings_stored"] == N
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == N                   # every cycle exactly once
    assert scalar(db, "SELECT lease_owner FROM simulation_runs") is None


def test_an_api_restart_mid_run_does_not_stop_the_simulation(make_settings, client, db, synthetic):
    """The API process disappears for a while and comes back as a NEW app instance; state lives in PostgreSQL."""
    rid = start(client)

    class FlakyHttp:
        def __init__(self, current):
            self.current, self.replacement, self.down, self.refused = current, None, False, 0
            self.base_url = current.base_url

        def request(self, *a, **k):
            if self.down:
                self.refused += 1
                if self.refused > 40:  # the new process is up. Long enough that heartbeat retries (up to 12 refused
                    # requests per heartbeat) cannot absorb it and ingestion/claim must pause and reconnect too
                    self.current, self.down = self.replacement, False
                else:
                    raise httpx.ConnectError("api restarting")
            return self.current.request(*a, **k)

    with TestClient(create_app(make_settings()), raise_server_exceptions=False) as new_api:
        http = FlakyHttp(client)
        http.replacement = new_api
        code, logs, _ = work(http, hook=lambda n: setattr(http, "down", True) if n == 3 else None, max_outage=600)
        run = new_api.get(f"{BASE}/runs/{rid}").json()
    assert code == 0 and run["status"] == "completed" and run["readings_stored"] == N
    assert any("reconnected, resuming" in m for m in logs)
    assert run["error_count"] > 0 and run["last_error"]                              # the outage is visible to operators
    assert scalar(db, "SELECT count(*) FROM sensor_readings") == N                   # nothing lost or doubled


def test_worker_that_cannot_reach_the_api_for_too_long_gives_up_and_the_server_times_the_run_out(client, db, synthetic):
    rid = start(client)

    class DeadHttp:
        base_url = client.base_url
        dead = False

        def request(self, *a, **k):
            if self.dead:
                raise httpx.ConnectError("gone")
            return client.request(*a, **k)

    http = DeadHttp()
    ticks = [0.0]

    def hook(n):
        if n == 3:
            http.dead = True

    api = sim.ApiClient(http, API_KEY, sleep=lambda s: None)
    code = worker.run_worker(api, worker_id=W1, sleep=lambda s: (hook(1 + int(ticks.__setitem__(0, ticks[0] + 1) or ticks[0]))),
                             monotonic=lambda: ticks[0] * 50, heartbeat_interval=0, once=True, max_outage=120,
                             log=lambda m: None)
    assert code == 0 and run_state(client, rid)["status"] == "running"             # worker could not report anything
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET last_heartbeat_at = now() - interval '200 seconds', "
                       "lease_expires_at = now() - interval '170 seconds'"))
    run = run_state(client, rid)
    assert run["status"] == "failed" and run["failure_code"] == "worker_lost"


# ---- engine ids are never reused by a new run (lifecycle scenarios) ------------------------------------------------------------
def try_start(client, **over):
    body = {"scenario": "test", "engines": [3], "interval_seconds": 0.1}
    body.update(over)
    return client.post(f"{BASE}/runs", json=body, headers=CONTROL_HEADERS)


def stored_by_run(db, engine_id):
    with db.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT simulation_run_id, count(*) FROM sensor_readings WHERE engine_id=:e GROUP BY 1 ORDER BY 1"),
            {"e": engine_id})]


def test_a_second_run_cannot_silently_reuse_a_completed_runs_engine_ids(client, db, synthetic):
    r1 = start(client)                                          # test scenario, engine 3, slot 0
    work(client)
    first = run_state(client, r1)
    assert first["status"] == "completed" and first["readings_created"] == first["readings_stored"] == N

    r = try_start(client)                                       # same scenario, engine units and slot
    err = r.json()["error"]
    assert r.status_code == 409 and err["code"] == "engine_ids_in_use"
    assert err["details"]["engines"] == [{"engine_id": 3, "unit": 3, "origin": "simulation", "run_ids": [r1]}]
    assert err["details"]["free_slots"] == list(range(1, 10))
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 1       # no run was created ...
    assert stored_by_run(db, 3) == [(r1, N)]                              # ... and the stored history is untouched
    st = client.get(BASE).json()
    assert st["active_run"] is None and st["last_run"]["id"] == r1 and st["last_run"]["status"] == "completed"

    r2 = try_start(client, replay_slot=1)                       # a fresh replay goes through another slot
    assert r2.status_code == 201 and r2.json()["engine_ids"] == [10003]
    work(client, wid=W2)
    second = run_state(client, r2.json()["id"])
    assert second["status"] == "completed" and second["readings_created"] == second["readings_stored"] == N
    assert stored_by_run(db, 10003) == [(r2.json()["id"], N)] and stored_by_run(db, 3) == [(r1, N)]


def test_a_stopped_partial_run_cannot_be_continued_by_a_new_run_with_the_same_ids(client, db, synthetic):
    r1 = start(client)

    def stop_midway(n):
        if n == 3:
            client.post(f"{BASE}/runs/{r1}/stop", headers=CONTROL_HEADERS)

    work(client, hook=stop_midway)
    part = run_state(client, r1)
    assert part["status"] == "stopped" and 0 < part["readings_stored"] < N
    r = try_start(client)
    assert r.status_code == 409 and r.json()["error"]["details"]["engines"][0]["run_ids"] == [r1]
    assert stored_by_run(db, 3) == [(r1, part["readings_stored"])]       # one engine's history stays with ONE run


def test_overlapping_engine_selections_are_refused_whole_and_untouched_engines_stay_usable(client, db, synthetic):
    r1 = start(client, engines=[3, 4])
    work(client)
    r = try_start(client, engines=[4, 5])                       # 4 was used by run 1, 5 is fresh
    assert r.status_code == 409
    assert [e["engine_id"] for e in r.json()["error"]["details"]["engines"]] == [4]       # only the collision is named
    assert scalar(db, "SELECT count(*) FROM simulation_runs") == 1
    assert scalar(db, "SELECT count(*) FROM engines WHERE id=5") == 0                    # nothing was half-created

    r2 = try_start(client, engines=[5, 6])                      # non-overlapping selection in the same slot: fine, fresh data
    assert r2.status_code == 201
    work(client, wid=W2)
    assert run_state(client, r2.json()["id"])["readings_stored"] == 2 * N

    r3 = try_start(client, engines=[4], replay_slot=1)          # an overlapping unit in another slot: fine
    assert r3.status_code == 201 and r3.json()["engine_ids"] == [10004]
    assert stored_by_run(db, 3) == [(r1, N)] and stored_by_run(db, 4) == [(r1, N)]


def test_scenarios_are_isolated_from_each_other(client, db, synthetic):
    r1 = start(client, scenario="test")
    work(client)
    assert try_start(client, scenario="test").status_code == 409         # test/slot 0 is used up ...
    r2 = try_start(client, scenario="train")                             # ... but train/slot 0 (engine 1003) is untouched
    assert r2.status_code == 201 and r2.json()["engine_ids"] == [1003]
    work(client, wid=W2)
    assert run_state(client, r2.json()["id"])["readings_stored"] == N
    assert try_start(client, scenario="train").status_code == 409        # and is used up in turn
    assert stored_by_run(db, 3) == [(r1, N)] and stored_by_run(db, 1003) == [(r2.json()["id"], N)]


def test_restarting_the_same_run_still_resumes_idempotently(client, db, synthetic):
    r1 = start(client)

    def die(n):
        if n == 3:
            raise Crash

    with pytest.raises(Crash):
        work(client, wid=W1, hook=die)
    mid = run_state(client, r1)["readings_stored"]
    assert 0 < mid < N
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET lease_expires_at = now() - interval '5 seconds'"))
    work(client, wid=W2)                                        # takes over the SAME run on the SAME engine ids
    run = run_state(client, r1)
    assert run["status"] == "completed" and run["readings_stored"] == N
    assert stored_by_run(db, 3) == [(r1, N)]                    # every cycle exactly once, still attributed to run 1
    assert try_start(client).status_code == 409                 # afterwards the ids are used up, as designed


def test_a_worker_that_dies_after_the_last_reading_is_recovered_without_new_data(client, db, synthetic, monkeypatch):
    r1 = start(client)
    original_finish = worker._finish

    def crash_finish(*a, **k):
        raise Crash

    monkeypatch.setattr(worker, "_finish", crash_finish)
    with pytest.raises(Crash):
        work(client, wid=W1)
    assert run_state(client, r1)["status"] == "running" and run_state(client, r1)["readings_stored"] == N
    monkeypatch.setattr(worker, "_finish", original_finish)
    with db.begin() as c:
        c.execute(text("UPDATE simulation_runs SET lease_expires_at = now() - interval '5 seconds'"))
    work(client, wid=W2)                                        # legitimately finds everything stored for ITS OWN run
    run = run_state(client, r1)
    assert run["status"] == "completed" and run["readings_stored"] == N and stored_by_run(db, 3) == [(r1, N)]


def test_refused_starts_leave_run_history_and_the_active_run_rule_intact(client, db, synthetic):
    r1 = start(client)
    work(client)
    for _ in range(3):
        assert try_start(client).status_code == 409
    hist = client.get(f"{BASE}/runs").json()
    assert hist["total"] == 1 and [(r["id"], r["status"]) for r in hist["items"]] == [(r1, "completed")]

    r2 = try_start(client, replay_slot=1).json()["id"]          # accepted: it is now the active run
    r = try_start(client, replay_slot=2)                        # the active-run rule still applies
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_already_active"
    assert r.json()["error"]["details"]["active_run_id"] == r2
    assert client.post(f"{BASE}/runs/{r2}/stop", headers=CONTROL_HEADERS).json()["status"] == "stopped"
    hist = client.get(f"{BASE}/runs").json()
    assert [(r["id"], r["status"]) for r in hist["items"]] == [(r2, "stopped"), (r1, "completed")]


# ---- real FD001 data (skipped without the dataset) -----------------------------------------------------------------------------------
@pytest.mark.skipif(not (DEFAULT_RAW_DIR / "test_FD001.txt").is_file(), reason="needs FD001 data in ml/data/raw")
def test_real_fd001_engine_runs_through_the_control_plane(client, db):
    rid = start(client, engines=[1])  # test unit 1: 31 observed cycles
    code, _, _ = work(client)
    run = run_state(client, rid)
    assert code == 0 and run["status"] == "completed" and run["readings_stored"] == 31
    assert scalar(db, "SELECT origin FROM engines WHERE id=1") == "simulation"
    assert scalar(db, "SELECT count(*) FROM predictions WHERE engine_id=1") == 31
