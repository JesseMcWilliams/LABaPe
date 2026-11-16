"""Batch T2 (plan: Planning_SMART-Self-Test-Schedule.md): saving a schedule applies it on the host through a setup job
that runs only the self-test step, and the monitoring tick brings a host whose smartd.conf differs back in line."""
import datetime as dt
import subprocess
from types import SimpleNamespace

from labape.db import session_factory
from labape.engine import hostadmin, hostio, hostsetup, selftests
from labape.models import HostFacts, Job, KvmHost, utcnow

from test_api import _admin_session
from test_host_setup import _make_host

def _host(db, name):
    h = KvmHost(name=name, libvirt_uri="qemu+ssh://labape@10.0.0.9/system", vm_storage_path="/v", template_storage_path="/t",
                bridge="br0", ssh_user="localadmin01", ssh_address="10.0.0.9", access_status="ready")
    db.add(h)
    db.commit()
    return h


EXPR = "S/../../7/02|L/../01/./03"


def _facts(db, host, scan=EXPR, devices=None):
    db.add(HostFacts(kvm_host_id=host.id, smart={
        "available": True,
        "disks": [{"device": "/dev/sda", "model": "WD", "serial": "S1", "power_on_hours": 5000, "tests": {}},
                  {"device": "/dev/sdb", "model": "USB", "serial": "S2", "power_on_hours": 100, "tests": {}}],
        "smartd": {"conf": "/etc/smartd.conf", "scan": f"({scan})" if scan else None, "devices": devices or {}, "active": True,
                   "tz": "MST", "now": "2026-11-13T09:00:00", "conf_mtime": "2026-11-12T10:00:00"}}))
    db.commit()


def _setup_jobs(db):
    return db.query(Job).filter(Job.type == "host.setup").order_by(Job.id).all()


def test_saving_a_host_override_stores_it_and_queues_only_the_self_test_step(client):
    _admin_session(client)
    with session_factory()() as db:
        h = _make_host(db)
        host_id = h.id
    r = client.put(f"/api/admin/selftests/hosts/{host_id}", json={
        "mode": "custom", "short_weekday": 3, "short_hour": 4, "long_on": False, "skip": ["/dev/sdb", "/dev/sdb"], "warn": False})
    assert r.status_code == 200 and r.json()["queued"] is True
    with session_factory()() as db:
        job = _setup_jobs(db)[0]
        assert job.params["steps"] == ["Prereq.SmartTests"] and job.requested_by and job.kvm_host_id == host_id
        eff = selftests.effective(db, host_id)
        assert selftests.expression(eff) == "S/../../3/04" and eff["skip"] == ["/dev/sdb"] and eff["warn"] is False
    again = client.put(f"/api/admin/selftests/hosts/{host_id}", json={"mode": "off"}).json()
    assert again["queued"] is False and "applies on the host" in again["note"]          # a job is already queued: saved anyway
    assert client.put(f"/api/admin/selftests/hosts/{host_id}", json={"skip": ["sdb"]}).status_code == 422
    assert client.put(f"/api/admin/selftests/hosts/{host_id}", json={"short_weekday": 9}).status_code == 422
    assert client.put("/api/admin/selftests/hosts/99999", json={}).status_code == 404


def test_the_default_reaches_only_hosts_that_follow_it(client):
    _admin_session(client)
    with session_factory()() as db:
        a, b, c = _host(db, "pi1"), _host(db, "kvm2"), _host(db, "kvm3")
        ids = (a.id, b.id, c.id)
    client.put(f"/api/admin/selftests/hosts/{ids[1]}", json={"mode": "off"})
    client.put(f"/api/admin/selftests/hosts/{ids[2]}", json={"mode": "custom", "short_hour": 6})
    with session_factory()() as db:
        for j in _setup_jobs(db):
            j.state = "succeeded"
        db.commit()
    r = client.put("/api/admin/selftests/default", json={"short_hour": 5})
    assert r.status_code == 200 and r.json()["queued"] == ["pi1"]
    with session_factory()() as db:
        fresh = [j for j in _setup_jobs(db) if j.state == "queued"]
        assert [j.kvm_host_id for j in fresh] == [ids[0]]
        assert selftests.expression(selftests.effective(db, ids[0])).startswith("S/../../7/05")
        assert selftests.expression(selftests.effective(db, ids[2])).startswith("S/../../7/06")          # custom keeps its own hour
    assert client.put("/api/admin/selftests/default", json={"long_day": 31}).status_code == 422


