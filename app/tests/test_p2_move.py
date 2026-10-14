"""Batch P2 (design §46): moving a host group's running VMs to another KVM host."""
import subprocess

import pytest

from labape.db import session_factory
from labape.engine import hostio, migrate
from labape.models import Environment, Job, KvmHost

from test_api import _admin_session, _host

XML = """<domain type='kvm'><name>web1</name><devices>
<disk type='file' device='disk'><source file='/data/a/web1.qcow2'/><target dev='vda'/></disk>
<disk type='file' device='cdrom'><source file='/data/a/web1-seed.iso'/><target dev='sda'/></disk>
<interface type='bridge'><source bridge='br1'/><mac address='52:54:00:aa:bb:cc'/></interface>
</devices></domain>"""


def _h(hid, name, uri, vm_dir):
    return KvmHost(id=hid, name=name, libvirt_uri=uri, vm_storage_path=vm_dir, template_storage_path=f"{vm_dir}/t",
                   bridge="br1", concurrency_limit=2, enabled=True, ssh_address=f"10.0.0.{hid}")


def test_the_destination_definition():
    src, dst = _h(1, "kvm1", "qemu:///system", "/data/a"), _h(2, "kvm2", "qemu+ssh://k@10.0.0.2/system", "/vm/b/")
    xml, disks, cdroms = migrate.plan_xml(XML, src, dst, "br-lab")
    assert disks == [("/data/a/web1.qcow2", "/vm/b/web1.qcow2")] and cdroms == ["/data/a/web1-seed.iso"]
    assert "br-lab" in xml and "/vm/b/web1.qcow2" in xml and "web1-seed.iso" not in xml
    assert "52:54:00:aa:bb:cc" in xml                                   # the VM keeps its MAC


class FakeHost:
    ran: list = []

    def __init__(self, uri):
        self.uri = uri

    def prime(self):
        pass

    def run(self, cmd, input=None, timeout=60):
        FakeHost.ran.append((self.uri, cmd))
        out = b'{"virtual-size": 42949672960}' if cmd.startswith("qemu-img info") else b""
        return subprocess.CompletedProcess(cmd, 0, out, b"")


def _virsh(states):
    calls = []

    def run(uri, *args, timeout=120):
        calls.append((uri, args))
        if args[0] == "domstate":
            st = states.get((uri, args[1]))
            return subprocess.CompletedProcess(args, 0 if st else 1, st or "", "" if st else "no domain")
        if args[0] == "dumpxml":
            return subprocess.CompletedProcess(args, 0, XML, "")
        if args[0] == "migrate":
            states[(args[-1].split("?")[0], args[-2])] = "running"
            states.pop(("qemu:///system", args[-2]), None)
        return subprocess.CompletedProcess(args, 0, "", "")
    return run, calls


def test_a_running_vm_moves_and_the_source_files_go(monkeypatch):
    monkeypatch.setattr(hostio, "Host", FakeHost)
    FakeHost.ran = []
    src, dst = _h(1, "kvm1", "qemu:///system", "/data/a"), _h(2, "kvm2", "qemu+ssh://k@10.0.0.2/system", "/vm/b")
    run, calls = _virsh({("qemu:///system", "web1"): "running"})
    log = []
    migrate.move_vm(src, dst, "web1", "br-lab", "labape-workspace=lab--h2", log.append, run)
    mig = next(a for _, a in calls if a[0] == "migrate")
    assert "--live" in mig and "--copy-storage-all" in mig and "--undefinesource" in mig
    assert mig[mig.index("--migrateuri") + 1] == "tcp://10.0.0.2" and mig[mig.index("--disks-uri") + 1] == "tcp://10.0.0.2"
    assert any(a[:2] == ("change-media", "web1") for _, a in calls)              # the seed CD emptied first
    assert any(u.startswith("qemu+ssh") and a[0] == "desc" and a[-1] == "labape-workspace=lab--h2" for u, a in calls)
    created = [c for u, c in FakeHost.ran if "qemu-img create" in c]
    assert created and "/vm/b/web1.qcow2" in created[0] and "42949672960" in created[0]
    removed = [c for u, c in FakeHost.ran if c.startswith("rm -f")]
    assert removed and "/data/a/web1.qcow2" in removed[-1] and "/data/a/web1-seed.iso" in removed[-1]


def test_refusals(monkeypatch):
    monkeypatch.setattr(hostio, "Host", FakeHost)
    src, dst = _h(1, "kvm1", "qemu:///system", "/data/a"), _h(2, "kvm2", "qemu+ssh://k@10.0.0.2/system", "/vm/b")
    run, _ = _virsh({("qemu:///system", "web1"): "shut off"})
    with pytest.raises(migrate.MoveError, match="start it first"):
        migrate.move_vm(src, dst, "web1", "br-lab", "t", print, run)
    run, _ = _virsh({("qemu:///system", "web1"): "running", ("qemu+ssh://k@10.0.0.2/system", "web1"): "running"})
    with pytest.raises(migrate.MoveError, match="already exists"):
        migrate.move_vm(src, dst, "web1", "br-lab", "t", print, run)


def test_the_move_job_names_the_new_host_and_queues_a_deploy(client, monkeypatch):
    _admin_session(client)
    a = _host(client, name="kvm1")
    b = _host(client, name="kvm2")
    r = client.post("/api/environments", json={"name": "movelab", "kvm_host_id": a, "host_groups": [
        {"name": "web", "os": "rocky9", "roles": ["linux_server"]}]})
    assert r.status_code == 201, r.text
    env_id = r.json()["environment"]["id"]
    assert client.post(f"/api/environments/{env_id}/move", json={"group": "web", "kvm_host_id": b}).status_code == 409
    with session_factory()() as db:
        for j in db.query(Job).filter_by(environment_id=env_id):
            j.state = "succeeded"
        db.get(Environment, env_id).status = "deployed"
        db.commit()
    assert client.post(f"/api/environments/{env_id}/move", json={"group": "web", "kvm_host_id": a}).status_code == 422
    r = client.post(f"/api/environments/{env_id}/move", json={"group": "web", "kvm_host_id": b})
    assert r.status_code == 200, r.text
    moved = []
    monkeypatch.setattr(migrate, "move_vm", lambda src, dst, vm, bridge, tag, log, run: moved.append((vm, bridge, tag)))
    monkeypatch.setattr(migrate, "virsh", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    import labape.engine.runner as runner
    monkeypatch.setattr(runner, "_append", lambda job, msg: None)
    monkeypatch.setattr(runner, "beat", lambda job_id: True)
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert migrate.run_job(db, job, run=migrate.virsh) == 0
        e = db.get(Environment, env_id)
        assert e.spec["host_groups"][0]["kvm_host_id"] == b and e.status == "deploying"
        assert moved == [("web1", "br1", "labape-workspace=movelab--h2")]
        assert db.query(Job).filter_by(environment_id=env_id, type="environment.deploy", state="queued").count() == 1
