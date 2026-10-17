"""S1 (question 161): checks of the stored secrets, Recoverable yes/no, recovery, alerts."""
import datetime as dt
import io

from labape.db import session_factory
from labape.engine import rotation, secret_health as H
from labape.engine.secret_health import CheckError, Health, NotInUse
from labape.models import Job, SecretPolicy, utcnow

from test_api import _admin_session


def _p(kind="directory_token", hours=720, **kw):
    kw = {"enabled": True, "managed_externally": False, **kw}           # the column defaults, before a flush
    return SecretPolicy(name=f"platform.{kind}", kind=kind, label=kind, interval_hours=hours, **kw)


def test_how_often():
    rot = {"elevated_ref": "authentik-rotator"}                                  # the directory token needs one to rotate
    assert H.check_interval(_p(hours=720, **rot)) == dt.timedelta(hours=180)   # a quarter of 30 days
    assert H.check_interval(_p(hours=72, **rot)) == dt.timedelta(hours=24)     # at least a day
    assert H.check_interval(_p(hours=24, **rot)) is None                       # rotates daily: no check
    assert H.check_interval(_p(hours=24)) == dt.timedelta(days=7)              # a reminder without one: checked weekly
    assert H.check_interval(_p(kind="oidc_client_secret", hours=24, elevated_ref="")) == dt.timedelta(days=7)
    assert H.describe(_p(kind="oidc_client_secret", hours=24, **rot))["check_every_hours"] is None
    assert H.check_interval(_p(kind="smtp_password", hours=168)) == dt.timedelta(days=7)   # not rotated by LABaPe
    assert H.check_interval(_p(hours=720, enabled=False)) == dt.timedelta(days=7)
    assert H.describe(_p(kind="ca_password"))["recoverable"] is False
    assert H.describe(_p(kind="oidc_client_secret"))["recoverable"] is True
    assert H.describe(_p(kind="env_account"))["checkable"] is False


def _run(db, kind, health, monkeypatch):
    monkeypatch.setitem(H.HEALTH, kind, health)
    p = db.query(SecretPolicy).filter_by(kind=kind).first() or _p(kind=kind)
    if p.id is None:
        db.add(p)
        db.commit()
    log = io.StringIO()
    return H.run_check(db, p, log), p, log.getvalue()


def test_outcomes(db_reset, monkeypatch):
    with session_factory()() as db:
        status, p, _ = _run(db, "directory_token", Health(True, "x", lambda db, p: "fine"), monkeypatch)
        assert status == "ok" and p.check_error == "fine" and p.next_check_at > utcnow() + dt.timedelta(hours=160)

        def missing(db, p):
            raise NotInUse("not set up")
        assert _run(db, "directory_token", Health(True, "x", missing), monkeypatch)[0] == "none"

        def broken(db, p):
            raise CheckError("refused")
        status, p, _ = _run(db, "ca_password", Health(False, "restore it from a backup", broken), monkeypatch)
        assert status == "failed" and "Not recoverable" in p.check_error and "backup" in p.check_error
        assert "secret:platform.ca_password" in H.alerts(db)                 # monitoring raises it
        assert p.next_check_at < utcnow() + dt.timedelta(hours=2)            # tried again soon

        state = {"good": False}

        def flaky(db, p):
            if not state["good"]:
                raise CheckError("refused")
            return "works again"

        def fix(db, p, log):
            state["good"] = True
            return "an earlier value"
        status, p, log = _run(db, "vault_password", Health(True, "earlier values", flaky, fix), monkeypatch)
        assert status == "recovered" and "an earlier value" in p.check_error and "recovering" in log

        def no_fix(db, p, log):
            raise CheckError("none of them works")
        state["good"] = False
        status, p, _ = _run(db, "vault_password", Health(True, "earlier values", flaky, no_fix), monkeypatch)
        assert status == "failed" and "recovery failed" in p.check_error


def test_recovery_by_rotation(db_reset, monkeypatch):
    calls = []
    monkeypatch.setitem(rotation.REGISTRY, "directory_token",
                        rotation.Rotator(kind="directory_token", label="t", run=lambda db, p, log: calls.append(1) or "new"))
    state = {"n": 0}

    def check(db, p):
        state["n"] += 1
        if state["n"] == 1:
            raise CheckError("refused")
        return "accepted"
    with session_factory()() as db:
        status, p, _ = _run(db, "directory_token", Health(True, "rotate", check), monkeypatch)
        assert status == "recovered" and calls == [1] and p.last_rotated_at is not None


def test_scheduling_and_the_endpoint(client, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        rotation.ensure_policies(db)
        p = db.query(SecretPolicy).filter_by(kind="directory_token").one()
        assert H.due(db) == [] and p.next_check_at is not None              # the first check is spread out
        p.next_check_at = utcnow() - dt.timedelta(minutes=1)
        db.commit()
        jobs = H.schedule(db)
        assert len(jobs) == 1 and db.get(Job, jobs[0]).type == "secret.check"
        assert H.schedule(db) == []                                          # not twice
        pid = p.id
        smtp = db.query(SecretPolicy).filter_by(kind="smtp_password").one().id
    r = client.post(f"/api/admin/secrets/{pid}/check")
    assert r.status_code == 409                                              # the scheduled one is queued
    with session_factory()() as db:
        for j in db.query(Job).filter_by(type="secret.check"):
            j.state = "succeeded"
        db.commit()
    r = client.post(f"/api/admin/secrets/{pid}/check")
    assert r.status_code == 200 and r.json()["job_id"]
    row = next(x for x in client.get("/api/admin/secrets").json()["policies"] if x["id"] == smtp)
    assert row["recoverable"] is False and row["check_every_hours"] == 168 and row["check_status"] == "unchecked"
