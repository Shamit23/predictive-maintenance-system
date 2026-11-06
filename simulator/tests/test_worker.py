"""Worker behaviour against an in-memory fake of the control API (claim / heartbeat / finish) and ingestion.
A virtual clock replaces real sleeping, so outage and timeout paths run instantly."""
import json
import re

import httpx
import pytest

import pm_simulator.__main__ as cli
import pm_simulator.worker as worker_mod
from conftest import KEY, FakeBackend, envelope, make_src
from pm_simulator.replay import ApiClient, ReplayError, ServerUnavailable, run_replay
from pm_simulator.worker import run_worker

CONTROL_KEY = "must-never-be-sent-by-the-worker-0000"
W = "w-testworker00001"


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def monotonic(self):
        return self.t


class FakeControl(FakeBackend):
    """FakeBackend (ingestion) + the three worker-protocol endpoints."""

    def __init__(self):
        super().__init__()
        self.run = None
        self.owner = None
        self.desired_status, self.desired_interval = "running", 1.0
        self.claims, self.beats, self.finishes, self.headers_seen = [], [], [], []
        self.on_beat = None
        self.lease_lost = False
        self.claim_http = 200
        self.claim_down = False
        self.hb_script = []        # consumed one per heartbeat attempt: an HTTP status (!= 200) or an exception to raise
        self.hb_always = None      # if set, EVERY heartbeat answers this HTTP status (persistent failure)
        self.on_claim = None       # called with the claim count at every claim request
        self.claim_script = []     # consumed one per claim request: an HTTP status (!= 200) or an exception to raise
        self.claim_attempts = 0    # every claim request received, including failed ones
        self.readings_at_claim = []  # total readings stored at the moment of each claim

    def handler(self, request):
        path = request.url.path
        self.headers_seen.append((path, {k.lower(): v for k, v in request.headers.items()}))
        if path == "/api/v1/simulation/worker/claim":
            self.claim_attempts += 1
            if self.claim_script:
                item = self.claim_script.pop(0)
                if isinstance(item, Exception):
                    raise item
                if item != 200:
                    return httpx.Response(item, json=envelope("injected"))
            if self.claim_down:
                raise httpx.ConnectError("down")
            if self.claim_http != 200:
                return httpx.Response(self.claim_http, json=envelope("unauthorized"))
            wid = json.loads(request.content)["worker_id"]
            self.claims.append(wid)
            self.readings_at_claim.append(sum(len(v) for v in self.store.values()))
            if self.on_claim:
                self.on_claim(len(self.claims))
            if self.run is None or self.owner not in (None, wid):
                return httpx.Response(200, json={"run": None})
            self.owner = wid
            return httpx.Response(200, json={"run": self.run})
        m = re.fullmatch(r"/api/v1/simulation/runs/(\d+)/(heartbeat|finish)", path)
        if m:
            body = json.loads(request.content)
            if m.group(2) == "heartbeat":
                self.beats.append(body)
                if self.on_beat:
                    self.on_beat(len(self.beats))
                if self.hb_script:
                    item = self.hb_script.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    if item != 200:
                        return httpx.Response(item, json=envelope("injected"))
                if self.hb_always:
                    return httpx.Response(self.hb_always, json=envelope("injected"))
                if self.lease_lost:
                    return httpx.Response(409, json=envelope("lease_lost"))
                return httpx.Response(200, json={"status": self.desired_status,
                                                 "interval_seconds": self.desired_interval})
            self.finishes.append(body)
            return httpx.Response(200, json={"status": body["outcome"]})
        return super().handler(request)

    def plan(self, **over):
        self.run = {"run_id": 7, "status": "running", "scenario": "test", "engine_units": [1], "id_offset": 0,
                    "interval_seconds": 1.0, "max_cycles": None, **over}
        return self

    @property
    def reading_headers(self):
        return [h for p, h in self.headers_seen if p == "/api/v1/readings"]


