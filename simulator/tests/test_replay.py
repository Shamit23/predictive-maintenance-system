import httpx
import pytest
from pm_ml.data import load_fd001

from conftest import KEY, T0, VALUE_COLS, envelope, make_src, needs_data
from pm_simulator.replay import (
    ApiClient, AuthError, ConflictError, ReconcileError, RejectedError, ReplayError, ServerUnavailable, build_plan,
    format_event, format_summary, load_api_key, run_replay, safe_url,
)


def go(fake, plan, client=None, **kw):
    events = []
    kw.setdefault("interval", 0)
    kw.setdefault("now", lambda: T0)
    res = run_replay(plan, client or fake.client(), on_event=events.append, **kw)
    return res, events


# ---- payload fidelity & ordering -------------------------------------------------------------
def test_bodies_contain_exact_source_values_and_only_allowed_fields(fake):
    src = make_src(n=5)
    go(fake, [src])
    assert [b["cycle"] for b in fake.posts] == [1, 2, 3, 4, 5]
    for b in fake.posts:
        assert set(b) == {"engine_id", "cycle", "event_time", *VALUE_COLS}
        assert {k: b[k] for k in VALUE_COLS} == src.values[b["cycle"] - 1]  # exact floats, nothing derived
        assert b["event_time"] == T0.isoformat() and b["event_time"].endswith("+00:00")  # UTC send time only
    assert not any("rul" in k.lower() for b in fake.posts for k in b)  # no label ever leaves the simulator


def test_replay_is_deterministic_across_id_offsets(fake):
    go(fake, [make_src(offset=0)])
    go(fake, [make_src(offset=500)])
    strip = lambda b: {k: v for k, v in b.items() if k not in ("engine_id", "event_time")}  # noqa: E731
    a = [strip(b) for b in fake.posts if b["engine_id"] == 1]
    b = [strip(b) for b in fake.posts if b["engine_id"] == 501]
    assert a == b and len(a) == 6


def test_engines_are_interleaved_each_tick_in_cycle_order(fake):
    go(fake, [make_src(unit=1, n=3), make_src(unit=2, n=3)])
    assert [(b["engine_id"], b["cycle"]) for b in fake.posts] == [(1, 1), (2, 1), (1, 2), (2, 2), (1, 3), (2, 3)]


def test_interval_sleeps_between_ticks_but_not_after_the_last(fake):
    slept = []
    run_replay([make_src(n=3)], fake.client(), interval=2.5, sleep=slept.append, now=lambda: T0)
    assert slept == [2.5, 2.5]
    slept.clear()
    run_replay([make_src(unit=2, n=3)], fake.client(), interval=0, sleep=slept.append, now=lambda: T0)
    assert slept == []


def test_max_cycles_limits_this_run_and_next_run_resumes(fake):
    src = make_src(n=6)
    res, _ = go(fake, [src], max_cycles=2)
    assert max(fake.store[1]) == 2 and not res.engines[1].complete
    res, ev = go(fake, [src])
    assert res.engines[1].resumed_after == 2 and res.engines[1].complete
    assert [e.cycle for e in ev] == [3, 4, 5, 6]
    assert max(fake.store[1]) == 6


# ---- resume, duplicates, collisions ------------------------------------------------------------
def test_rerun_of_a_complete_engine_sends_only_the_verification_handshake(fake):
    src = make_src(n=4)
    go(fake, [src])
    fake.posts.clear()
    res, ev = go(fake, [src])
    assert ev == [] and res.engines[1].complete
    assert [b["cycle"] for b in fake.posts] == [4]  # re-sent last stored cycle only, to verify identity


def test_server_duplicates_are_counted_and_do_not_stop_the_replay(fake):
    src = make_src(n=4)
    fake.store[1] = {c: src.values[c - 1] for c in (1, 2)}
    fake.gap_override = None
    res, ev = go(fake, [src])
    assert res.engines[1].created == 2 and res.engines[1].complete
    fake.store[1].pop(4)  # server loses cycle 4 -> only 4 is re-sent
    res, ev = go(fake, [src])
    assert [e.cycle for e in ev] == [4] and ev[0].outcome == "created"


