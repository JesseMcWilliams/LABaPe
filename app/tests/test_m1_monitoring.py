"""Batch M1 (design §30): host roles, stack host checks, peer stacks, alerts to dashboard, syslog, email."""
import datetime as dt

from labape import appsettings
from labape.db import session_factory
from labape.engine import hostadmin, stackmon
from labape.models import HostFacts, HostMetric, KvmHost, MonitorAlert, StackStatus, utcnow
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)


def _setup(db, role="stack", **kw):
    h = db.get(KvmHost, 1)
    h.role, h.access_status = role, "ready"
    for k, v in kw.items():
        setattr(h, k, v)
    db.merge(HostFacts(kvm_host_id=1, facts={"os": {}, "tools": {"smartctl": True}}, smart={}))
    db.add(HostMetric(kvm_host_id=1, at=NOW, data={"temps": {"cpu": 82.0}, "fs_used_pct": {"/": 87}}))
    db.commit()
    return h


def test_stack_hosts_use_the_monitoring_thresholds(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        _setup(db, role="stack", stack_url="https://pi.lab")
        cur = stackmon.evaluate(db, NOW)
        msgs = {v[2]: v[1] for v in cur.values()}
        assert msgs == {"kvm1: cpu at 82 °C": "warning", "kvm1: / is 87% full": "warning"}
        h = db.get(KvmHost, 1)
        h.role = "kvm"                                                   # a KVM host keeps the host page's levels
        db.commit()
        assert stackmon.evaluate(db, NOW) == {}
        # The key doesn't change with the numbers.
        h.role = "stack"
        db.add(HostMetric(kvm_host_id=1, at=NOW + dt.timedelta(minutes=5), data={"temps": {"cpu": 84.0}, "fs_used_pct": {"/": 88}}))
        db.commit()
        assert set(stackmon.evaluate(db, NOW)) == set(cur)


def test_containers_self_checks_and_peers(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        h = _setup(db, role="both")
        db.query(HostMetric).delete()
        db.add(KvmHost(name="pi", role="stack", stack_url="https://pi.lab", libvirt_uri="qemu+ssh://x@pi/system"))
        db.commit()
        assert stackmon.is_self(h) and not stackmon.is_self(db.query(KvmHost).filter_by(name="pi").one())
        pi = db.query(KvmHost).filter_by(name="pi").one()
        db.merge(StackStatus(kvm_host_id=1, data={
            "containers": [{"name": "labape-api-1", "state": "running", "health": "", "restarts": 3},
                           {"name": "labape-guacd-1", "state": "exited", "health": "", "restarts": 0},
                           {"name": "labape-ca-1", "state": "running", "health": "unhealthy", "restarts": 0}],
            "restart_seen": {"labape-api-1": (NOW - dt.timedelta(minutes=5)).isoformat()},
            "self": {"backup": {"enabled": True, "newest": (NOW - dt.timedelta(hours=30)).isoformat()},
                     "certs": {"web": {"expires": (NOW + dt.timedelta(days=3)).isoformat(),
                                       "issued": (NOW - dt.timedelta(days=87)).isoformat()},
                               "ca intermediate": {"expires": (NOW + dt.timedelta(days=300)).isoformat()}}}}))
        db.merge(StackStatus(kvm_host_id=pi.id, data={"peer": {"ok": False, "failures": 2, "error": "timed out",
                                                                "cert_expires": None}}))
        db.commit()
        msgs = {v[2]: v[1] for v in stackmon.evaluate(db, NOW).values()}
        assert msgs["kvm1: container labape-guacd-1 is exited"] == "error"
        assert msgs["kvm1: container labape-ca-1 is unhealthy"] == "warning"
        assert msgs["kvm1: container labape-api-1 restarted (restart count 3)"] == "warning"
        assert msgs["kvm1: no backup in the last 26 hours"] == "warning"
        assert any(m.startswith("kvm1: the web certificate expires") for m in msgs)
        assert not any("intermediate" in m for m in msgs)
        assert msgs["pi: the stack at https://pi.lab isn't answering (timed out)"] == "error"
        later = stackmon.evaluate(db, NOW + dt.timedelta(minutes=20))       # the restart is old news now
        assert not any("restarted" in v[2] for v in later.values())


def test_alerts_start_update_and_clear_once(client):
    _admin_session(client)
    with session_factory()() as db:
        sent = []
        send = lambda db, s, c: sent.append(([a.message for a in s], [a.message for a in c]))  # noqa: E731
        cur = {"host1:fs:/ is #% full": (None, "warning", "kvm1: / is 87% full")}
        stackmon.sync_alerts(db, cur, NOW, send)
        stackmon.sync_alerts(db, {"host1:fs:/ is #% full": (None, "warning", "kvm1: / is 88% full")}, NOW, send)
        assert sent == [(["kvm1: / is 87% full"], [])]                           # an update isn't announced
        a = db.query(MonitorAlert).one()
        assert a.message == "kvm1: / is 88% full" and a.cleared_at is None
        stackmon.sync_alerts(db, {}, NOW + dt.timedelta(hours=1), send)
        assert sent[-1] == ([], ["kvm1: / is 88% full"])
        assert db.query(MonitorAlert).one().cleared_at is not None


def test_announcements_go_to_syslog_and_email(client, monkeypatch):
    from labape import notify
    _admin_session(client)
    events, mails = [], []
    monkeypatch.setattr(notify, "audit_to_syslog", lambda cfg, ev: events.append(ev))
    monkeypatch.setattr(notify, "send_email", lambda cfg, pw, to, subject, body, **k: mails.append((to, subject, body)))
    with session_factory()() as db:
        appsettings.put(db, "monitoring", appsettings.MonitoringSettings(email_to=["ops@example.invalid"]))
        appsettings.put(db, "smtp", appsettings.SmtpSettings(enabled=True, host="smtp.example.invalid",
                                                             from_address="labape@example.invalid"))
        a = MonitorAlert(key="k", level="error", message="pi: the stack isn't answering", started_at=NOW)
        stackmon.announce(db, [a], [])
    assert events[0]["action"] == "monitor.alert.started" and events[0]["outcome"] == "failed"
    to, subject, body = mails[0]
    assert to == ["ops@example.invalid"] and "1 new alert" in subject and "[ERROR] pi: the stack isn't answering" in body


def test_containers_over_the_access_account(client, monkeypatch):
    _admin_session(client)
    _host(client)
    runs = iter([{"runtime": "docker", "containers": [{"name": "labape-api-1", "state": "running", "restarts": 1, "project": "labape"},
                                                      {"name": "someone-elses", "state": "exited", "restarts": 0, "project": "other"}],
                  "volumes": [{"name": "labape_postgres-data", "size": "1.2GB"}]},
                 {"runtime": "docker", "containers": [{"name": "labape-api-1", "state": "running", "restarts": 2, "project": "labape"}],
                  "volumes": None}])

    class Ssh:
        def python(self, script, args, timeout=0):
            return next(runs)

    monkeypatch.setattr(hostadmin, "for_host", lambda h: Ssh())
    with session_factory()() as db:
        h = _setup(db)
        d = stackmon.collect_containers(db, h, volumes=True, now=NOW)
        assert d["volumes"][0]["size"] == "1.2GB" and not d["restart_seen"]
        assert [c["name"] for c in d["containers"]] == ["labape-api-1"]          # only LABaPe's own
        d = stackmon.collect_containers(db, h, volumes=False, now=NOW + dt.timedelta(minutes=5))
        assert d["restart_seen"]["labape-api-1"].startswith("2026-10-14T12:05") and d["volumes"]   # volumes kept


def test_roles_api_and_views(client):
    _admin_session(client)
    hid = _host(client)
    h = client.get(f"/api/hosts/{hid}").json()
    body = {k: v for k, v in h.items() if k not in ("id", "resources")}
    assert client.put(f"/api/hosts/{hid}", json={**body, "role": "router"}).status_code == 422
    assert client.put(f"/api/hosts/{hid}", json={**body, "stack_url": "http://x"}).status_code == 422
    r = client.put(f"/api/hosts/{hid}", json={**body, "role": "both", "stack_url": "https://kvm1.lab"})
    assert r.status_code == 200 and r.json()["role"] == "both"
    assert client.get(f"/api/hosts/{hid}/admin").json()["stack"]["self"] is False      # labape.local is this stack
    m = client.get("/api/dashboard/monitoring").json()
    assert m["open"] == [] and m["stacks"][0]["name"] == "kvm1"
    client.app.dependency_overrides[current_principal] = lambda: Principal(user_id=9, username="v", display_name="",
                                                                           roles={"viewer"})
    assert client.get("/api/dashboard/monitoring").status_code == 403
    client.app.dependency_overrides.clear()
    bad = client.put("/api/admin/settings/monitoring", json={"email_to": ["not-an-address"]})
    assert bad.status_code == 422


def test_certificate_thresholds_follow_their_lifetime():
    issued, exp = NOW - dt.timedelta(hours=16), NOW + dt.timedelta(hours=8)        # a 24 h web certificate
    c = {"issued": issued.isoformat(), "expires": exp.isoformat()}
    assert stackmon.cert_due(c, NOW, 7) is False                                  # Caddy renews it: fine
    assert stackmon.cert_due(c, NOW + dt.timedelta(hours=3), 7) is True            # under a quarter left: renewal failed
    long = {"issued": (NOW - dt.timedelta(days=80)).isoformat(), "expires": (NOW + dt.timedelta(days=5)).isoformat()}
    assert stackmon.cert_due(long, NOW, 7) is True                                 # a 90-day one inside 7 days
    assert stackmon.cert_due({"expires": (NOW + dt.timedelta(days=30)).isoformat()}, NOW, 7) is False