@pytest.fixture
def ctl():
    return FakeControl()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def n_cycles(monkeypatch):
    """Synthetic sources instead of the NASA files; returns a mutable cycle count."""
    box = {"n": 6, "calls": []}

    def fake_build(scenario, units, offset, **kw):
        box["calls"].append((scenario, list(units), offset))
        return [make_src(scenario, u, box["n"], offset) for u in units]

    monkeypatch.setattr(worker_mod, "build_plan", fake_build)
    return box


def api_for(ctl, clock, key=KEY):
    http = httpx.Client(transport=httpx.MockTransport(ctl.handler), base_url="http://fake.test")
    return ApiClient(http, key, sleep=clock.sleep)


def go(ctl, clock, **kw):
    log = []
    kw.setdefault("heartbeat_interval", 0)
    code = run_worker(api_for(ctl, clock), worker_id=W, sleep=clock.sleep, monotonic=clock.monotonic,
                      log=log.append, once=kw.pop("once", True), **kw)
    return code, log


# ---- idle / polling -----------------------------------------------------------------------------------
def test_idle_worker_with_once_exits_cleanly_and_sends_nothing_else(ctl, clock):
    code, _ = go(ctl, clock)
    assert code == 0 and ctl.claims == [W] and ctl.posts == [] and ctl.finishes == []


def test_idle_worker_polls_at_the_poll_interval_until_interrupted(ctl, clock):
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 3:
            raise KeyboardInterrupt

    log = []
    code = run_worker(api_for(ctl, clock), worker_id=W, poll_interval=2.0, sleep=sleep, monotonic=clock.monotonic,
                      log=log.append)
    assert code == 130 and calls == [2.0, 2.0, 2.0] and len(ctl.claims) == 3


def test_claim_unauthorized_exits_1_without_leaking_the_key(ctl, clock):
    ctl.claim_http = 401
    code, log = go(ctl, clock, once=False)
    assert code == 1 and any("API key" in m for m in log) and not any(KEY in m for m in log)


# ---- executing a run --------------------------------------------------------------------------------------
def test_run_is_executed_marked_simulated_and_reported_completed(ctl, clock, n_cycles):
    n_cycles["n"] = 4
    ctl.plan(engine_units=[1, 2], id_offset=1000, interval_seconds=1.0)
    code, log = go(ctl, clock)
    assert code == 0 and n_cycles["calls"] == [("test", [1, 2], 1000)]
    assert sorted(ctl.store) == [1001, 1002] and all(len(v) == 4 for v in ctl.store.values())
    assert {h["x-simulation-run"] for h in ctl.reading_headers} == {"7"}      # every reading carries the marker
    assert {h["x-api-key"] for h in ctl.reading_headers} == {KEY}
    assert ctl.finishes == [{"worker_id": W, "outcome": "completed"}]
    last = ctl.beats[-1]
    assert (last["readings_created"], last["readings_duplicate"]) == (8, 0)
    assert last["progress"]["1001"] == {"unit": 1, "n_cycles": 4, "last_cycle": 4, "created": 4, "duplicates": 0,
                                         "rewinds": 0, "complete": True}
    assert any("SIMULATED" in m for m in log)


def test_worker_never_sends_a_control_key_and_uses_only_the_api_key(ctl, clock, n_cycles):
    ctl.plan()
    go(ctl, clock)
    for path, headers in ctl.headers_seen:
        assert "x-control-key" not in headers, path
        if path == "/api/v1/readings" or path.startswith("/api/v1/simulation"):  # writes + worker protocol
            assert headers.get("x-api-key") == KEY, path
        assert CONTROL_KEY not in json.dumps(headers)


def test_plain_cli_replay_stays_unmarked(ctl, clock):
    """Phase 4a behaviour is unchanged: ad-hoc replays send no X-Simulation-Run header."""
    run_replay([make_src(n=3)], api_for(ctl, clock), interval=0)
    assert len(ctl.reading_headers) == 3 and all("x-simulation-run" not in h for h in ctl.reading_headers)


