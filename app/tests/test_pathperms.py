"""Checking and fixing a KVM host's VM, template and files folders (engine/pathperms.py)."""
import json
import os
import subprocess
import sys

import pytest

from labape.db import session_factory
from labape.engine import hostadmin, pathperms
from labape.models import KvmHost

from test_api import _admin_session

URI = "qemu+ssh://labape@10.0.0.9/system"


def _host(**kw) -> KvmHost:
    return KvmHost(name="k", libvirt_uri=URI, access_status="ready", ssh_address="10.0.0.9", **kw)


def test_available_needs_ssh_and_an_access_account():
    assert "qemu+ssh" in pathperms.available(KvmHost(name="k", libvirt_uri="qemu:///system"))
    assert "access" in pathperms.available(KvmHost(name="k", libvirt_uri=URI, access_status="none"))
    assert pathperms.available(_host()) == ""


def test_specs_skip_a_template_folder_equal_to_the_vm_folder():
    h = _host()
    assert pathperms.specs(h, "/data2/VMs/LABaPe/", "/data2/VMs/LABaPe/templates", "/data2/Files") == [
        {"path": "/data2/VMs/LABaPe", "kind": "vm"}, {"path": "/data2/VMs/LABaPe/templates", "kind": "templates"},
        {"path": "/data2/Files", "kind": "files"}]
    assert len(pathperms.specs(h, "/a", "/a")) == 1


def _result(path, kind="vm", level="fix"):
    return {"path": path, "kind": kind, "items": [{"level": level, "text": "do it"}],
            "ok": level != "problem", "changes": 1 if level in ("fix", "fixed") else 0}


def test_preview_and_fix_endpoint(client, db_reset, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        db.add(_host(vm_storage_path="/data/VMs/LABaPe", template_storage_path="/data/VMs/LABaPe/templates"))
        db.commit()
    seen = []
    monkeypatch.setattr(pathperms, "check", lambda h, paths, fix=False: seen.append((paths, fix)) or
                        [_result(p["path"], p["kind"]) for p in paths])
    body = {"vm_storage_path": "/data2/VMs/LABaPe", "template_storage_path": "/data2/VMs/LABaPe/templates",
            "files_path": "/data2/Files"}
    r = client.post("/api/hosts/1/paths", json=body)
    assert r.status_code == 200 and r.json()["available"] and r.json()["changes"] == 3 and not seen[0][1]
    assert [p["kind"] for p in seen[0][0]] == ["vm", "templates", "files"]
    assert client.post("/api/hosts/1/paths", json={**body, "fix": True}).status_code == 200 and seen[1][1]
    assert client.post("/api/hosts/1/paths", json={**body, "vm_storage_path": "/a b"}).status_code == 422


def test_save_fixes_a_changed_path_and_refuses_what_it_cannot_fix(client, db_reset, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        db.add(_host(vm_storage_path="/data/VMs/LABaPe", template_storage_path="/data/VMs/LABaPe/templates"))
        db.commit()
    calls = []

    def fake(h, paths, fix=False):
        calls.append((paths, fix))
        return [_result(p["path"], p["kind"], "fixed" if fix else "fix") for p in paths]
    monkeypatch.setattr(pathperms, "check", fake)
    monkeypatch.setattr(hostadmin, "ensure_files_path", lambda *a, **k: "")
    put = {"name": "k", "libvirt_uri": URI, "vm_storage_path": "/data2/VMs/LABaPe",
           "template_storage_path": "/data2/VMs/LABaPe/templates"}
    assert client.put("/api/hosts/1", json=put).status_code == 200
    assert calls and calls[0][1] is True and calls[0][0][0] == {"path": "/data2/VMs/LABaPe", "kind": "vm"}
    calls.clear()
    assert client.put("/api/hosts/1", json=put).status_code == 200 and not calls         # unchanged: no check

    monkeypatch.setattr(pathperms, "check", lambda h, paths, fix=False: [_result(paths[0]["path"], "vm", "problem")])
    r = client.put("/api/hosts/1", json={**put, "vm_storage_path": "/data3/VMs"})
    assert r.status_code in (400, 422) and "vm_storage_path" in json.dumps(r.json())
    with session_factory()() as db:
        assert db.get(KvmHost, 1).vm_storage_path == "/data2/VMs/LABaPe"                  # not saved


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="runs the host script as a plain Linux user")
def test_script_reports_what_it_would_change(tmp_path):
    """Dry run against real folders: a missing path would be created, a closed folder is flagged."""
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    script = pathperms.SCRIPT.replace('("libvirt-qemu", "qemu")', f'("{me}",)')    # this user stands in for qemu
    script = script.replace('"run", "tmp", "etc"', '"run", "etc"')                  # pytest's folders are under /tmp
    target = tmp_path / "VMs" / "LABaPe"
    args = {"user": me, "paths": [{"path": str(target), "kind": "vm"}], "fix": False, "subdirs": []}
    r = subprocess.run([sys.executable, "-c", "import json,sys;exec(sys.stdin.read(), {'ARGS': json.loads(sys.argv[1])})",
                        json.dumps(args)], input=script, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)[0]
    assert out["ok"] and out["changes"] >= 1 and any("create" in i["text"] for i in out["items"])
    assert not target.exists()                                                            # a dry run changes nothing
    target.mkdir(parents=True)
    args["paths"] = [{"path": str(target), "kind": "vm"}]
    r = subprocess.run([sys.executable, "-c", "import json,sys;exec(sys.stdin.read(), {'ARGS': json.loads(sys.argv[1])})",
                        json.dumps(args)], input=script, capture_output=True, text=True)
    assert json.loads(r.stdout)[0]["changes"] == 0
