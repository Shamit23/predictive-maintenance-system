"""Replay NASA C-MAPSS FD001 readings through the public ingestion API, one cycle at a time.

This is a SIMULATION of a sensor stream built from a historical dataset; it is not a connection to real aircraft.

Guarantees
  * Readings are sent in cycle order per engine, values exactly as in the source file (never generated,
    interpolated or altered). The only value added is `event_time` = UTC send time, as event metadata.
  * Every request is idempotent on the server, so retries and re-runs are safe. On start each engine resumes
    from the server's `last_cycle + 1`, after a handshake that re-sends the last stored cycle and requires the
    server to report it as an identical duplicate (this detects engine-ID collisions with other data).
  * All retrying is bounded: transport/502/503/504 errors (`max_retries`), cycle-gap rewinds (`max_rewinds`,
    each rewind must move strictly backwards and `expected_cycle` is validated against the source).
  * The API key is only ever sent as the `X-API-Key` header and never appears in messages, reprs or output.

Ground truth
  * `train` scenario: engines run to failure, so the true RUL at each cycle (last_cycle - cycle) is known and
    shown, labelled SIMULATED GROUND TRUTH.
  * `test` scenario: engines are cut before failure; no per-cycle truth exists. `RUL_FD001.txt` gives the RUL
    after the last observed cycle; it is shown once, only after the full observed sequence was replayed, and
    labelled EVALUATION ONLY. It is never sent to the API.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx
import pandas as pd
from pm_ml.data import DEFAULT_RAW_DIR, SENSOR_COLS, SETTING_COLS, DatasetError, load_fd001

VALUE_COLS: list[str] = [*SETTING_COLS, *SENSOR_COLS]
DEFAULT_OFFSETS = {"test": 0, "train": 1000}
MAX_ENGINE_ID = 1_000_000  # backend limit (ReadingIn.engine_id)
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---- errors -----------------------------------------------------------------------------------
class ReplayError(Exception):
    """Base class. Messages never contain the API key or full response bodies."""


class AuthError(ReplayError):
    pass


class ConflictError(ReplayError):
    pass


class RejectedError(ReplayError):
    pass


class ReconcileError(ReplayError):
    """Server state and source data cannot be reconciled; the replay stops instead of looping."""


class ServerUnavailable(ReplayError):
    pass


class _Gap(Exception):
    def __init__(self, details: dict):
        self.details = details


# ---- configuration helpers --------------------------------------------------------------------
def safe_url(url: str) -> str:
    """scheme://host[:port] only: drops any user:password@ part, path and query."""
    p = urlsplit(url)
    host = p.hostname or "?"
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme or 'http'}://{host}{port}"