def test_stop_request_ends_the_replay_and_is_reported_as_stopped(ctl, clock, n_cycles):
    n_cycles["n"] = 20
    ctl.plan()
    ctl.on_beat = lambda k: setattr(ctl, "desired_status", "stopping") if k == 2 else None
    code, _ = go(ctl, clock)
    sent = max(ctl.store[1])
    assert code == 0 and 0 < sent < 20
    assert ctl.finishes == [{"worker_id": W, "outcome": "stopped"}]


def test_claiming_an_already_stopping_run_stops_without_sending_anything(ctl, clock, n_cycles):
    ctl.plan(status="stopping")
    go(ctl, clock)
    assert ctl.posts == [] and ctl.finishes == [{"worker_id": W, "outcome": "stopped"}]


def test_speed_changes_apply_to_the_next_wait(ctl, clock, n_cycles):
    n_cycles["n"] = 8
    ctl.plan(interval_seconds=1.0)
    ctl.on_beat = lambda k: setattr(ctl, "desired_interval", 0.25) if k == 3 else None
    go(ctl, clock)
    assert clock.sleeps[0] == 1.0 and 0.25 in clock.sleeps
    assert clock.sleeps.index(0.25) > 0 and all(s == 0.25 for s in clock.sleeps[clock.sleeps.index(0.25):])


def test_long_intervals_are_waited_in_one_second_slices_so_stop_stays_responsive(ctl, clock, n_cycles):
    n_cycles["n"] = 3
    ctl.plan(interval_seconds=2.5)
    ctl.on_beat = lambda k: setattr(ctl, "desired_status", "stopping") if k == 3 else None
    go(ctl, clock)
    assert max(clock.sleeps) <= 1.0 and sum(clock.sleeps) < 2.5 * 2  # stopped partway through the wait


# ---- losing the run -----------------------------------------------------------------------------------------
def test_refused_heartbeat_means_stop_immediately_and_report_nothing(ctl, clock, n_cycles):
    n_cycles["n"] = 20
    ctl.plan()
    ctl.on_beat = lambda k: setattr(ctl, "lease_lost", True) if k == 2 else None
    code, log = go(ctl, clock)
    assert code == 0 and ctl.finishes == [] and max(ctl.store[1]) < 20
    assert any("heartbeat refused" in m for m in log)


# ---- failures are reported with a code, never with secrets -------------------------------------------------------
def test_id_collision_is_reported_failed_with_a_code(ctl, clock, n_cycles):
    ctl.plan()
    other = make_src(unit=9, n=6)
    ctl.store[1] = {c: other.values[c - 1] for c in (1, 2)}
    go(ctl, clock)
    (f,) = ctl.finishes
    assert f["outcome"] == "failed" and f["failure_code"] == "id_conflict" and "--id-offset" in f["failure_message"]
    assert KEY not in json.dumps(f)


def test_plan_errors_are_reported_failed(ctl, clock, monkeypatch):
    ctl.plan(engine_units=[500])

    def boom(*a, **k):
        raise ReplayError("test data has no engine(s) [500] (valid: 1..100)")

    monkeypatch.setattr(worker_mod, "build_plan", boom)
    go(ctl, clock)
    assert ctl.finishes[0]["failure_code"] == "plan_error" and ctl.posts == []


def test_unexpected_exceptions_are_reported_generically(ctl, clock, n_cycles, monkeypatch):
    ctl.plan()

    def boom(*a, **k):
        raise RuntimeError(f"secret detail {KEY}")

    monkeypatch.setattr(worker_mod, "run_replay", boom)
    go(ctl, clock)
    (f,) = ctl.finishes
    assert f["failure_code"] == "worker_error" and f["failure_message"] == "Unexpected worker error."
    assert "secret" not in json.dumps(f) and KEY not in json.dumps(f)


