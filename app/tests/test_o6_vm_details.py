"""Batch O6 (design §28.6): a VM's details, its accounts and groups, and its consoles."""
import asyncio
import subprocess
import sys

import pytest

from labape import remote
from labape.db import session_factory
from labape.engine import hostadmin, hoststats, vmadmin
from labape.models import Environment, HostStats, KvmHost, MetricSample, RemoteSession, utcnow
from labape.security import Principal, current_principal

from test_api import _admin_session
from test_o5_remote import _env

PW = "ThisIsMy_FAKE_Password6!"

GUEST = """arch                : x86_64
os.id               : rocky
os.pretty-name      : Rocky Linux 9.4 (Blue Onyx)
hostname            : lin1
timezone.name       : UTC
user.count          : 1
user.0.name         : labape
user.0.login-time   : 1760000000000
fs.count            : 1
fs.0.name           : vda1
fs.0.mountpoint     : /
fs.0.fstype         : xfs
fs.0.total-bytes    : 21474836480
fs.0.used-bytes     : 5368709120
disk.count          : 1
disk.0.name         : /dev/vda
disk.0.alias        : vda
if.count            : 2
if.0.name           : eth0
if.0.hwaddr         : 52:54:00:aa:bb:cc
if.0.addr.count     : 1
if.0.addr.0.type    : ipv4
if.0.addr.0.addr    : 172.21.50.10
if.0.addr.0.prefix  : 24
if.1.name           : Loopback Pseudo-Interface 1
if.1.hwaddr         :
if.1.addr.count     : 0
"""


def test_parse_guest_full():
    g = hoststats.parse_guest_full(GUEST)
    assert g["os"]["pretty-name"].startswith("Rocky Linux 9.4") and g["hostname"] == "lin1"
    assert g["users"] == [{"name": "labape", "domain": "", "login_time": "1760000000000"}]
    assert g["filesystems"][0] == {"mount": "/", "name": "vda1", "type": "xfs", "total": 21474836480, "used": 5368709120}
    assert g["interfaces"] == [{"name": "eth0", "hwaddr": "52:54:00:aa:bb:cc", "addresses": ["172.21.50.10/24"]},
                               {"name": "Loopback Pseudo-Interface 1", "hwaddr": "", "addresses": []}]   # empty hwaddr
    assert hoststats.parse_guest_full("") ["users"] == []


def _ready_host(tmp_path, monkeypatch):
    key = tmp_path / "labape_hostadmin"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nhostkey\n-----END OPENSSH PRIVATE KEY-----\n")
    monkeypatch.setattr(hostadmin, "key_path", lambda: key)
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        h.ssh_address, h.ssh_port, h.ssh_user, h.access_status = "172.21.20.11", 22, "localadmin01", "ready"
        db.commit()


def test_details_endpoint_and_console_choices(client, tmp_path, monkeypatch):
    _admin_session(client)
    eid = _env(client)
    with session_factory()() as db:
        db.merge(HostStats(kvm_host_id=1, collected_at=utcnow(), error="", stats={"vms": [
            {"name": "lin1", "state": "running", "online": True, "responding": True, "vcpus": 2, "memory_mb": 2048,
             "cpu_pct": 3.5, "guest_disk": {"total_gb": 20.0, "free_gb": 15.0},
             "guest_info": hoststats.parse_guest_full(GUEST)}]}))
        db.add(MetricSample(at=utcnow(), kvm_host_id=1, vm="lin1", data={"cpu_pct": 3.5, "memory_used_mb": 900}))
        db.commit()
    r = client.get(f"/api/environments/{eid}/vms/lin1")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["state"] == "running" and d["address"] == "172.21.50.10" and d["guest"]["hostname"] == "lin1"
    assert d["series"]["cpu_pct"] == [3.5] and d["size"]["vcpus"] == 2
    assert d["consoles"] == {"vnc": False, "serial": True, "host_ready": False, "host_id": 1, "virt_viewer": ""}
    win = client.get(f"/api/environments/{eid}/vms/win1").json()
    assert win["state"] == "missing" and win["consoles"]["vnc"] and not win["consoles"]["serial"]
    assert client.get(f"/api/environments/{eid}/vms/nope").status_code == 404

    # Consoles need the KVM host's access account.
    bad = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "serial", "environment_id": eid,
                                                    "vm": "lin1"})
    assert bad.status_code == 422 and "SSH access" in bad.json()["errors"][0]["message"]
    _ready_host(tmp_path, monkeypatch)
    d = client.get(f"/api/environments/{eid}/vms/lin1").json()
    assert d["consoles"]["virt_viewer"] == "virt-viewer --connect qemu+ssh://localadmin01@172.21.20.11/system lin1"
    r = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "serial", "environment_id": eid,
                                                  "vm": "lin1"})
    assert r.status_code == 200, r.text
    with session_factory()() as db:
        s = db.get(RemoteSession, r.json()["id"])
        assert s.address == "172.21.20.11" and s.protocol == "serial"
        env = db.get(Environment, eid)
        t = remote.console_target(env, "lin1", "serial")
        assert t["protocol"] == "ssh" and t["params"]["username"] == "localadmin01"
        assert t["params"]["command"] == "sudo -n virsh -c qemu:///system console lin1 --force"
        assert "hostkey" in t["params"]["private-key"]
        v = remote.console_target(env, "win1", "vnc")
        assert v["protocol"] == "vnc" and v["forward"] == {"address": "172.21.20.11", "port": 22,
                                                           "user": "localadmin01", "domain": "win1"}
        with pytest.raises(remote.RemoteError):
            remote.console_target(env, "win1", "serial")