def test_id_collision_with_different_data_is_detected_by_handshake(fake):
    src = make_src(n=6)
    other = make_src(unit=9, n=6)  # different values
    fake.store[1] = {c: other.values[c - 1] for c in (1, 2, 3)}
    with pytest.raises(ConflictError, match="--id-offset"):
        go(fake, [src])
    assert len(fake.posts) == 1  # stopped at the handshake


def test_server_holding_more_cycles_than_the_source_is_not_reconcilable(fake):
    fake.store[1] = {c: {} for c in range(1, 9)}
    with pytest.raises(ReconcileError, match="--id-offset"):
        go(fake, [make_src(n=6)])
    assert fake.posts == []


# ---- cycle gaps: bounded and validated --------------------------------------------------------
def test_gap_after_server_state_loss_rewinds_once_and_completes(fake):
    src = make_src(n=8)

    fired = []

    def lose_state(ev):
        if ev.cycle == 4 and not fired:  # one-shot: simulate a single server-side state loss
            fired.append(1)
            fake.store[1] = {c: v for c, v in fake.store[1].items() if c < 3}  # server now ends at cycle 2

    notes = []
    res = run_replay([src], fake.client(), interval=0, now=lambda: T0, on_event=lose_state, on_note=notes.append)
    assert res.engines[1].rewinds == 1 and res.engines[1].complete
    assert sorted(fake.store[1]) == list(range(1, 9))
    assert any("rewound to cycle 3" in n for n in notes)


@pytest.mark.parametrize("details", [
    {"expected_cycle": 999}, {"expected_cycle": 0}, {"expected_cycle": -1}, {"expected_cycle": "3"},
    {"expected_cycle": True}, {"expected_cycle": None}, {}, {"expected_cycle": 2}, {"expected_cycle": 5},
])
def test_invalid_expected_cycle_stops_immediately_without_looping(fake, details):
    fake.gap_override = lambda body: details if body["cycle"] >= 2 else None  # cycle 2 is rejected with a bad hint
    with pytest.raises(ReconcileError):
        go(fake, [make_src(n=6)])
    assert [b["cycle"] for b in fake.posts] == [1, 2]  # no resend after the invalid hint


def test_repeated_gaps_are_bounded_by_max_rewinds(fake):
    fake.gap_override = lambda body: {"expected_cycle": body["cycle"] - 1} if body["cycle"] >= 2 else None
    with pytest.raises(ReconcileError, match="rewinds"):
        go(fake, [make_src(n=10)], max_rewinds=3)
    assert len(fake.posts) <= 12  # terminates; no infinite loop


# ---- errors: auth, validation, retries --------------------------------------------------------
def test_wrong_api_key_stops_with_auth_error_that_never_contains_the_key(fake):
    secret = "wrong-secret-key-9999999"
    with pytest.raises(AuthError) as ei:
        go(fake, [make_src()], client=fake.client(key=secret))
    assert secret not in str(ei.value) and KEY not in str(ei.value) and secret not in repr(fake.client(key=secret))


def test_validation_rejection_is_fatal_and_not_retried(fake):
    fake.fail_queue = [422]
    with pytest.raises(RejectedError, match="422"):
        go(fake, [make_src()])
    assert fake.attempts == 1


def test_transient_errors_are_retried_with_exponential_backoff(fake):
    fake.fail_queue = [503, httpx.ConnectError("boom"), 502]
    sleeps = []
    res, _ = go(fake, [make_src(n=2)], client=fake.client(sleeps=sleeps))
    assert res.engines[1].complete and sleeps == [0.5, 1.0, 2.0]


def test_retries_are_bounded_then_server_unavailable(fake):
    fake.fail_queue = [503] * 10
    with pytest.raises(ServerUnavailable, match="4 attempts"):
        go(fake, [make_src()], client=fake.client(max_retries=3))
    assert fake.attempts == 4


def test_not_ready_backend_is_reported_with_failing_checks(fake):
    fake.ready_status = 503
    with pytest.raises(ReplayError, match="model=unavailable"):
        fake.client().check_ready()