def test_ctrl_c_reports_the_run_failed_and_exits_130(ctl, clock, n_cycles):
    n_cycles["n"] = 10
    ctl.plan()
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 2:
            raise KeyboardInterrupt

    log = []
    code = run_worker(api_for(ctl, clock), worker_id=W, sleep=sleep, monotonic=clock.monotonic, log=log.append,
                      heartbeat_interval=0, once=True)
    assert code == 130
    assert ctl.finishes[-1]["failure_code"] == "worker_interrupted"


# ---- outages -------------------------------------------------------------------------------------------------------
def test_backend_outage_pauses_then_resumes_the_same_run(ctl, clock, n_cycles):
    n_cycles["n"] = 8
    ctl.plan()
    ctl.on_beat = lambda k: setattr(ctl, "fail_queue", [503] * 4) if k == 3 else None  # next reading: outage
    code, log = go(ctl, clock)
    assert code == 0 and sorted(ctl.store[1]) == list(range(1, 9))                        # nothing lost
    assert any("reconnected, resuming" in m for m in log)
    assert ctl.finishes == [{"worker_id": W, "outcome": "completed"}]
    beats = [(b["readings_created"], b["error_count"]) for b in ctl.beats]
    assert [c for c, _ in beats] == sorted(c for c, _ in beats) and beats[-1][0] == 8     # cumulative, monotonic
    assert beats[-1][1] > 0 and ctl.beats[-1]["last_error"] == "HTTP 503"                  # outage visible to operators


def test_outage_longer_than_max_outage_abandons_the_run_locally(ctl, clock, n_cycles):
    n_cycles["n"] = 8
    ctl.plan()

    def down(k):
        if k == 2:
            ctl.fail_queue = [503] * 99
            ctl.claim_down = True

    ctl.on_beat = down
    code, log = go(ctl, clock, max_outage=30.0)
    assert code == 0 and ctl.finishes == []                      # cannot report; the server will mark it worker_lost
    assert any("giving up" in m for m in log) and clock.t >= 30   # bounded: virtual time passed, no infinite loop
    assert max(ctl.store[1]) < 8


def test_run_taken_over_during_an_outage_is_abandoned(ctl, clock, n_cycles):
    n_cycles["n"] = 8
    ctl.plan()

    def takeover(k):
        if k == 2:
            ctl.fail_queue = [503] * 4
            ctl.owner = "w-someoneelse0001"  # another worker now holds the lease

    ctl.on_beat = takeover
    code, log = go(ctl, clock)
    assert ctl.finishes == [] and any("no longer ours" in m for m in log)


def test_idle_outage_is_retried_not_fatal(ctl, clock):
    ctl.claim_down = True
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 3:
            raise KeyboardInterrupt

    code = run_worker(api_for(ctl, clock), worker_id=W, poll_interval=2.0, sleep=sleep, monotonic=clock.monotonic,
                      log=lambda m: None)
    assert code == 130  # never crashed while the backend was down


# ---- client accounting & CLI ---------------------------------------------------------------------------------------
def test_with_run_marks_readings_and_retries_are_counted(ctl, clock):
    client = api_for(ctl, clock).with_run(42)
    ctl.fail_queue = [503, 503]
    src = make_src(n=1)
    client.post_reading(src.body(1, __import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)))
    assert client.retries == 2 and client.last_transient == "HTTP 503"
    assert ctl.reading_headers[-1]["x-simulation-run"] == "42"


