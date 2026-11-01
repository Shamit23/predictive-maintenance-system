"""Alert lifecycle, driven by a stub predictor with a controllable RUL (thresholds 60 / 30)."""
from conftest import HEADERS, StubPredictor, post_reading, scalar


class Seq:
    """Feeds consecutive cycles for one engine with a chosen RUL each time."""

    def __init__(self, client, engine_id=1):
        self.c, self.e, self.cycle = client, engine_id, 0

    def step(self, rul):
        self.c.app.state.model.predictor = StubPredictor(rul)
        self.cycle += 1
        r = post_reading(self.c, self.e, self.cycle)
        assert r.status_code == 201, r.text
        return r.json()


def test_normal_rul_creates_no_alert(client, db):
    s = Seq(client)
    out = s.step(100)
    assert out["alert_action"] == "none" and out["active_alert"] is None
    assert scalar(db, "SELECT count(*) FROM alerts") == 0


def test_warning_opens_alert_and_repeats_are_suppressed(client, db):
    s = Seq(client)
    first = s.step(50)
    assert first["alert_action"] == "opened"
    a = first["active_alert"]
    assert a["severity"] == "warning" and a["status"] == "open" and a["predicted_rul"] == 50
    assert a["model_version"] == "test-model-a"  # the registered version, not the stub's own label
    for _ in range(5):
        assert s.step(45)["alert_action"] == "none"
    assert scalar(db, "SELECT count(*) FROM alerts") == 1  # no duplicate per prediction
    assert scalar(db, "SELECT count(*) FROM predictions") == 6


def test_boundaries_60_is_warning_30_is_critical(client, db):
    assert Seq(client, 1).step(60)["active_alert"]["severity"] == "warning"
    assert Seq(client, 2).step(30)["active_alert"]["severity"] == "critical"
    assert Seq(client, 3).step(60.5)["active_alert"] is None


def test_open_alert_escalates_in_place(client, db):
    s = Seq(client)
    s.step(50)
    out = s.step(20)
    assert out["alert_action"] == "escalated" and out["active_alert"]["severity"] == "critical"
    assert scalar(db, "SELECT count(*) FROM alerts") == 1
    assert scalar(db, "SELECT predicted_rul FROM alerts") == 20
    assert s.step(40)["alert_action"] == "none"  # improvement does not downgrade an open alert
    assert scalar(db, "SELECT severity FROM alerts") == "critical"


def test_acknowledged_alert_escalation_opens_new_alert_and_keeps_history(client, db):
    s = Seq(client)
    first = s.step(50)["active_alert"]
    assert client.post(f"/api/v1/alerts/{first['id']}/acknowledge", headers=HEADERS).status_code == 200
    assert s.step(45)["alert_action"] == "none"  # acknowledged + same severity: stays quiet
    out = s.step(10)
    assert out["alert_action"] == "escalated"
    new = out["active_alert"]
    assert new["id"] != first["id"] and new["severity"] == "critical" and new["status"] == "open"
    old = client.get("/api/v1/alerts", params={"status": "resolved"}).json()["items"]
    assert [(a["id"], a["resolution_reason"]) for a in old] == [(first["id"], "escalated")]
    assert old[0]["acknowledged_at"] is not None  # acknowledgement history preserved


def test_recovery_resolves_alert_and_new_problem_opens_new_one(client, db):
    s = Seq(client)
    a1 = s.step(25)["active_alert"]
    out = s.step(150)
    assert out["alert_action"] == "resolved" and out["active_alert"] is None
    assert scalar(db, "SELECT status FROM alerts WHERE id=:i", i=a1["id"]) == "resolved"
    assert scalar(db, "SELECT resolution_reason FROM alerts WHERE id=:i", i=a1["id"]) == "recovered"
    assert s.step(55)["alert_action"] == "opened"
    assert scalar(db, "SELECT count(*) FROM alerts") == 2


def test_acknowledge_requires_api_key_and_persists(client, db):
    a = Seq(client).step(20)["active_alert"]
    url = f"/api/v1/alerts/{a['id']}/acknowledge"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"X-API-Key": "nope-nope-nope-nope-nope"}).status_code == 401
    assert scalar(db, "SELECT status FROM alerts") == "open"
    r = client.post(url, headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "acknowledged" and r.json()["acknowledged_at"]
    assert scalar(db, "SELECT status FROM alerts") == "acknowledged"  # persisted in the database
    first_ts = r.json()["acknowledged_at"]
    again = client.post(url, headers=HEADERS)  # idempotent
    assert again.status_code == 200 and again.json()["acknowledged_at"] == first_ts


def test_acknowledge_unknown_and_resolved_alerts(client, db):
    assert client.post("/api/v1/alerts/999/acknowledge", headers=HEADERS).status_code == 404
    s = Seq(client)
    a = s.step(20)["active_alert"]
    s.step(200)  # resolves
    r = client.post(f"/api/v1/alerts/{a['id']}/acknowledge", headers=HEADERS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "alert_resolved"


def test_alert_listing_filters(client, db):
    Seq(client, 1).step(50)  # warning
    Seq(client, 2).step(10)  # critical
    Seq(client, 3).step(200)  # none
    api = lambda **p: client.get("/api/v1/alerts", params=p).json()  # noqa: E731
    assert api()["total"] == 2
    assert [a["engine_id"] for a in api(severity="critical")["items"]] == [2]
    assert [a["engine_id"] for a in api(engine_id=1)["items"]] == [1]
    assert api(status="resolved")["total"] == 0
    assert api(status="unresolved")["total"] == 2
    assert client.get("/api/v1/alerts", params={"severity": "bogus"}).status_code == 422
