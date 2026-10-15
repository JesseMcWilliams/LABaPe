"""Batch H2 (design §53): OS updates and reboots of a host, as jobs."""
import subprocess

from labape.db import session_factory
from labape.engine import hostpower, runner, stackmon
from labape.models import Job, KvmHost

from test_api import _admin_session, _host


def _ready(host_id):
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        h.access_status, h.ssh_address, h.ssh_user = "ready", "kvm1.lab", "localadmin01"
        db.commit()


class FakeSsh:
    def __init__(self, out=b"", rc=0):
        self.calls, self.out, self.rc = [], out, rc

    def run(self, command, input=None, timeout=60):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.rc, self.out, b"")


def test_pending_updates_are_parsed(monkeypatch):
    fake = FakeSsh(b"manager=apt\nopenssl 3.0.1 -> 3.0.2\nlibc6 2.36 -> 2.37\nreboot_required=yes\n")
    monkeypatch.setattr(hostpower, "for_host", lambda h: fake)
    out = hostpower.pending(KvmHost(name="k"))
    assert out == {"manager": "apt", "packages": ["openssl 3.0.1 -> 3.0.2", "libc6 2.36 -> 2.37"], "reboot_required": True}


def test_update_and_reboot_are_queued_once_and_audited(client, monkeypatch):
    _admin_session(client)
    host_id = _host(client)
    assert client.post(f"/api/hosts/{host_id}/os-update").status_code == 422          # no SSH access yet
    _ready(host_id)
    r = client.post(f"/api/hosts/{host_id}/os-update")
    assert r.status_code == 200
    assert client.post(f"/api/hosts/{host_id}/reboot").status_code == 409           # one at a time
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert job.type == "host.update" and job.kvm_host_id == host_id
    assert "host.update" in [a["action"] for a in client.get("/api/admin/audit").json()]
    monkeypatch.setattr(hostpower, "pending", lambda h: {"manager": "apt", "packages": ["a 1 -> 2"], "reboot_required": False})
    info = client.get(f"/api/hosts/{host_id}/os-updates").json()
    assert info["packages"] == ["a 1 -> 2"] and info["running_jobs"] == 0 and info["is_stack_host"] is False


def test_a_running_reboot_keeps_new_jobs_off_the_host(client):
    _admin_session(client)
    host_id = _host(client, concurrency_limit=3)
    with session_factory()() as db:
        db.add_all([Job(type="host.reboot", requested_by="t", kvm_host_id=host_id, state="running"),
                    Job(type="host.update", requested_by="t", kvm_host_id=host_id, state="queued")])
        db.commit()
        assert runner.claim(db, "w") is None


def test_the_update_job_runs_without_the_host_being_enabled_for_deploys(client):
    _admin_session(client)
    host_id = _host(client, enabled=False)
    with session_factory()() as db:
        db.add(Job(type="host.update", requested_by="t", kvm_host_id=host_id, state="queued"))
        db.commit()
        assert runner.claim(db, "w") is not None


def test_the_reboot_waits_for_the_hosts_other_jobs(client, monkeypatch, tmp_path):
    _admin_session(client)
    host_id = _host(client)
    _ready(host_id)
    fake = FakeSsh()
    monkeypatch.setattr(hostpower, "for_host", lambda h: fake)
    monkeypatch.setattr(stackmon, "is_self", lambda h: True)             # no waiting for the host to return
    monkeypatch.setattr(runner, "beat", lambda job_id: False)
    with session_factory()() as db:
        other = Job(type="environment.deploy", requested_by="t", kvm_host_id=host_id, state="running")
        mine = Job(type="host.reboot", kvm_host_id=host_id, state="running", requested_by="ann",
                   log_path=str(tmp_path / "job.log"))
        db.add_all([other, mine])
        db.commit()
        sleeps = []

        def sleep(n):
            sleeps.append(n)
            if len(sleeps) == 3:                                          # the other job ends during the wait
                with session_factory()() as d2:
                    d2.get(Job, other.id).state = "succeeded"
                    d2.commit()

        assert hostpower.run_reboot(db, mine, sleep=sleep) == 0
    assert len(sleeps) == 3
    assert fake.calls and fake.calls[0].startswith("sudo -n shutdown -r +1") and "ann" in fake.calls[0]
    log = (tmp_path / "job.log").read_text()
    assert "waiting for 1 running job" in log and "offline until the host is back" in log


def test_a_reboot_that_cannot_be_scheduled_fails(client, monkeypatch, tmp_path):
    _admin_session(client)
    host_id = _host(client)
    monkeypatch.setattr(hostpower, "for_host", lambda h: FakeSsh(rc=1))
    with session_factory()() as db:
        mine = Job(type="host.reboot", requested_by="t", kvm_host_id=host_id, state="running", log_path=str(tmp_path / "j.log"))
        db.add(mine)
        db.commit()
        assert hostpower.run_reboot(db, mine, sleep=lambda n: None) == 1
    assert "couldn't schedule the reboot" in (tmp_path / "j.log").read_text()