@pytest.fixture
def cli_env(ctl, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("CONTROL_API_KEY", CONTROL_KEY)  # present in the environment, must still never be used
    return lambda url, t: httpx.Client(transport=httpx.MockTransport(ctl.handler), base_url="http://fake.test")


def run_cli(argv, factory):
    lines = []
    code = cli.main(argv, http_factory=factory, out=lines.append, sleep=lambda s: None)
    return code, "\n".join(lines)


def test_cli_worker_idle_once(ctl, cli_env):
    code, out = run_cli(["worker", "--once"], cli_env)
    assert code == 0 and "simulation worker w-" in out and "SIMULATED" in out and KEY not in out
    assert all("x-control-key" not in h for _, h in ctl.headers_seen)


def test_cli_worker_executes_a_queued_run(ctl, cli_env, n_cycles):
    n_cycles["n"] = 3
    ctl.plan()
    code, out = run_cli(["worker", "--once", "--heartbeat-interval", "0.5"], cli_env)
    assert code == 0 and ctl.finishes[-1]["outcome"] == "completed" and "claimed run 7" in out
    assert CONTROL_KEY not in json.dumps(ctl.headers_seen)


@pytest.mark.parametrize("argv", [["worker", "--poll-interval", "0"], ["worker", "--max-outage", "0"],
                                  ["worker", "--timeout", "-1"]])
def test_cli_worker_usage_errors(cli_env, argv):
    assert run_cli(argv, cli_env)[0] == 2


def test_cli_worker_without_an_api_key_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("API_KEY", raising=False)
    code = cli.main(["worker", "--env-file", str(tmp_path / "none.env"), "--once"], out=lambda m: None)
    assert code == 1 and "API_KEY not found" in capsys.readouterr().err


# ---- heartbeat resilience: transient failures are retried, definitive ones are not ---------------------------------------
def run_ok(ctl, n):
    """The run finished normally with every cycle stored and a 'completed' report."""
    assert sorted(ctl.store[1]) == list(range(1, n + 1))
    assert ctl.finishes == [{"worker_id": W, "outcome": "completed"}]


@pytest.mark.parametrize("failure", [500, 501, 408, 429])
def test_a_single_transient_heartbeat_failure_is_retried_and_does_not_abandon_the_run(ctl, clock, n_cycles, failure):
    n_cycles["n"] = 6
    ctl.plan()
    ctl.hb_script = [failure]
    code, log = go(ctl, clock)
    assert code == 0 and not any("refused" in m for m in log)
    run_ok(ctl, 6)
    assert 0.5 in clock.sleeps                                  # the retry backoff (first step)
    assert len(ctl.claims) == 1                                 # no reconnect was needed


def test_two_consecutive_failures_are_retried_with_exponential_backoff(ctl, clock, n_cycles):
    n_cycles["n"] = 6
    ctl.plan()
    ctl.hb_script = [500, 500]
    go(ctl, clock)
    run_ok(ctl, 6)
    i = clock.sleeps.index(0.5)
    assert clock.sleeps[i:i + 2] == [0.5, 1.0]                  # 0.5 then 1.0, bounded to HEARTBEAT_ATTEMPTS


def test_an_unreachable_backend_on_heartbeat_is_transient_not_fatal(ctl, clock, n_cycles):
    n_cycles["n"] = 6
    ctl.plan()
    ctl.hb_script = [httpx.ConnectError("down")] * 4             # exhausts the client's own retries -> ServerUnavailable
    code, log = go(ctl, clock)
    assert code == 0 and not any("refused" in m for m in log)
    run_ok(ctl, 6)


def test_exhausting_the_retry_budget_within_the_grace_period_just_waits_for_the_next_tick(ctl, clock, n_cycles):
    n_cycles["n"] = 8
    ctl.plan()
    ctl.hb_script = [500, 500, 500]                              # all 3 attempts of the first heartbeat fail
    code, log = go(ctl, clock)
    assert code == 0 and not any("refused" in m or "reconnected" in m for m in log)
    run_ok(ctl, 8)
    assert len(ctl.claims) == 1 and ctl.beats[-1]["readings_created"] == 8


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422])
def test_definitive_refusals_abandon_immediately_without_any_retry(ctl, clock, n_cycles, status):
    n_cycles["n"] = 20
    ctl.plan()
    ctl.hb_script = [status]
    code, log = go(ctl, clock)
    assert code == 0 and ctl.finishes == []                      # nothing reported for a run that is no longer ours
    assert len(ctl.beats) == 1                                   # exactly one attempt: no retry
    assert 0.5 not in clock.sleeps and any(f"HTTP {status}" in m for m in log)
    assert max(ctl.store[1]) <= 2                                # stopped processing right away