def load_api_key(env: dict | None = None, env_file: Path | None = None) -> str:
    """API_KEY from the environment, else from `API_KEY=` in the repo-root .env. Never printed."""
    env = os.environ if env is None else env
    value = (env.get("API_KEY") or "").strip()
    if value:
        return value
    path = Path(env_file) if env_file else REPO_ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "API_KEY" and v.strip().strip("'\""):
                return v.strip().strip("'\"")
    raise ReplayError("API_KEY not found: set the API_KEY environment variable or add API_KEY to the .env file.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---- source data ------------------------------------------------------------------------------
@dataclass(frozen=True)
class EngineSource:
    scenario: str
    source_unit: int
    engine_id: int
    values: tuple[dict, ...]          # values[i] = readings of cycle i+1, exactly as in the source file
    reference_rul: int | None = None  # test scenario only: RUL after the last observed cycle (evaluation only)

    @property
    def n_cycles(self) -> int:
        return len(self.values)

    def true_rul(self, cycle: int) -> int | None:
        """Simulated ground truth (train scenario only; None for test engines)."""
        return self.n_cycles - cycle if self.scenario == "train" else None

    def body(self, cycle: int, event_time: datetime) -> dict:
        if not 1 <= cycle <= self.n_cycles:
            raise ValueError(f"cycle {cycle} outside 1..{self.n_cycles}")
        return {"engine_id": self.engine_id, "cycle": cycle, "event_time": event_time.isoformat(),
                **self.values[cycle - 1]}

    @classmethod
    def from_frame(cls, scenario: str, unit: int, engine_id: int, frame: pd.DataFrame,
                   reference_rul: int | None = None) -> "EngineSource":
        f = frame.sort_values("cycle")
        if list(f["cycle"]) != list(range(1, len(f) + 1)):
            raise ReplayError(f"unit {unit}: cycles must be consecutive from 1")
        vals = tuple({c: float(r[c]) for c in VALUE_COLS} for r in f[VALUE_COLS].to_dict("records"))
        return cls(scenario, int(unit), int(engine_id), vals, reference_rul)


def build_plan(scenario: str, units: list[int], id_offset: int | None = None,
               raw_dir: str | Path = DEFAULT_RAW_DIR) -> list[EngineSource]:
    if scenario not in DEFAULT_OFFSETS:
        raise ReplayError(f"unknown scenario '{scenario}' (choose test or train)")
    offset = DEFAULT_OFFSETS[scenario] if id_offset is None else id_offset
    if offset < 0:
        raise ReplayError("--id-offset must be >= 0")
    if not units or len(set(units)) != len(units):
        raise ReplayError("engine list must be non-empty and contain no duplicates")
    try:
        train, test, rul = load_fd001(raw_dir)
    except DatasetError as e:
        raise ReplayError(str(e)) from e
    frame = train if scenario == "train" else test
    missing = sorted(set(units) - set(frame["unit"].unique()))
    if missing:
        raise ReplayError(f"{scenario} data has no engine(s) {missing} (valid: 1..100)")
    if max(units) + offset > MAX_ENGINE_ID:
        raise ReplayError(f"engine id {max(units) + offset} exceeds the backend maximum {MAX_ENGINE_ID}")
    return [
        EngineSource.from_frame(scenario, u, u + offset, frame[frame["unit"] == u],
                                int(rul[u]) if scenario == "test" else None)
        for u in units
    ]


# ---- API client -------------------------------------------------------------------------------
class ApiClient:
    """Thin wrapper over httpx.Client (injectable for tests) adding the API key and bounded retries."""

    RETRY_STATUS = (502, 503, 504)

    def __init__(self, http: httpx.Client, api_key: str, *, max_retries: int = 3, backoff: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self._http, self._key = http, api_key
        self.max_retries, self.backoff, self._sleep = max_retries, backoff, sleep

    def __repr__(self) -> str:  # never include the key
        return f"ApiClient(base={safe_url(str(self._http.base_url))})"

    def _request(self, method: str, path: str, *, retry_status: bool = True, **kw) -> httpx.Response:
        last = "no response"
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.request(method, path, **kw)
                if not retry_status or r.status_code not in self.RETRY_STATUS:
                    return r
                last = f"HTTP {r.status_code}"
            except httpx.TransportError as e:
                last = type(e).__name__
            if attempt < self.max_retries:
                self._sleep(self.backoff * 2 ** attempt)
        raise ServerUnavailable(f"backend unavailable after {self.max_retries + 1} attempts ({last})")

    def check_ready(self) -> None:
        # A 503 here is an answer (it names the failing check), so only transport errors are retried.
        r = self._request("GET", "/ready", retry_status=False)
        if r.status_code != 200:
            checks = _json(r).get("checks", {})
            raise ReplayError("backend is not ready: " + ", ".join(f"{k}={v}" for k, v in sorted(checks.items())))

    def last_cycle(self, engine_id: int) -> int:
        r = self._request("GET", f"/api/v1/engines/{engine_id}")
        if r.status_code == 404:
            return 0
        if r.status_code != 200:
            raise RejectedError(f"could not read engine {engine_id} (HTTP {r.status_code})")
        return int(r.json()["last_cycle"])

    def post_reading(self, body: dict) -> httpx.Response:
        return self._request("POST", "/api/v1/readings", json=body, headers={"X-API-Key": self._key})

    def unresolved_alerts(self, engine_id: int) -> list[dict] | None:
        try:
            r = self._request("GET", "/api/v1/alerts",
                              params={"engine_id": engine_id, "status": "unresolved", "limit": 5})
        except ReplayError:
            return None
        return r.json().get("items", []) if r.status_code == 200 else None


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _error(r: httpx.Response) -> tuple[str, str, dict]:
    e = _json(r).get("error", {})
    e = e if isinstance(e, dict) else {}
    details = e.get("details", {})
    return str(e.get("code", "unknown")), str(e.get("message", ""))[:200], details if isinstance(details, dict) else {}


# ---- replay -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    engine_id: int
    source_unit: int
    cycle: int
    n_cycles: int
    outcome: str                     # created | duplicate
    prediction_status: str
    prediction: dict | None
    alert_action: str
    active_alert: dict | None
    true_rul: int | None             # simulated ground truth (train scenario only)


@dataclass
class EngineStats:
    created: int = 0
    duplicates: int = 0
    rewinds: int = 0
    resumed_after: int = 0           # server's last_cycle when this run started
    complete: bool = False           # every source cycle is stored on the server
    last_event: Event | None = None
    unresolved_alerts: list[dict] | None = None


@dataclass
class ReplayResult:
    plan: list[EngineSource]
    engines: dict[int, EngineStats]
    interrupted: bool = False


@dataclass
class _Run:
    src: EngineSource
    stats: EngineStats
    next_cycle: int
    processed: int = 0
    done: bool = False


def _send(client: ApiClient, src: EngineSource, cycle: int, now: Callable[[], datetime]):
    """POST one cycle. Returns (status_code, response_json). Raises typed errors; _Gap on a cycle gap."""
    r = client.post_reading(src.body(cycle, now()))
    if r.status_code in (200, 201):
        return r.status_code, _json(r)
    code, message, details = _error(r)
    if r.status_code == 401:
        raise AuthError("the API rejected the API key (HTTP 401): check API_KEY")
    if r.status_code == 409 and code == "cycle_gap":
        raise _Gap(details)
    if r.status_code == 409 and code == "conflicting_duplicate":
        raise ConflictError(
            f"engine {src.engine_id} cycle {cycle}: the server already stores DIFFERENT values for it. "
            f"This engine id holds other data; pick another with --id-offset.")
    raise RejectedError(f"engine {src.engine_id} cycle {cycle}: HTTP {r.status_code} {code}: {message}")


def _start(client: ApiClient, src: EngineSource, stats: EngineStats, now) -> _Run:
    last = client.last_cycle(src.engine_id)
    stats.resumed_after = last
    if last > src.n_cycles:
        raise ReconcileError(
            f"engine {src.engine_id} already has {last} cycles stored but the source (unit {src.source_unit}, "
            f"{src.scenario}) has only {src.n_cycles}. This id holds other data; pick another with --id-offset.")
    if last > 0:  # handshake: the last stored cycle must be identical to our source row
        try:
            status, _ = _send(client, src, last, now)
        except _Gap as g:
            raise ReconcileError(f"engine {src.engine_id}: server reported last_cycle={last} but then reported a "
                                 f"cycle gap while verifying it (details={g.details})") from None
        if status != 200:
            raise ReconcileError(f"engine {src.engine_id}: server reported last_cycle={last} but accepted cycle "
                                 f"{last} as new; server state is inconsistent")
    run = _Run(src, stats, last + 1)
    if run.next_cycle > src.n_cycles:
        run.done = stats.complete = True
    return run


def _step(client, run: _Run, now, max_rewinds: int, max_cycles: int | None) -> Event | None:
    src, st = run.src, run.stats
    cycle = run.next_cycle
    try:
        status, data = _send(client, src, cycle, now)
    except _Gap as g:
        exp = g.details.get("expected_cycle")
        if not isinstance(exp, int) or isinstance(exp, bool) or not 1 <= exp <= src.n_cycles or exp >= cycle:
            raise ReconcileError(
                f"engine {src.engine_id}: the server expects cycle {exp!r} but the source has cycles "
                f"1..{src.n_cycles} and cycle {cycle} was just sent; cannot reconcile, stopping.") from None
        st.rewinds += 1
        if st.rewinds > max_rewinds:
            raise ReconcileError(f"engine {src.engine_id}: more than {max_rewinds} cycle-gap rewinds; the server "
                                 f"state keeps changing or disagrees with the source, stopping.") from None
        run.next_cycle = exp
        return None
    created = status == 201
    st.created += created
    st.duplicates += not created
    ev = Event(src.engine_id, src.source_unit, cycle, src.n_cycles, "created" if created else "duplicate",
               data.get("prediction_status", "?"), data.get("prediction"), data.get("alert_action", "none"),
               data.get("active_alert"), src.true_rul(cycle))
    st.last_event = ev
    run.processed += 1
    run.next_cycle = cycle + 1
    if run.next_cycle > src.n_cycles:
        run.done = st.complete = True
    elif max_cycles is not None and run.processed >= max_cycles:
        run.done = True
    return ev


def run_replay(plan: list[EngineSource], client: ApiClient, *, interval: float = 1.0, max_cycles: int | None = None,
               max_rewinds: int = 3, now: Callable[[], datetime] = utc_now,
               sleep: Callable[[float], None] = time.sleep,
               on_event: Callable[[Event], None] | None = None,
               on_note: Callable[[str], None] | None = None) -> ReplayResult:
    """Replay `plan`. Each tick sends the next cycle of every unfinished engine, then waits `interval` seconds."""
    result = ReplayResult(plan, {s.engine_id: EngineStats() for s in plan})
    note = on_note or (lambda _m: None)
    try:
        runs = []
        for s in plan:
            r = _start(client, s, result.engines[s.engine_id], now)
            note(f"engine {s.engine_id}: server has {r.stats.resumed_after}/{s.n_cycles} cycles"
                 + (" - already complete" if r.done else f" - resuming at cycle {r.next_cycle}"))
            runs.append(r)
        while any(not r.done for r in runs):
            for r in runs:
                if r.done:
                    continue
                rewinds_before = r.stats.rewinds
                ev = _step(client, r, now, max_rewinds, max_cycles)
                if ev is None and r.stats.rewinds > rewinds_before:
                    note(f"engine {r.src.engine_id}: cycle gap reported by server, rewound to cycle {r.next_cycle}")
                elif ev is not None and on_event:
                    on_event(ev)
            if interval > 0 and any(not r.done for r in runs):
                sleep(interval)
    except KeyboardInterrupt:
        result.interrupted = True
    for s in plan:
        result.engines[s.engine_id].unresolved_alerts = client.unresolved_alerts(s.engine_id)
    return result


# ---- presentation -----------------------------------------------------------------------------
def format_event(e: Event) -> str:
    head = f"[engine {e.engine_id} <- unit {e.source_unit}] cycle {e.cycle}/{e.n_cycles} {e.outcome}"
    if e.prediction:
        head += f" | RUL {e.prediction['predicted_rul']:.1f} risk={e.prediction['risk_category']}"
    else:
        head += f" | no prediction ({e.prediction_status})"
    if e.alert_action != "none":
        head += f" | alert {e.alert_action}"
    if e.active_alert:
        head += f" [{e.active_alert['severity']}/{e.active_alert['status']}]"
    if e.true_rul is not None:
        head += f" | SIMULATED GROUND TRUTH RUL={e.true_rul} (train scenario)"
    return head


def format_summary(result: ReplayResult) -> str:
    lines = ["", "Summary (simulated replay of NASA C-MAPSS FD001; not real aircraft data)"]
    for s in result.plan:
        st = result.engines[s.engine_id]
        line = (f"  engine {s.engine_id} (unit {s.source_unit}, {s.scenario}): created={st.created} "
                f"duplicates={st.duplicates} rewinds={st.rewinds} complete={st.complete}")
        ev = st.last_event
        if ev and ev.prediction:
            line += f" | last prediction RUL {ev.prediction['predicted_rul']:.1f} at cycle {ev.cycle}"
        if st.unresolved_alerts:
            line += " | unresolved alert: " + ", ".join(f"{a['severity']}/{a['status']}" for a in st.unresolved_alerts)
        lines.append(line)
        if st.complete and s.scenario == "test" and s.reference_rul is not None:
            lines.append(f"      EVALUATION ONLY (RUL_FD001.txt, never sent to the API): true RUL after the last "
                         f"observed cycle = {s.reference_rul}")
        if st.complete and s.scenario == "train":
            lines.append("      train engine ran to failure: true RUL at its last cycle = 0 (simulated ground truth)")
    if result.interrupted:
        lines.append("  INTERRUPTED: progress is saved on the server; re-run the same command to resume.")
    return "\n".join(lines)
