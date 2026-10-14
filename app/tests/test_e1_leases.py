"""Batch E1 (design §40): environment leases: reminders, shut down at the end, destroy after the grace."""
import datetime as dt

import pytest

from labape import appsettings, leases
from labape.db import session_factory
from labape.models import Environment, Job, User

from test_api import _admin_session, _host

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)


def _env(db, **kw):
    db.add(User(provider="oidc", subject="o", username="owner1", display_name="O", email="o@example.invalid", groups=[]))
    e = Environment(name="lab9", kvm_host_id=1, created_by="owner1", status="deployed",
                    spec={"host_groups": [{"name": "web", "os": "rocky9", "count": 1, "prefix": "web"}]}, **kw)
    db.add(e)
    db.commit()
    return e


def _on(db, **kw):
    appsettings.put(db, "leases", appsettings.LeaseSettings(enabled=True, **kw))
    appsettings.put(db, "smtp", appsettings.SmtpSettings(enabled=True, host="smtp.example.invalid",
                                                         from_address="labape@example.invalid"))


def test_the_lease_reminds_stops_then_destroys(client):
    _admin_session(client)
    _host(client)
    mails = []
    send = lambda cfg, pw, to, subject, body, **k: mails.append((to, subject))  # noqa: E731
    with session_factory()() as db:
        e = _env(db, expires_at=NOW + dt.timedelta(days=10))
        assert leases.tick(db, NOW, send) == []                         # off by default
        _on(db, remind_days=[7, 1], grace_days=7)
        assert leases.tick(db, NOW, send) == []                         # 10 days left: nothing yet
        assert leases.tick(db, NOW + dt.timedelta(days=4), send) == ["reminded the owners of lab9"]
        assert leases.tick(db, NOW + dt.timedelta(days=4, hours=1), send) == []        # once per reminder
        assert leases.tick(db, NOW + dt.timedelta(days=9, hours=12), send) == ["reminded the owners of lab9"]
        assert mails[0] == (["o@example.invalid"], "LABaPe: lab9 expires in 6 day(s)")
        assert "12 hour(s)" in mails[1][1]
        done = leases.tick(db, NOW + dt.timedelta(days=10, minutes=1), send)
        job = db.query(Job).order_by(Job.id.desc()).first()
        assert done == [f"shut down lab9 (job {job.id})"] and job.type == "environment.power"
        assert job.params["action"] == "shutdown" and job.params["force_after_timeout"] and job.requested_by == "lease"
        assert leases.tick(db, NOW + dt.timedelta(days=11), send) == []     # the shutdown job is still queued
        job.state = "succeeded"
        db.commit()
        assert leases.tick(db, NOW + dt.timedelta(days=12), send) == []     # inside the grace period
        done = leases.tick(db, NOW + dt.timedelta(days=17, minutes=1), send)
        job = db.query(Job).order_by(Job.id.desc()).first()
        assert done == [f"destroying lab9 (job {job.id})"] and job.type == "environment.destroy"
        assert db.get(Environment, e.id).status == "destroying" and "destroyed" in mails[-1][1]


def test_keep_after_the_grace_and_extending_starts_over(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        _on(db, after_grace="keep", grace_days=0, max_days=60)
        e = _env(db, expires_at=NOW - dt.timedelta(days=1))
        leases.tick(db, NOW, lambda *a, **k: None)
        db.query(Job).update({"state": "succeeded"})
        db.commit()
        assert leases.tick(db, NOW + dt.timedelta(days=30), lambda *a, **k: None) == []   # kept, shut down
        assert leases.phase(e, db, NOW) == "ended"
        with pytest.raises(leases.LeaseError, match="at most 60 days"):
            leases.set_lease(db, e, days=90, now=NOW)
        leases.set_lease(db, e, days=20, now=NOW)
        assert e.expires_at == NOW + dt.timedelta(days=20) and e.lease_state == {}
        leases.set_lease(db, e, days=20, now=NOW)                          # extends from the current end
        assert e.expires_at == NOW + dt.timedelta(days=40)
        with pytest.raises(leases.LeaseError, match="Only admins"):
            leases.set_lease(db, e, clear=True, now=NOW)
        leases.set_lease(db, e, clear=True, admin=True, now=NOW)
        assert e.expires_at is None and leases.phase(e, db, NOW) == "none"


def test_new_environments_get_the_default_and_the_api(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        assert leases.default_expiry(db, NOW) is None
        _on(db, default_days=14)
        assert leases.default_expiry(db, NOW) == NOW + dt.timedelta(days=14)
        e = _env(db)
        eid = e.id
    assert client.get(f"/api/environments/{eid}").json()["lease"]["phase"] == "none"
    r = client.post(f"/api/environments/{eid}/lease", json={"days": 10})
    assert r.status_code == 200 and r.json()["phase"] in ("active", "expiring") and r.json()["destroy_at"]
    assert client.post(f"/api/environments/{eid}/lease", json={"days": 0}).status_code == 422
    assert client.post(f"/api/environments/{eid}/lease", json={"clear": True}).json()["phase"] == "none"   # admin
    assert client.put("/api/admin/settings/leases", json={"enabled": True, "remind_days": [0]}).status_code == 422


def test_deploying_a_destroyed_environment_starts_a_new_lease(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        _on(db, default_days=5)
        e = _env(db, expires_at=NOW - dt.timedelta(days=30), lease_state={"stopped_at": "x", "destroy_job": 1})
        e.status = "destroyed"
        db.commit()
        eid = e.id
    client.post(f"/api/environments/{eid}/deploy")
    with session_factory()() as db:
        e = db.get(Environment, eid)
        assert e.lease_state == {} and leases._aware(e.expires_at) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)