def test_lease_fencing_still_stops_the_worker_on_409(ctl, clock, n_cycles):
    n_cycles["n"] = 20
    ctl.plan()
    ctl.on_beat = lambda k: setattr(ctl, "lease_lost", True) if k == 2 else None
    code, log = go(ctl, clock)
    assert ctl.finishes == [] and max(ctl.store[1]) < 20 and any("heartbeat refused (HTTP 409)" in m for m in log)


def test_persistent_heartbeat_failure_reverifies_ownership_and_only_resumes_while_the_run_is_ours(ctl, clock, n_cycles):
    n_cycles["n"] = 40
    ctl.plan()
    ctl.hb_always = 500
    code, log = go(ctl, clock, heartbeat_grace=5.0)
    assert code == 0 and sorted(ctl.store[1]) == list(range(1, 41))
    assert len(ctl.claims) >= 3 and sum("reconnected, resuming" in m for m in log) >= 2   # ownership re-checked repeatedly
    assert ctl.finishes == [{"worker_id": W, "outcome": "completed"}]                      # failing final heartbeat is best-effort


def test_losing_the_lease_while_heartbeats_fail_stops_ingestion_within_the_grace_window(ctl, clock, n_cycles):
    n_cycles["n"] = 200
    ctl.plan()
    mark = {}

    def takeover(k):
        if k == 3:
            ctl.hb_always = 500                     # heartbeats start failing ...
            ctl.owner = "w-someoneelse0001"         # ... and, unseen, another worker now holds the lease
            mark["sent"] = max(ctl.store[1])

    ctl.on_beat = takeover
    code, log = go(ctl, clock, heartbeat_grace=5.0)
    assert ctl.finishes == [] and any("no longer ours" in m for m in log)
    assert max(ctl.store[1]) - mark["sent"] <= 6, "kept ingesting long after ownership could have been lost"
    assert max(ctl.store[1]) < 200


def test_a_known_stop_request_is_not_delayed_by_heartbeat_retries(ctl, clock, n_cycles):
    n_cycles["n"] = 20
    ctl.plan()

    def stop_then_fail(k):
        if k == 2:
            ctl.desired_status = "stopping"        # heartbeat 2 answers 200 + 'stopping': the worker now knows
        if k == 3:
            ctl.hb_script = [500, 500, 500]        # heartbeat 3 is the final progress report; it fails

    ctl.on_beat = stop_then_fail
    code, _ = go(ctl, clock)
    assert code == 0 and ctl.finishes == [{"worker_id": W, "outcome": "stopped"}]
    assert len(ctl.beats) == 3                       # beat 1, beat 2 (stopping), then exactly ONE failed final attempt
    assert clock.sleeps == [1.0]                     # only the interval wait: no 0.5 / 1.0 retry backoff after the stop


def test_a_stop_requested_while_heartbeats_were_failing_is_honoured_after_the_reconnect(ctl, clock, n_cycles):
    n_cycles["n"] = 200
    ctl.plan()

    def stop_unseen(k):
        if k == 2:
            ctl.hb_always = 500
            ctl.run["status"] = "stopping"          # the operator asked to stop; only a claim will reveal it

    ctl.on_beat = stop_unseen
    code, log = go(ctl, clock, heartbeat_grace=5.0)
    assert ctl.finishes == [{"worker_id": W, "outcome": "stopped"}]
    assert any("reconnected, resuming" in m for m in log)           # ownership was re-verified by the claim ...
    assert max(ctl.store[1]) <= 8 < 200                             # ... ingestion stopped within the grace window ...
    assert len(ctl.claims) == 2                                     # ... after exactly one re-verifying claim ...
    total = sum(len(v) for v in ctl.store.values())
    assert total == ctl.readings_at_claim[1]                        # ... and NOTHING was ingested after that claim revealed 'stopping'