# ---- interruption -----------------------------------------------------------------------------
def test_keyboard_interrupt_ends_cleanly_and_a_rerun_resumes(fake):
    src = make_src(n=6)

    def interrupt(_s):
        raise KeyboardInterrupt

    res = run_replay([src], fake.client(), interval=1, sleep=interrupt, now=lambda: T0)
    assert res.interrupted and max(fake.store[1]) == 1 and not res.engines[1].complete
    assert "INTERRUPTED" in format_summary(res)
    res2, ev = go(fake, [src])
    assert not res2.interrupted and res2.engines[1].complete and [e.cycle for e in ev][0] == 2


# ---- ground truth labelling -------------------------------------------------------------------
def test_train_scenario_shows_simulated_truth_test_scenario_never_does(fake):
    train = make_src("train", unit=1, n=5, offset=1000)
    test = make_src("test", unit=2, n=5, ref=20)
    res, ev = go(fake, [train, test])
    t_train = [e.true_rul for e in ev if e.source_unit == 1]
    t_test = [e.true_rul for e in ev if e.source_unit == 2]
    assert t_train == [4, 3, 2, 1, 0] and t_test == [None] * 5
    assert all("SIMULATED GROUND TRUTH" in format_event(e) for e in ev if e.source_unit == 1)
    assert not any("GROUND TRUTH" in format_event(e) or "20" in format_event(e).split("|")[-1] for e in ev
                   if e.source_unit == 2)
    summary = format_summary(res)
    assert "EVALUATION ONLY" in summary and "never sent to the API" in summary and "= 20" in summary


def test_test_reference_rul_is_withheld_until_the_whole_sequence_was_replayed(fake):
    src = make_src("test", n=5, ref=20)
    res, _ = go(fake, [src], max_cycles=3)
    assert "EVALUATION ONLY" not in format_summary(res)


# ---- configuration helpers --------------------------------------------------------------------
def test_safe_url_strips_credentials_path_and_query():
    assert safe_url("http://user:hunter2@example.test:8000/x?y=1") == "http://example.test:8000"
    assert "hunter2" not in safe_url("https://user:hunter2@example.test/")


def test_load_api_key_prefers_env_then_file_and_errors_without_leaking(tmp_path):
    f = tmp_path / "e.env"
    f.write_text("# c\nOTHER=1\nAPI_KEY='file-key-0123456789abc'\n")
    assert load_api_key({"API_KEY": "env-key"}, f) == "env-key"
    assert load_api_key({}, f) == "file-key-0123456789abc"
    f.write_text("OTHER=secretvalue\n")
    with pytest.raises(ReplayError) as ei:
        load_api_key({}, f)
    assert "secretvalue" not in str(ei.value)
    with pytest.raises(ReplayError):
        load_api_key({}, tmp_path / "missing.env")


# ---- plan building with the real dataset ------------------------------------------------------
@needs_data
def test_plan_uses_documented_offsets_and_exact_source_rows():
    train, test, rul = load_fd001()
    (t,) = build_plan("test", [24])
    (r,) = build_plan("train", [24])
    assert (t.engine_id, r.engine_id) == (24, 1024) and t.scenario == "test" and r.scenario == "train"
    assert build_plan("train", [24], id_offset=5000)[0].engine_id == 5024
    rows = test[test["unit"] == 24].sort_values("cycle")
    assert t.n_cycles == len(rows) == 186 and t.reference_rul == int(rul[24]) == 20
    for i in (0, 85, 185):
        assert t.values[i] == {c: float(rows.iloc[i][c]) for c in VALUE_COLS}
    assert t.true_rul(1) is None and r.reference_rul is None
    assert r.true_rul(r.n_cycles) == 0 and r.true_rul(1) == r.n_cycles - 1  # train engines run to failure


@needs_data
@pytest.mark.parametrize("args,match", [
    (("test", [101]), "no engine"), (("bogus", [1]), "unknown scenario"), (("test", [1, 1]), "duplicates"),
    (("test", []), "non-empty"), (("test", [1], -1), "offset"), (("test", [100], 1_000_000), "maximum"),
])
def test_plan_rejects_bad_input(args, match):
    with pytest.raises(ReplayError, match=match):
        build_plan(*args)


def test_missing_dataset_gives_actionable_error(tmp_path):
    with pytest.raises(ReplayError, match="data.nasa.gov"):
        build_plan("test", [1], raw_dir=tmp_path)
