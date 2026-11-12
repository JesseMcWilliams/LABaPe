"""Applying a host's alert and disk setup again (question 229): the job runs only the health steps, the monitoring
tick queues it once for a host whose setup predates the installer's steps, and the API starts it by hand."""
import datetime as dt
import subprocess
from types import SimpleNamespace

from labape.db import session_factory
from labape.engine import hostadmin, hostio, hostsetup
from labape.models import Job, KvmHost, Setting, utcnow

from test_api import _admin_session


def _make_host(db, **kw) -> KvmHost:
    h = KvmHost(name="pi1", libvirt_uri="qemu+ssh://labape@10.0.0.9/system", vm_storage_path="/v",
                template_storage_path="/t", bridge="br0", ssh_user="localadmin01", ssh_address="10.0.0.9",
                access_status="ready", **kw)
    db.add(h)
    db.commit()
    return h


def test_setup_runs_only_the_health_steps(client, tmp_path, monkeypatch):
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

    engine = tmp_path / "engine"
    (engine / "deploy" / "labape_deploy").mkdir(parents=True)
    (engine / "deploy" / "install-labape.py").write_text("a")
    (engine / "deploy" / "labape_deploy" / "steps.py").write_text("b")
    import labape.config
    monkeypatch.setattr(labape.config, "get_settings", lambda: SimpleNamespace(engine_dir=engine))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh())
    monkeypatch.setattr(hostadmin, "stack_mail_host", lambda db: "10.0.0.1:1025")
    monkeypatch.setattr(hostio, "tool_env", lambda: {})
    monkeypatch.setattr(hostsetup.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b""))
    with session_factory()() as db:
        h = _make_host(db)
        log = tmp_path / "job.log"
        job = SimpleNamespace(id=7, kvm_host_id=h.id, params={}, log_path=str(log))
        assert hostsetup.run_setup(db, job, popen=fake_popen) == 0
        st = hostsetup.state(db, h.id)
    installer = seen["popen"][-1][-1]
    assert "--instance setup" in installer and "--non-interactive" in installer
    for step in hostsetup.HEALTH_STEPS:
        assert f"--step {step}" in installer
    assert "Prereq.Packages" not in installer and "Net.Bridges" not in installer      # nothing that reshapes the host
    written = [inp for cmd, inp in seen["runs"] if "answers.setup.json" in cmd][0]
    assert b'"AlertMailHost": "10.0.0.1:1025"' in written and b'"ServiceUser": "labape"' in written
    assert st["ok"] is True and st["revision"] == hostsetup.revision()


def test_failed_run_keeps_the_old_revision(client, tmp_path, monkeypatch):
    class FakeSsh:
        def base(self):
            return ["ssh", "pi1"]

        def run(self, command, input=None, timeout=60):
            return subprocess.CompletedProcess(command, 0, b"", b"")

    class FakeTar:
        stdout = SimpleNamespace(close=lambda: None)
        returncode = 0

        def wait(self):
            return 0

    class Failing:
        def poll(self):
            return 3

    import labape.config
    (tmp_path / "deploy").mkdir()
    monkeypatch.setattr(labape.config, "get_settings", lambda: SimpleNamespace(engine_dir=tmp_path))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh())
    monkeypatch.setattr(hostadmin, "stack_mail_host", lambda db: "")
    monkeypatch.setattr(hostio, "tool_env", lambda: {})
    monkeypatch.setattr(hostsetup.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b""))
    with session_factory()() as db:
        h = _make_host(db)
        job = SimpleNamespace(id=8, kvm_host_id=h.id, params={}, log_path=str(tmp_path / "j.log"))
        popen = lambda argv, **kw: FakeTar() if argv[0] == "tar" else Failing()          # noqa: E731
        assert hostsetup.run_setup(db, job, popen=popen) == 3
        st = hostsetup.state(db, h.id)
    assert st["ok"] is False and st["revision"] is None


def test_tick_queues_once_and_waits_after_a_failure(client, monkeypatch):
    monkeypatch.setattr(hostsetup, "revision", lambda: "rev2")
    now = utcnow()
    with session_factory()() as db:
        h = _make_host(db)
        hostsetup.due(db, now)
        hostsetup.due(db, now)                                              # a job is queued: no second one
        jobs = db.query(Job).filter(Job.type == "host.setup").all()
        assert len(jobs) == 1 and jobs[0].requested_by == "system" and jobs[0].kvm_host_id == h.id
        jobs[0].state = "failed"
        db.commit()
        hostsetup.due(db, now + dt.timedelta(hours=1))                      # failed an hour ago: wait
        assert db.query(Job).filter(Job.type == "host.setup").count() == 1
        hostsetup.due(db, now + dt.timedelta(hours=25))
        assert db.query(Job).filter(Job.type == "host.setup").count() == 2


def test_tick_leaves_a_current_host_and_respects_the_switch(client, monkeypatch):
    monkeypatch.setattr(hostsetup, "revision", lambda: "rev2")
    now = utcnow()
    with session_factory()() as db:
        h = _make_host(db)
        db.add(Setting(key=hostsetup.KEY.format(h.id), value={"revision": "rev2", "ok": True}))
        db.commit()
        hostsetup.due(db, now)
        assert db.query(Job).filter(Job.type == "host.setup").count() == 0
        db.get(Setting, hostsetup.KEY.format(h.id)).value = {"revision": "old"}
        db.add(Setting(key=hostsetup.AUTO_KEY, value={"on": False}))
        db.commit()
        hostsetup.due(db, now)
        assert db.query(Job).filter(Job.type == "host.setup").count() == 0      # switched off


def test_tick_skips_a_host_without_ssh_access(client, monkeypatch):
    monkeypatch.setattr(hostsetup, "revision", lambda: "rev2")
    with session_factory()() as db:
        h = _make_host(db)
        h.access_status = "none"
        db.commit()
        hostsetup.due(db, utcnow())
        assert db.query(Job).count() == 0


def test_api_state_and_apply(client):
    _admin_session(client)
    with session_factory()() as db:
        hid = _make_host(db).id
    s = client.get(f"/api/hosts/{hid}/host-setup").json()
    assert s["current"] is False and s["auto"] is True and "Prereq.AlertMail" in s["steps"]
    r = client.post(f"/api/hosts/{hid}/host-setup")
    assert r.status_code == 200
    assert client.post(f"/api/hosts/{hid}/host-setup").status_code == 409          # one at a time
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert job.type == "host.setup" and job.kvm_host_id == hid