def test_accounts_need_an_owner(client):
    _admin_session(client)
    eid = _env(client)
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username="someone", display_name="", roles={"auditor"})
    assert client.get(f"/api/environments/{eid}/vms/lin1/accounts").status_code in (403, 404)
    assert client.post(f"/api/environments/{eid}/vms/lin1/accounts",
                       json={"op": "user.lock", "name": "bob"}).status_code in (403, 404)
    client.app.dependency_overrides.clear()


class FakeSsh:
    def __init__(self):
        self.commands = []

    def run(self, command, input=None, timeout=60):
        self.commands.append((command, input))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def python(self, script, args, timeout=120):
        return {"accounts": [{"name": "labape"}], "groups": [{"name": "wheel", "members": ["labape"]}]}

    def close(self):
        pass


def test_linux_vm_accounts_over_ssh(client, monkeypatch):
    _admin_session(client)
    eid = _env(client)
    ssh = FakeSsh()
    monkeypatch.setattr(vmadmin, "_linux_ssh", lambda db, host: ssh)
    r = client.get(f"/api/environments/{eid}/vms/lin1/accounts")
    assert r.status_code == 200 and r.json()["groups"][0]["name"] == "wheel" and r.json()["windows"] is False
    r = client.post(f"/api/environments/{eid}/vms/lin1/accounts", json={"op": "user.password", "name": "bob", "password": PW})
    assert r.status_code == 200, r.text
    assert ssh.commands[-1] == ("sudo -n chpasswd", f"bob:{PW}\n".encode())
    for protected in ("labape", "root"):
        r = client.post(f"/api/environments/{eid}/vms/lin1/accounts", json={"op": "user.delete", "name": protected})
        assert r.status_code == 422 and "protected" in r.json()["errors"][0]["message"]
    audit = client.get("/api/admin/audit?action=vm.user.password").json()
    assert audit and PW not in str(audit)


def test_windows_vm_accounts_through_ansible(client, monkeypatch):
    _admin_session(client)
    eid = _env(client)
    tasks = []

    def fake_win(inv, vm, task, timeout=180):
        tasks.append((vm, task))
        if "ansible.windows.win_powershell" in task:
            return {"output": ['{"users":[{"name":"Administrator","enabled":true,"groups":["Administrators"]},'
                               '{"name":"Guest","enabled":false,"groups":[]}],'
                               '"groups":[{"name":"Administrators","members":["WIN1\\\\Administrator"]}]}']}
        return {"changed": True}

    monkeypatch.setattr(vmadmin, "_win", fake_win)
    r = client.get(f"/api/environments/{eid}/vms/win1/accounts").json()
    assert r["windows"] and r["accounts"][0] == {"name": "Administrator", "comment": "", "locked": False,
                                                 "groups": ["Administrators"], "admin": True}
    assert r["accounts"][1]["locked"] and r["groups"][0]["members"] == ["Administrator"]
    r = client.post(f"/api/environments/{eid}/vms/win1/accounts",
                    json={"op": "user.add", "name": "Lab User", "password": PW, "groups": ["Remote Desktop Users"]})
    assert r.status_code == 200, r.text
    vm, task = tasks[-1]
    assert vm == "win1" and task["no_log"] is True
    assert task["ansible.windows.win_user"]["groups"] == ["Remote Desktop Users"]
    r = client.post(f"/api/environments/{eid}/vms/win1/accounts", json={"op": "user.delete", "name": "Administrator"})
    assert r.status_code == 422 and "protected" in r.json()["errors"][0]["message"]


def test_vnc_forwarder_pipes_one_connection_through_ssh(monkeypatch):
    # A stand-in for `ssh ... -W 127.0.0.1:PORT dest`: echoes its -W target, then stdin, upper-cased.
    script = ("import sys;w=sys.argv[sys.argv.index('-W')+1];sys.stdout.write(w+'|');sys.stdout.flush();"
              "d=sys.stdin.read(5);sys.stdout.write(d.upper());sys.stdout.flush()")
    monkeypatch.setattr(remote, "vnc_port", lambda fwd: 5901)
    monkeypatch.setattr(hostadmin.Ssh, "base", lambda self: [sys.executable, "-c", script, f"{self.user}@{self.address}"])

    async def go():
        server, port = await remote.start_forward({"address": "h", "port": 22, "user": "u", "domain": "lin1"},
                                                  allow_host="")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"hello")
        await writer.drain()
        got = await asyncio.wait_for(reader.read(100), 10)
        while b"HELLO" not in got:
            more = await asyncio.wait_for(reader.read(100), 10)
            if not more:
                break
            got += more
        writer.close()
        # The forwarder takes one connection only.
        with pytest.raises((ConnectionError, OSError, asyncio.TimeoutError)):
            r2, w2 = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
            if not await asyncio.wait_for(r2.read(10), 2):
                raise ConnectionError("closed")
        server.close()
        return got

    assert asyncio.run(go()) == b"127.0.0.1:5901|HELLO"
