"""Batch J: remote KVM hosts over SSH (hostio), remote template sync, job command rewriting."""
import json
from pathlib import Path

from labape.db import session_factory
from labape.engine import hostio, templates
from labape.engine.runner import _iso_keys
from labape.models import Job, KvmHost, Template

from test_api import _admin_session

PG = "postgres://labape:secret@postgres:5432/labape_tofu?sslmode=disable"


def test_uri_and_tunnel_helpers():
    assert hostio.is_remote("qemu+ssh://u@h/system") and not hostio.is_remote("qemu:///system")
    uri = hostio.effective_uri("qemu+ssh://labape@kvm2:2222/system")
    assert uri.startswith("qemu+ssh://labape@kvm2:2222/system?") and "keyfile=" in uri and "no_tty=1" in uri
    assert hostio.effective_uri("qemu:///system") == "qemu:///system"
    assert hostio.tunnel_conn(PG, 31000) == "postgres://labape:secret@127.0.0.1:31000/labape_tofu?sslmode=disable"
    h = hostio.Host("qemu+ssh://labape@kvm2:2222/system")
    assert h.target == "labape@kvm2" and h.port == 2222 and h.ssh_base()[-1] == "labape@kvm2"
    assert "-p" in h.ssh_base() and "BatchMode=yes" in h.ssh_base()


def test_snippets_run_locally(tmp_root):
    lib = tmp_root / "tpl-lib"
    lib.mkdir(exist_ok=True)
    (lib / "rocky9-base-2026.10.qcow2").write_bytes(b"")
    (lib / "win2022-core-2026.10.qcow2").write_bytes(b"")
    (lib / "win2022-core-2026.10.json").write_text(json.dumps({"os_key": "windows_server_2022"}))
    local = hostio.Host("qemu:///system")
    out = local.python(hostio.TEMPLATES, {"lib": str(lib)})
    by = {t["name"]: t for t in out["templates"]}
    assert out["exists"] and by["rocky9-base-2026.10"]["meta"] is None
    assert by["win2022-core-2026.10"]["meta"]["os_key"] == "windows_server_2022"
    probe = local.python(hostio.PROBE, {"paths": {"vm": str(tmp_root), "templates": str(lib)}})
    assert probe["storage"]["vm"]["total_gb"] > 0
    local.python(hostio.WRITE_FILES, {"files": {str(lib / "x.json"): "{}\n"}})
    assert (lib / "x.json").read_text() == "{}\n"


def test_remote_template_sync(client, monkeypatch):
    _admin_session(client)
    r = client.post("/api/hosts", json={"name": "kvm2", "libvirt_uri": "qemu+ssh://labape@kvm2/system",
                                        "template_storage_path": "/srv/tpl"})
    host_id = r.json()["id"]
    calls = []

    def fake_python(self, script, args, timeout=60):
        calls.append((self.target, script, args))
        if script is hostio.TEMPLATES:
            return {"exists": True, "templates": [
                {"name": "rocky9-base-2026.10", "meta": None, "disk_gb": 10},
                {"name": "custom", "meta": {"os_key": "debian_latest", "description": "mine"}, "disk_gb": 20}]}
        return {"written": len(args["files"])}

    monkeypatch.setattr(hostio.Host, "python", fake_python)
    with session_factory()() as db:
        assert templates.sync_host(db, db.get(KvmHost, host_id)) == 2
        rows = {t.name: t for t in db.query(Template).filter(Template.kvm_host_id == host_id)}
        assert rows["rocky9-base-2026.10"].os_key == "rocky9" and rows["rocky9-base-2026.10"].source == "inferred"
        assert rows["custom"].description == "mine" and rows["custom"].disk_gb == 20
    writes = [c for c in calls if c[1] is hostio.WRITE_FILES]
    assert list(writes[0][2]["files"]) == ["/srv/tpl/rocky9-base-2026.10.json"] and writes[0][0] == "labape@kvm2"


def test_remote_job_command(monkeypatch, tmp_root):
    work = tmp_root / "rwork"
    work.mkdir(exist_ok=True)

    class FakeHost(hostio.Host):
        def run(self, command, input=None, timeout=60):
            class R:
                returncode, stdout, stderr = 0, b"/home/labape\n", b""
            return R()

    from labape.config import get_settings
    monkeypatch.setattr(get_settings(), "tofu_pg_conn", PG)
    rj = hostio.RemoteJob(FakeHost("qemu+ssh://labape@kvm2/system"), work, 42)
    assert rj.root == "/home/labape/.labape/jobs/42"
    cmd = rj.command(["bash", str(work / "scripts" / "deploy.sh"), "libvirt", "env1", "--env-file",
                      str(work / "environment.yml")])
    assert cmd[0] == "ssh" and "-tt" in cmd and any(a.startswith("127.0.0.1:") and a.endswith(":postgres:5432")
                                                     for a in cmd)
    remote = cmd[-1]
    assert "/home/labape/.labape/jobs/42/scripts/deploy.sh" in remote and str(work) not in remote
    assert ". ./.labape-remote/env" in remote


def test_iso_keys():
    env = type("E", (), {"spec": {"host_groups": [
        {"os": "rocky9"}, {"os": "windows_server_2022", "image_source": "packer_template"}]}})()
    assert _iso_keys(Job(type="environment.deploy"), env) == {"rocky9"}
    assert _iso_keys(Job(type="template.build", params={"os_key": "debian_latest"}), None) == {"debian_latest"}
    assert _iso_keys(Job(type="environment.destroy"), env) == set()
