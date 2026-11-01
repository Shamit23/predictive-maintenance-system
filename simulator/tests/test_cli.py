import httpx
import pytest

import pm_simulator.__main__ as cli
from conftest import KEY, T0, make_src
from pm_simulator.__main__ import main, parse_units


@pytest.mark.parametrize("spec,expected", [("24", [24]), ("1,5,7", [1, 5, 7]), ("1-3,24", [1, 2, 3, 24])])
def test_parse_units(spec, expected):
    assert parse_units(spec) == expected


@pytest.mark.parametrize("spec", ["", "a", "0", "3-1", "1,1", "1,,2", "-5"])
def test_parse_units_rejects_bad_input(spec):
    with pytest.raises(ValueError):
        parse_units(spec)


@pytest.fixture
def patched(fake, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setattr(cli, "build_plan", lambda scenario, units, offset, **kw: [make_src(scenario, u, 4, (offset or 0)) for u in units])
    factory = lambda url, t: httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://fake.test")  # noqa: E731
    return fake, factory


def run_cli(argv, factory, **kw):
    lines = []
    code = main(argv, http_factory=factory, out=lines.append, **kw)
    return code, "\n".join(lines)


def test_replay_runs_and_labels_the_data_as_simulated(patched):
    fake, factory = patched
    code, out = run_cli(["replay", "--scenario", "test", "--engines", "1,2", "--interval", "0"], factory)
    assert code == 0
    assert "SIMULATED" in out and "not real aircraft" in out and "unit 1 -> backend engine id 1" in out
    assert sorted(fake.store) == [1, 2] and len(fake.store[1]) == 4
    assert KEY not in out


def test_default_and_custom_offsets_reach_the_plan(patched):
    fake, factory = patched
    run_cli(["replay", "--scenario", "train", "--engines", "3", "--interval", "0"], factory)
    run_cli(["replay", "--scenario", "train", "--engines", "3", "--id-offset", "7000", "--interval", "0"], factory)
    assert sorted(fake.store) == [3, 7003]  # stub plan adds (offset or 0); real defaults are tested in test_replay


def test_dry_run_sends_nothing_and_needs_no_api_key(patched, monkeypatch):
    fake, _ = patched
    monkeypatch.delenv("API_KEY", raising=False)

    def boom(*a):
        raise AssertionError("no HTTP client may be created in a dry run")

    code, out = run_cli(["replay", "--scenario", "test", "--engines", "1", "--dry-run"], boom)
    assert code == 0 and "nothing was sent" in out and fake.paths == []


def test_wrong_key_exits_1_without_echoing_the_key(patched, monkeypatch, capsys):
    fake, factory = patched
    monkeypatch.setenv("API_KEY", "definitely-wrong-key-123456")
    code, out = run_cli(["replay", "--scenario", "test", "--engines", "1", "--interval", "0"], factory)
    err = capsys.readouterr().err
    assert code == 1 and "HTTP 401" in err and "definitely-wrong-key-123456" not in err + out


def test_credentials_in_the_api_url_are_never_printed(patched):
    _, factory = patched
    code, out = run_cli(["replay", "--scenario", "test", "--engines", "1", "--interval", "0", "--dry-run",
                         "--api-url", "http://admin:hunter2@example.test:9/x"], factory)
    assert "hunter2" not in out and "http://example.test:9" in out


def test_interrupt_returns_130(patched):
    fake, factory = patched

    def interrupt(_s):
        raise KeyboardInterrupt

    code, out = run_cli(["replay", "--scenario", "test", "--engines", "1", "--interval", "1"], factory, sleep=interrupt)
    assert code == 130 and "re-run the same command to resume" in out


@pytest.mark.parametrize("argv", [
    ["replay", "--scenario", "test", "--engines", "x"], ["replay", "--scenario", "bad", "--engines", "1"],
    ["replay", "--scenario", "test", "--engines", "1", "--interval", "-1"],
    ["replay", "--scenario", "test", "--engines", "1", "--max-cycles", "0"], [], ["replay"],
])
def test_usage_errors_exit_2(patched, argv):
    _, factory = patched
    assert run_cli(argv, factory)[0] == 2


def test_unreachable_backend_exits_1(patched, fake):
    _, _ = patched
    fake.ready_status = 503
    factory = lambda url, t: httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://fake.test")  # noqa: E731
    code, _ = run_cli(["replay", "--scenario", "test", "--engines", "1", "--interval", "0"], factory,
                      sleep=lambda s: None)
    assert code == 1
