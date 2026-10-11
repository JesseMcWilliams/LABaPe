"""Maintenance mode: writes refused, reads and the allowed paths open, scheduling, windows."""
import datetime as dt

from labape import maintenance_mode
from labape.api.maintenance import next_window
from labape.db import session_factory

from test_api import _admin_session


def test_writes_stop_reads_go_on(client):
    _admin_session(client)
    r = client.put("/api/admin/maintenance", json={"active": True, "reason": ""})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "reason"
    r = client.put("/api/admin/maintenance", json={"active": True, "reason": "upgrading to v0.3.0"})
    assert r.status_code == 200 and r.json()["active"] and r.json()["source"] == "admin" and r.json()["started_by"]
    maintenance_mode._cache = None
    r = client.post("/api/hosts", json={"name": "kvm9"})
    assert r.status_code == 503 and "upgrading to v0.3.0" in r.json()["detail"] and r.json()["maintenance"]["active"]
    assert client.get("/api/hosts").status_code == 200                       # reads go on
    assert client.get("/api/health").json() == {"ok": True, "maintenance": True}
    assert client.get("/api/maintenance").json()["reason"] == "upgrading to v0.3.0"
    assert client.post("/api/errors/report", json={"page": "/x", "fields": []}).status_code != 503   # allowed
    r = client.put("/api/admin/maintenance", json={"active": False})        # allowed: ending maintenance
    assert r.status_code == 200 and not r.json()["active"]
    maintenance_mode._cache = None
    assert client.post("/api/hosts", json={"name": "kvm9"}).status_code == 201
    actions = [a["action"] for a in client.get("/api/admin/audit").json()]
    assert "maintenance.on" in actions and "maintenance.off" in actions


def test_scheduled_start(client):
    _admin_session(client)
    start = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
    r = client.put("/api/admin/maintenance", json={"active": True, "reason": "patching", "scheduled_start": start})
    assert r.status_code == 200 and not r.json()["active"] and r.json()["scheduled_start"]
    with session_factory()() as db:
        assert not maintenance_mode.start_if_due(db)
        assert maintenance_mode.start_if_due(db, dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
        assert maintenance_mode.get(db)["active"]
        maintenance_mode.turn_off(db)


def test_allowed_paths():
    for path in ("/api/auth/logout", "/api/admin/maintenance", "/api/jobs/12/cancel", "/api/engine/certificates"):
        assert maintenance_mode.allowed(path), path
    for path in ("/api/hosts", "/api/environments", "/api/jobs/12/retry", "/api/admin/settings/smtp"):
        assert not maintenance_mode.allowed(path), path


def test_window(client):
    _admin_session(client)
    with session_factory()() as db:
        assert next_window(db) is None                                        # off by default
    s = client.get("/api/admin/settings").json()["maintenance_window"]
    client.put("/api/admin/settings/maintenance_window",
               json={**s, "enabled": True, "weekday": 6, "time_of_day": "02:00", "duration_minutes": 120, "heads_up_hours": 24})
    from labape import appsettings
    appsettings._cache.clear()
    saturday = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)      # a Saturday
    with session_factory()() as db:
        w = next_window(db, saturday)
        assert w["start"].startswith("2026-10-11T02:00") and w["heads_up"] and not w["in_window"]
        w = next_window(db, saturday + dt.timedelta(hours=15))               # Sunday 03:00, inside it
        assert w["in_window"] and w["start"].startswith("2026-10-11T02:00")
        w = next_window(db, saturday + dt.timedelta(hours=17))               # after it: next week
        assert w["start"].startswith("2026-10-18T02:00") and not w["heads_up"]
