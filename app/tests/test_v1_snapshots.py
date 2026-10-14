"""Batch V1 (design §39): VM snapshots with libvirt, the API, and automatic ones before a deploy."""
import subprocess

import pytest

from labape import appsettings
from labape.db import session_factory
from labape.engine import power, snapshots
from labape.models import Environment, Job, KvmHost, VmSnapshot

from test_api import _admin_session, _host


class FakeVirsh:
    """virsh against an imaginary host: domains and their internal snapshots."""

    def __init__(self, vms, uefi=()):
        self.vms, self.uefi, self.snaps, self.calls, self.fail = set(vms), set(uefi), {}, [], set()

    def __call__(self, uri, *args, timeout=60):
        self.calls.append(args)
        cmd, ok = args[0], lambda out="": subprocess.CompletedProcess(args, 0, out, "")
        err = lambda m: subprocess.CompletedProcess(args, 1, "", m)  # noqa: E731
        if cmd in self.fail:
            return err(f"error: {cmd} failed")
        vm = args[args.index("--domain") + 1] if "--domain" in args else args[1]
        if vm not in self.vms:
            return err(f"error: failed to get domain '{vm}'")
        s = self.snaps.setdefault(vm, [])
        if cmd == "domstate":
            return ok("running")
        if cmd == "dumpxml":
            return ok("<domain><os><loader type='pflash'/></os></domain>" if vm in self.uefi else "<domain/>")
        if cmd == "snapshot-create-as":
            s.append(args[args.index("--name") + 1])
            return ok()
        if cmd == "snapshot-list":
            return ok("\n".join(s) + "\n")
        if cmd == "snapshot-current":
            return ok(s[-1]) if s else err("no current")
        if cmd == "snapshot-dumpxml":
            return ok(f"<domainsnapshot><name>{args[2]}</name><state>running</state><creationTime>1791950000</creationTime>"
                      "<description>from libvirt</description><domain><description>the VM's own</description></domain>"
                      "</domainsnapshot>")
        if cmd in ("snapshot-revert", "snapshot-delete"):
            name = args[args.index("--snapshotname") + 1]
            if name not in s:
                return err(f"error: snapshot {name} not found")
            if cmd == "snapshot-delete":
                s.remove(name)
            return ok()
        return err(f"unexpected {args}")


def _env(client, status="deployed"):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = Environment(name="lab9", kvm_host_id=1, created_by="admin", status=status,
                        spec={"host_groups": [{"name": "web", "os": "rocky9", "count": 2, "prefix": "web"}]})
        db.add(e)
        db.commit()
        return e.id


def test_snapshot_jobs_and_listing(client, monkeypatch):
    eid = _env(client)
    v = FakeVirsh(["web1", "web2"])
    monkeypatch.setattr(power, "virsh", v)
    vm = snapshots.env_vms(session_factory()().get(Environment, eid))[0]
    assert client.get(f"/api/environments/{eid}/vms/{vm}/snapshots").json()["snapshots"] == []
    assert client.post(f"/api/environments/{eid}/vms/{vm}/snapshots", json={"name": "bad name"}).status_code == 422
    r = client.post(f"/api/environments/{eid}/vms/{vm}/snapshots", json={"name": "before-patch", "description": "KB123"})
    assert r.status_code == 200
    assert client.post(f"/api/environments/{eid}/vms/{vm}/snapshots", json={"name": "x"}).status_code == 409   # busy
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        job.log_path = str(__import__("tempfile").mkdtemp()) + "/j.log"
        assert snapshots.run_job(db, job) == 0
        job.state = "succeeded"
        db.commit()
    v.snaps[vm].append("made-with-virsh")                    # one taken outside LABaPe shows up on the next read
    lst = client.get(f"/api/environments/{eid}/vms/{vm}/snapshots").json()
    assert [x["name"] for x in lst["snapshots"]] == ["before-patch"]
    s = lst["snapshots"][0]
    assert lst["supported"] and lst["can_operate"] and s["name"] == "before-patch" and s["current"]
    assert s["description"] == "KB123" and s["created_by"] and s["state"] == "running" and s["created"].startswith("2026")
    for action, path, method in (("revert", "/before-patch/revert", "post"), ("delete", "/before-patch", "delete")):
        r = getattr(client, method)(f"/api/environments/{eid}/vms/{vm}/snapshots{path}")
        with session_factory()() as db:
            job = db.get(Job, r.json()["job_id"])
            assert job.params["action"] == action
            job.log_path = str(__import__("tempfile").mkdtemp()) + "/j.log"
            assert snapshots.run_job(db, job) == 0
            job.state = "succeeded"
            db.commit()
    with session_factory()() as db:
        assert [x.name for x in db.query(VmSnapshot)] == ["made-with-virsh"] and v.snaps[vm] == ["made-with-virsh"]


def test_uefi_vms_are_refused_with_the_reason(client, monkeypatch):
    eid = _env(client)
    v = FakeVirsh(["web1", "web2"], uefi=["web1"])
    v.snaps["web1"] = ["made-with-virsh"]                     # not LABaPe's: dropped, the VM can't have them
    monkeypatch.setattr(power, "virsh", v)
    m = client.get(f"/api/environments/{eid}/vms/web1/snapshots").json()
    assert m["synced_at"] is None and m["supported"] is True              # not read yet
    r = client.post(f"/api/environments/{eid}/vms/web1/snapshots/refresh")
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        job.log_path = str(__import__("tempfile").mkdtemp()) + "/j.log"
        assert snapshots.run_job(db, job) == 0 and "UEFI" in open(job.log_path).read()
    m = client.get(f"/api/environments/{eid}/vms/web1/snapshots").json()
    assert m["supported"] is False and "UEFI" in m["reason"] and m["synced_at"]
    with pytest.raises(snapshots.SnapshotError, match="UEFI"):
        snapshots.create("qemu:///system", "web1", "s1")


def test_automatic_snapshots_before_a_deploy_keep_the_newest(client, monkeypatch):
    eid = _env(client)
    v = FakeVirsh(["web1"], uefi=[])                       # web2 doesn't exist yet: skipped
    logs = []
    with session_factory()() as db:
        env, host = db.get(Environment, eid), db.get(KvmHost, 1)
        snapshots.before_deploy(db, Job(id=1, type="environment.deploy"), env, host, logs.append, run=v)
        assert v.snaps == {}                                  # off by default
        appsettings.put(db, "vm_snapshots", appsettings.VmSnapshotSettings(auto_before_deploy=True, keep_auto=2))
        for jid in (11, 12, 13):
            snapshots.before_deploy(db, Job(id=jid, type="environment.deploy"), env, host, logs.append, run=v)
        assert v.snaps["web1"] == ["auto-before-job-12", "auto-before-job-13"]
        assert [s.name for s in db.query(VmSnapshot).order_by(VmSnapshot.id)] == ["auto-before-job-12", "auto-before-job-13"]
        assert any("removed the older automatic snapshot auto-before-job-11" in m for m in logs)
        v.fail.add("snapshot-create-as")
        with pytest.raises(snapshots.SnapshotError):         # a failed safety snapshot stops the deploy
            snapshots.before_deploy(db, Job(id=14, type="environment.deploy"), env, host, logs.append, run=v)