def test_cli_exposes_and_validates_the_heartbeat_grace(cli_env, ctl):
    code, out = run_cli(["worker", "--once", "--heartbeat-grace", "15"], cli_env)
    assert code == 0 and "simulation worker" in out
    assert run_cli(["worker", "--heartbeat-grace", "0"], cli_env)[0] == 2


# ---- an abandoned run must not turn into a tight re-claim loop; the grace default is a single constant ---------------------
def test_an_abandoned_run_is_followed_by_a_poll_interval_pause_not_a_tight_reclaim_loop(ctl, clock, n_cycles):
    """A definitive heartbeat refusal that leaves the run claimable by us (a 422 here) used to re-claim at once."""
    n_cycles["n"] = 200
    ctl.plan()
    ctl.hb_always = 422                                   # refused every time, but the run is still ours
    ctl.on_claim = lambda k: pytest.fail(f"tight re-claim loop: claim #{k} without the poll_interval pause") if k > 8 else None
    pauses = []

    def sleep(s):
        clock.sleep(s)
        if s == 2.0:
            pauses.append(s)
            if len(pauses) == 3:
                raise KeyboardInterrupt                   # end the otherwise endless worker loop

    log = []
    code = run_worker(api_for(ctl, clock), worker_id=W, poll_interval=2.0, sleep=sleep, monotonic=clock.monotonic,
                      log=log.append, heartbeat_interval=0, once=False)
    assert code == 130
    assert len(ctl.claims) == 3 and clock.sleeps.count(2.0) == 3   # one poll_interval pause after each abandoned run
    assert ctl.finishes == []                                       # nothing is reported for an abandoned run
    assert max(ctl.store[1]) == 3                                   # one new cycle per claim: no runaway ingestion
    assert sum("heartbeat refused (HTTP 422)" in m for m in log) == 3


def test_the_pause_after_a_run_does_not_apply_to_once_mode_and_does_not_delay_fencing(ctl, clock, n_cycles):
    n_cycles["n"] = 20
    ctl.plan()
    ctl.on_beat = lambda k: setattr(ctl, "lease_lost", True) if k == 2 else None
    code, _ = go(ctl, clock)                                        # once=True: returns right after the run
    assert code == 0 and ctl.finishes == [] and 2.0 not in clock.sleeps   # no extra pause, still fenced (nothing reported)


def test_heartbeat_grace_default_is_one_constant_shared_by_the_cli():
    from pm_simulator.worker import DEFAULT_HEARTBEAT_GRACE

    assert DEFAULT_HEARTBEAT_GRACE == 10.0
    assert cli.build_parser().parse_args(["worker"]).heartbeat_grace == DEFAULT_HEARTBEAT_GRACE
    assert cli.build_parser().parse_args(["worker", "--heartbeat-grace", "7"]).heartbeat_grace == 7.0


# ---- claim errors: transient ones are retried after poll_interval, definitive ones exit cleanly (code 1) -------------------
def no_runaway(ctl, limit=3):
    """Safety net for these tests: a regression must fail fast, not loop forever on the virtual clock."""
    ctl.on_claim = lambda k: pytest.fail(f"claim #{k}: the worker is retrying a definitive failure or looping") if k > limit else None


