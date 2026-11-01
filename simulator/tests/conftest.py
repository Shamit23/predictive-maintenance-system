"""Test doubles. FakeBackend mimics the documented ingestion contract (docs/api.md) in memory:
201 created / 200 identical duplicate / 409 conflicting_duplicate / 409 cycle_gap with expected_cycle / 401,
plus fault injection. No network, no database."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest
from pm_ml.data import DEFAULT_RAW_DIR

from pm_simulator.replay import VALUE_COLS, ApiClient, EngineSource

KEY = "unit-test-key-0123456789"
T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)
HAVE_DATA = (DEFAULT_RAW_DIR / "train_FD001.txt").is_file() and (DEFAULT_RAW_DIR / "test_FD001.txt").is_file()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="FD001 files not in ml/data/raw")


def make_frame(unit: int = 1, n: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + unit)
    d = {"unit": unit, "cycle": np.arange(1, n + 1)}
    for c in VALUE_COLS:
        d[c] = np.round(rng.normal(size=n), 6)
    return pd.DataFrame(d)


def make_src(scenario: str = "test", unit: int = 1, n: int = 6, offset: int = 0, ref: int | None = None) -> EngineSource:
    return EngineSource.from_frame(scenario, unit, unit + offset, make_frame(unit, n), ref)


def envelope(code: str, message: str = "m", details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


class FakeBackend:
    def __init__(self, key: str = KEY):
        self.key = key
        self.store: dict[int, dict[int, dict]] = {}
        self.posts: list[dict] = []          # every POST body that reached the handler (incl. rejected ones)
        self.attempts = 0                    # POST attempts incl. injected failures
        self.fail_queue: list = []           # statuses (int) or exceptions returned/raised before normal handling
        self.gap_override = None             # callable(body) -> details dict | None, forces a cycle_gap response
        self.ready_status = 200
        self.paths: list[tuple[str, str]] = []

    def client(self, key: str = KEY, sleeps: list | None = None, **kw) -> ApiClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler), base_url="http://fake.test")
        rec = sleeps if sleeps is not None else []
        return ApiClient(http, key, sleep=rec.append, **kw)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append((request.method, request.url.path))
        path = request.url.path
        if path == "/ready":
            return httpx.Response(self.ready_status, json={
                "status": "ready" if self.ready_status == 200 else "not_ready",
                "checks": {"database": "ok", "model": "ok" if self.ready_status == 200 else "unavailable"}})
        if path.startswith("/api/v1/engines/"):
            eid = int(path.rsplit("/", 1)[1])
            if eid not in self.store:
                return httpx.Response(404, json=envelope("not_found"))
            return httpx.Response(200, json={"id": eid, "last_cycle": max(self.store[eid])})
        if path == "/api/v1/alerts":
            return httpx.Response(200, json={"items": [], "total": 0})
        assert path == "/api/v1/readings" and request.method == "POST", path
        body = json.loads(request.content)
        self.attempts += 1
        if self.fail_queue:
            f = self.fail_queue.pop(0)
            if isinstance(f, Exception):
                raise f
            return httpx.Response(f, json=envelope("injected"))
        if request.headers.get("X-API-Key") != self.key:
            return httpx.Response(401, json=envelope("unauthorized"))
        self.posts.append(body)
        eid, cycle = body["engine_id"], body["cycle"]
        values = {k: v for k, v in body.items() if k in VALUE_COLS}
        if self.gap_override and (d := self.gap_override(body)) is not None:
            return httpx.Response(409, json=envelope("cycle_gap", details=d))
        eng = self.store.setdefault(eid, {})
        if cycle in eng:
            if eng[cycle] != values:
                return httpx.Response(409, json=envelope("conflicting_duplicate"))
            return httpx.Response(200, json=self._ok("duplicate", eid, cycle))
        expected = max(eng, default=0) + 1
        if cycle != expected:
            if not eng:
                self.store.pop(eid)
            return httpx.Response(409, json=envelope("cycle_gap", details={"expected_cycle": expected}))
        eng[cycle] = values
        return httpx.Response(201, json=self._ok("created", eid, cycle))

    @staticmethod
    def _ok(status: str, eid: int, cycle: int) -> dict:
        return {"status": status, "engine_id": eid, "cycle": cycle, "prediction_status": "ok",
                "prediction": {"predicted_rul": 100.0 - cycle, "risk_category": "normal"},
                "alert_action": "none", "active_alert": None}


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend()