def test_the_fleet_view_shows_the_policy_what_the_host_has_and_each_drive(client):
    _admin_session(client)
    with session_factory()() as db:
        h = _make_host(db)
        _facts(db, h, devices={"/dev/sdb": None})
        host_id = h.id
    fleet = client.get("/api/admin/selftests").json()
    assert fleet["default"]["short_weekday"] == 7
    e = next(x for x in fleet["hosts"] if x["id"] == host_id)
    assert e["described"] == "short: every Sunday at 02:00, long: the 1st of the month at 03:00" and e["in_sync"] is True
    assert e["next"]["short"] == "2026-11-15T02:00" and e["smartd"]["tz"] == "MST" and e["smartd"]["active"] is True
    sda, sdb = e["drives"]
    assert sda["left_out"] is False and "scheduled" in sda["warnings"][0]
    assert sdb["left_out"] is True and sdb["warnings"] == []                    # the host's file leaves it out
    one = client.get(f"/api/admin/selftests/hosts/{host_id}").json()
    assert one["override"] == {} and one["policy"]["mode"] == "default"
    client.post("/api/auth/logout")
    assert client.get("/api/admin/selftests").status_code in (401, 403)


def test_the_tick_reapplies_a_schedule_the_host_does_not_have(client):
    _admin_session(client)
    now = utcnow()
    with session_factory()() as db:
        h = _make_host(db)
        _facts(db, h, scan="S/../../1/01")                                    # the host has another schedule
        selftests.reconcile(db, now)
        selftests.reconcile(db, now + dt.timedelta(hours=1))                  # one job queued, none waiting twice
        jobs = _setup_jobs(db)
        assert len(jobs) == 1 and jobs[0].params["steps"] == ["Prereq.SmartTests"] and jobs[0].requested_by == "system"
        jobs[0].state = "failed"
        db.commit()
        selftests.reconcile(db, now + dt.timedelta(hours=1))                  # tried an hour ago: wait
        assert len(_setup_jobs(db)) == 1
        selftests.reconcile(db, now + dt.timedelta(hours=7))
        assert len(_setup_jobs(db)) == 2


def test_the_tick_leaves_a_host_that_matches_or_has_not_been_read(client):
    now = utcnow()
    with session_factory()() as db:
        h = _make_host(db)
        selftests.reconcile(db, now)                                           # no facts yet
        _facts(db, h)                                                          # matches the default
        selftests.reconcile(db, now)
        assert _setup_jobs(db) == []


def test_a_run_of_one_step_sends_the_schedule_and_leaves_the_revision_alone(client, tmp_path, monkeypatch):
    seen = {"runs": [], "popen": []}

    class FakeSsh:
        def base(self):
            return ["ssh", "pi1"]

        def run(self, command, input=None, timeout=60):
            seen["runs"].append((command, input))
            return subprocess.CompletedProcess(command, 0, b"", b"")

    class FakeTar:
        stdout = SimpleNamespace(close=lambda: None)
        returncode = 0

        def wait(self):
            return 0

    class FakeInstaller:
        def poll(self):
            return 0

    def fake_popen(argv, **kw):
        seen["popen"].append(argv)
        return FakeTar() if argv[0] == "tar" else FakeInstaller()

    import labape.config
    (tmp_path / "deploy").mkdir()
    monkeypatch.setattr(labape.config, "get_settings", lambda: SimpleNamespace(engine_dir=tmp_path))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh())
    monkeypatch.setattr(hostadmin, "stack_mail_host", lambda db: "")
    monkeypatch.setattr(hostio, "tool_env", lambda: {})
    monkeypatch.setattr(hostsetup.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b""))
    with session_factory()() as db:
        h = _make_host(db)
        selftests.save_override(db, h.id, {"mode": "custom", "short_weekday": 2, "long_on": False, "skip": ["/dev/sdc"]})
        job = SimpleNamespace(id=9, kvm_host_id=h.id, params={"steps": ["Prereq.SmartTests"]}, log_path=str(tmp_path / "j.log"))
        assert hostsetup.run_setup(db, job, popen=fake_popen) == 0
        st = hostsetup.state(db, h.id)
    installer = seen["popen"][-1][-1]
    assert "--step Prereq.SmartTests" in installer and "Prereq.AlertMail" not in installer
    written = [inp for cmd, inp in seen["runs"] if "answers.setup.json" in cmd][0]
    assert b'"SmartSchedule": "S/../../2/02"' in written and b'"SmartSkip": "/dev/sdc"' in written
    assert st.get("revision") is None and st.get("ok") is None and st["job"] == 9          # not a full setup