@pytest.mark.parametrize("status", [500, 501, 408, 429])
def test_transient_http_error_on_claim_is_retried_after_poll_interval_then_the_run_proceeds(ctl, clock, n_cycles, status):
    n_cycles["n"] = 4
    ctl.plan()
    ctl.claim_script = [status]                          # first claim fails transiently, the second succeeds
    seen, pauses = {}, []

    def sleep(s):
        clock.sleep(s)
        if s == 2.0:
            pauses.append(s)
            seen.setdefault("readings_at_first_pause", sum(len(v) for v in ctl.store.values()))
            if len(pauses) == 2:  # 1st = the retry pause, 2nd = the pause after the finished run: end the endless loop
                raise KeyboardInterrupt

    log = []
    code = run_worker(api_for(ctl, clock), worker_id=W, poll_interval=2.0, sleep=sleep, monotonic=clock.monotonic,
                      log=log.append, heartbeat_interval=0, once=False)
    assert code == 130                                   # ended by the test's interrupt, not by a crash or exit 1
    assert ctl.claim_attempts == 2 and seen["readings_at_first_pause"] == 0    # waited poll_interval BEFORE retrying
    assert sorted(ctl.store[1]) == [1, 2, 3, 4] and ctl.finishes == [{"worker_id": W, "outcome": "completed"}]
    assert any(f"claim failed (HTTP {status})" in m and "will retry in 2s" in m for m in log)
    assert not any("ERROR" in m for m in log)


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_other_4xx_on_claim_is_definitive_and_exits_1_without_a_traceback(ctl, clock, status):
    ctl.plan()
    ctl.claim_script = [status]
    no_runaway(ctl)
    code, log = go(ctl, clock, once=False)               # would never return if it retried
    assert code == 1
    assert ctl.claim_attempts == 1 and clock.sleeps == []          # not retried, no waiting
    assert len(log) == 1 and log[0].startswith("ERROR:") and f"HTTP {status}" in log[0]
    assert "retrying will not help" in log[0] and "Traceback" not in log[0] and KEY not in log[0]
    assert ctl.posts == [] and ctl.finishes == []


def test_cli_worker_reports_a_refused_claim_and_exits_1(ctl, cli_env):
    ctl.claim_script = [403]
    no_runaway(ctl)
    code, out = run_cli(["worker"], cli_env)             # no --once: it must stop by itself
    assert code == 1 and "ERROR:" in out and "HTTP 403" in out and "Traceback" not in out
    assert ctl.claim_attempts == 1


def test_http_500_and_429_during_reconnection_are_retried_and_the_run_resumes(ctl, clock, n_cycles):
    n_cycles["n"] = 10
    ctl.plan()

    def outage(k):
        if k == 2:
            ctl.fail_queue = [503] * 4                   # the next reading fails all 4 client attempts -> reconnect path
            ctl.claim_script = [500, 429]                # ... and the first two re-claims are refused transiently

    ctl.on_beat = outage
    code, log = go(ctl, clock, poll_interval=3.0)        # 3.0 appears nowhere else in the timeline
    assert code == 0 and sorted(ctl.store[1]) == list(range(1, 11))
    assert ctl.finishes == [{"worker_id": W, "outcome": "completed"}]
    assert ctl.claim_attempts == 1 + 3                   # initial claim + 500 + 429 + the claim that succeeded
    assert clock.sleeps.count(3.0) == 2                  # one poll_interval pause after each transient re-claim failure
    assert any("reconnected, resuming" in m for m in log)
    assert not any("giving up" in m or "ERROR" in m for m in log)


@pytest.mark.parametrize("status,needle", [(401, "API key"), (403, "retrying will not help"), (422, "retrying will not help")])
def test_definitive_claim_refusal_during_reconnection_exits_1_cleanly(ctl, clock, n_cycles, status, needle):
    n_cycles["n"] = 10
    ctl.plan()

    def outage(k):
        if k == 2:
            ctl.fail_queue = [503] * 4
            ctl.claim_script = [status]

    ctl.on_beat = outage
    no_runaway(ctl)
    code, log = go(ctl, clock)
    assert code == 1 and ctl.claim_attempts == 2         # the initial claim + the single refused re-claim
    assert any(m.startswith("ERROR:") and needle in m and KEY not in m for m in log)
    assert not any("Traceback" in m for m in log)
    assert ctl.finishes == [] and max(ctl.store[1]) < 10  # nothing is reported (the server will time the run out)
