"""Batch O4 (design §28.4): KVM host access account, facts, metrics, accounts, install."""
import datetime as dt
import subprocess

from labape.db import session_factory
from labape.engine import hostadmin
from labape.models import HostFacts, HostMetric, Job, KvmHost, utcnow
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

PW = "ThisIsMy_FAKE_Password6!"


def test_setup_keeps_the_password_off_command_lines_and_out_of_errors(monkeypatch):
    monkeypatch.setattr(hostadmin, "ensure_key", lambda: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample labape-hostadmin")
    seen = {}

    def runner(argv, input=None, env=None, **kw):
        seen.update(argv=argv, input=input.decode(), env=env)
        return subprocess.CompletedProcess(argv, 0, b"labape-setup: localadmin01 ready\n", b"")

    out = hostadmin.setup_access("kvm2.lab", 22, "ladmin", PW, "localadmin01", runner=runner)
    assert "ready" in out
    assert PW not in " ".join(seen["argv"]) and seen["argv"][-1] == "sudo -S -p '' bash -s"
    assert seen["input"].startswith(PW + "\n") and "useradd" in seen["input"] and "NOPASSWD: ALL" in seen["input"]
    assert seen["env"]["SSH_ASKPASS_REQUIRE"] == "force" and seen["env"]["LABAPE_ASKPASS"] == PW

    def denied(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, b"", f"sudo: {PW} incorrect password attempt".encode())
    try:
        hostadmin.setup_access("kvm2.lab", 22, "ladmin", PW, "localadmin01", runner=denied)
        raise AssertionError("expected a failure")
    except hostadmin.AdminError as exc:
        assert PW not in str(exc) and "sudo" in str(exc)
    for bad in ("Root!", "a b"):
        try:
            hostadmin.setup_access("h", 22, bad, PW, "localadmin01", runner=runner)
            raise AssertionError("expected a refusal")
        except hostadmin.AdminError:
            pass


class FakeSsh:
    def __init__(self):
        self.calls = []

    def run(self, command, input=None, timeout=60):
        self.calls.append((command, input))
        return subprocess.CompletedProcess(command, 0, b"", b"")


def test_manage_protects_access_accounts_and_sends_passwords_on_stdin(monkeypatch):
    fake = FakeSsh()
    monkeypatch.setattr(hostadmin, "for_host", lambda h: fake)
    h = KvmHost(name="kvm2", ssh_user="localadmin01", access_status="ready")
    for op, name in (("user.delete", "localadmin01"), ("user.lock", "root")):
        try:
            hostadmin.manage(h, op, name)
            raise AssertionError("expected protection")
        except hostadmin.AdminError as exc:
            assert "protected" in str(exc)
    hostadmin.manage(h, "user.password", "alice", password=PW)
    assert fake.calls[-1] == ("sudo -n chpasswd", f"alice:{PW}\n".encode())
    hostadmin.manage(h, "user.add", "bob", comment="Bob", groups=["libvirt", "bad name"],
                     ssh_key="ssh-ed25519 AAAAC3Nza bob@laptop")
    cmd = fake.calls[-1][0]
    assert cmd.startswith("sudo -n bash -c ") and "-G libvirt" in cmd and "bad name" not in cmd and "authorized_keys" in cmd
    hostadmin.manage(h, "group.member_add", "libvirt", member="bob")
    assert fake.calls[-1][0] == "sudo -n gpasswd -a bob libvirt"
    try:
        hostadmin.manage(h, "user.add", "eve", ssh_key="not a key")
        raise AssertionError("expected a refusal")
    except hostadmin.AdminError:
        pass


def test_warnings_and_series():
    facts = {"os": {"failed_units": ["smartd.service"], "reboot_required": True, "updates": 3},
             "tools": {"smartctl": True}}
    smart = {"disks": [{"device": "/dev/sda", "model": "X", "passed": False, "reallocated": 8, "pending": 0},
                       {"device": "/dev/nvme0", "passed": True, "wear_pct": 91}]}
    latest = {"temps": {"coretemp/Package id 0": 92.0}, "fs_used_pct": {"/": 95.5, "/data": 50}}
    msgs = [w["message"] for w in hostadmin.warnings(facts, smart, latest)]
    assert any("FAILED" in m for m in msgs) and any("8 reallocated" in m for m in msgs)
    assert any("91%" in m for m in msgs) and any("92 °C" in m for m in msgs) and any("/ is 95.5% full" in m for m in msgs)
    assert any("smartd.service" in m for m in msgs) and any("reboot" in m for m in msgs)
    assert not any("updates available" in m for m in msgs)               # only 3


def test_access_admin_view_accounts_and_install_api(client, monkeypatch):
    _admin_session(client)
    _host(client)
    calls = {}
    monkeypatch.setattr(hostadmin, "setup_access", lambda *a, **k: calls.setdefault("setup", a) and "ok")
    monkeypatch.setattr(hostadmin, "verify", lambda h: None)
    monkeypatch.setattr("labape.api.hosts._refresh_later", lambda *a, **k: None)
    r = client.post("/api/hosts/1/install", json={"KvmHostName": "kvm2", "Networks": [], "DefaultNetwork": "x"})
    assert r.status_code == 422                                           # no networks
    body = {"address": "172.21.50.9", "admin_user": "ladmin", "admin_password": PW}
    r = client.post("/api/hosts/1/access", json=body)
    assert r.status_code == 200 and r.json()["access_status"] == "ready" and r.json()["ssh_user"] == "localadmin01"
    entry = client.get("/api/admin/audit?action=host.access.setup").json()[0]
    assert PW not in str(entry) and entry["detail"]["admin_user"] == "ladmin"

    with session_factory()() as db:
        db.merge(HostFacts(kvm_host_id=1, collected_at=utcnow(), facts={"os": {"name": "Debian 13"}}, smart={}))
        for i in range(2):
            db.add(HostMetric(kvm_host_id=1, at=utcnow() - dt.timedelta(minutes=10 - 5 * i),
                              data={"cpu": [100 + 25 * i, 0, 0, 100 + 25 * i, 0], "mem_total_kb": 1000,
                                    "mem_available_kb": 250, "fs_used_pct": {"/": 40}, "temps": {"t": 45},
                                    "nic_bytes": {"eno1": [1000 + 3000 * i, 0]}}))
        db.commit()
    v = client.get("/api/hosts/1/admin").json()
    assert v["facts"]["os"]["name"] == "Debian 13" and v["series"]["memory_used_pct"][-1] == 75.0
    assert v["series"]["cpu_pct"][-1] == 50.0 and v["series"]["rx_bps"][-1] == 10    # 3000 bytes in 300 s

    monkeypatch.setattr(hostadmin, "manage", lambda h, op, name, **kw: calls.setdefault("manage", (op, name, kw)))
    monkeypatch.setattr(hostadmin, "collect_facts", lambda db, h, smart=False: None)
    r = client.post("/api/hosts/1/accounts", json={"op": "user.password", "name": "alice", "password": PW})
    assert r.status_code == 200 and calls["manage"][0] == "user.password"
    assert PW not in str(client.get("/api/admin/audit?action=host.user.password").json())
    assert client.post("/api/hosts/1/accounts", json={"op": "user.rm", "name": "alice"}).status_code == 422

    net = {"Name": "lab", "Cidr": "10.0.0.0/24", "Gateway": "10.0.0.1", "Bridge": "br1", "Nic": "eno2"}
    r = client.post("/api/hosts/1/install", json={"KvmHostName": "kvm2", "Networks": [net], "DefaultNetwork": "lab"})
    assert r.status_code == 200, r.text
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert job.type == "host.install" and job.params["answers"]["Networks"][0]["Nic"] == "eno2"
        assert "password" not in str(job.params).lower()

    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username="someone", display_name="", roles={"deployer"})
    assert client.get("/api/hosts/1/admin").status_code == 403
    assert client.post("/api/hosts/1/access", json=body).status_code == 403
    client.app.dependency_overrides.clear()


def test_install_jobs_run_on_a_disabled_host(client):
    _admin_session(client)
    r = client.post("/api/hosts", json={"name": "newhost", "enabled": False})
    assert r.status_code == 201, r.text
    from labape.engine.runner import claim
    with session_factory()() as db:
        db.add_all([Job(type="environment.deploy", kvm_host_id=r.json()["id"], requested_by="t"),
                    Job(type="host.install", kvm_host_id=r.json()["id"], requested_by="t")])
        db.commit()
        got = claim(db, "w1")
        assert got is not None and got.type == "host.install"
        assert claim(db, "w1") is None                         # the deploy waits for the host to be enabled
