"""Secret rotation R2 (engine vault password, VM key: the host agent) and R4
(each environment's Windows Administrator and domain Administrator), plus the
configurable Linux admin account (Settings > VM accounts)."""
import io

import yaml

from labape import vm_passwords
from labape.db import session_factory
from labape.engine import rotation, vmaccess
from labape.engine.runner import claim
from labape.models import Environment, Job, SecretPolicy
from labape.security import get_secret, put_secret

from test_api import ENV, _admin_session, _host

INV = {"all": {"hosts": {
    "dc1": {"ansible_host": "10.0.0.10", "ansible_connection": "winrm", "ansible_user": "Administrator",
            "ansible_password": "ThisIsMy_FAKE_Password6!"},
    "app1": {"ansible_host": "10.0.0.11", "ansible_connection": "winrm", "ansible_user": "Administrator",
             "ansible_password": "ThisIsMy_FAKE_Password6!"},
    "web1": {"ansible_host": "10.0.0.12", "ansible_connection": "ssh", "ansible_user": "opsadmin",
             "ansible_ssh_private_key_file": "/x/labape_bootstrap"}},
    "children": {"domain_controller": {"hosts": {"dc1": None}}, "member_server": {"hosts": {"app1": None}}}}}
SHEET = ("# LABaPe: generated\n\n## Windows (app1, dc1)\nLocal Administrator: Administrator\n"
         "Password: ThisIsMy_FAKE_Password6!\n\n## Domain administrator\nUser: LAB\\Administrator\n"
         "Password: ThisIsMy_FAKE_Password6!\n\n## Linux (web1)\nUser: opsadmin\n")


def _deployed_env(client) -> int:
    _admin_session(client)
    host_id = _host(client)
    env_id = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id}).json()["environment"]["id"]
    with session_factory()() as db:
        for j in db.query(Job).filter(Job.environment_id == env_id):
            j.state = "succeeded"
        db.get(Environment, env_id).status = "deployed"
        put_secret(db, f"environment/{env_id}/inventory", yaml.safe_dump(INV))
        put_secret(db, f"environment/{env_id}/credentials", SHEET)
        db.commit()
    return env_id


def test_vm_accounts_setting_recorded_per_environment(client):
    _admin_session(client)
    assert client.get("/api/admin/settings").json()["vm_accounts"]["linux_admin_user"] == "labape"
    for bad in ("Root", "root", "1abc", "a" * 33, "has space"):
        assert client.put("/api/admin/settings/vm_accounts", json={"linux_admin_user": bad}).status_code == 422, bad
    assert client.put("/api/admin/settings/vm_accounts", json={"linux_admin_user": "opsadmin"}).status_code == 200
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id})
    assert r.json()["environment"]["spec"]["linux_admin_user"] == "opsadmin"
    client.put("/api/admin/settings/vm_accounts", json={"linux_admin_user": "labape"})


def test_r2_kinds_run_on_the_worker_without_maintenance(client):
    # Since the engine secrets live in the store (design §26), the worker rotates them; the
    # agent's file-based R2 rotation was removed.
    for kind in ("vault_password", "bootstrap_ssh_key"):
        r = rotation.REGISTRY[kind]
        assert r.automatic and not r.via_agent and not r.needs_restart and r.interval_hours == 24


def test_update_sheet_touches_only_its_section():
    out = vmaccess.update_sheet(SHEET, "windows_admin", "New-Local-1")
    assert "Password: New-Local-1" in out and out.count("ThisIsMy_FAKE_Password6!") == 1
    out = vmaccess.update_sheet(out, "domain_admin", "New-Domain-2")
    assert "## Domain administrator\nUser: LAB\\Administrator\nPassword: New-Domain-2" in out
    old = "## Windows (app1)\nLocal Administrator: Administrator\nPassword: x\n"
    assert "## Domain administrator" in vmaccess.update_sheet(old, "domain_admin", "y")   # sheets from before R4


class FakeHosts:
    """Windows hosts with real passwords: a module signs in only with the current one;
    a password change takes effect (and, like Basic auth over WinRM, may still report a failure)."""

    def __init__(self, inv, down=(), fail_after_change=False):
        self.pw = {h: v.get("ansible_password") for h, v in inv["all"]["hosts"].items()}
        self.down, self.fail_after_change, self.calls = set(down), fail_after_change, []

    def __call__(self, inv, pattern, module, args, extra=None, env=None, timeout=600, scrub=(), task=None):
        self.calls.append((pattern, module, dict(args), dict(extra or {}), task))
        out = vmaccess.Outcome()
        out.errors = {}
        for h in pattern.split(":"):
            hv = inv["all"]["hosts"][h]
            if h in self.down or hv.get("ansible_password") != self.pw[h]:
                out[h], out.errors[h] = "unreachable", "basic: the specified credentials were rejected by the server"
            elif module.endswith("win_ping"):
                out[h] = "ok"
            else:
                if args["password"] == "{{ new_pw }}":
                    self.pw[h] = (extra or {}).get("new_pw")
                elif args["password"] == "{{ labape_new_pw }}":       # one per host (A2)
                    self.pw[h] = hv["labape_new_pw"]
                else:
                    self.pw[h] = hv["labape_old_pw"]
                out[h] = "failed" if self.fail_after_change else "changed"
        return out


def _fake_hosts(monkeypatch, env_id, **kw):
    monkeypatch.setattr(vmaccess.time, "sleep", lambda s: None)
    monkeypatch.setattr(vmaccess, "PROBE_SECONDS", 0)
    with session_factory()() as db:
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
    hosts = FakeHosts(inv, **kw)
    monkeypatch.setattr(vmaccess, "_ansible", hosts)
    return hosts


def test_env_policies_and_rotation(client, monkeypatch):
    env_id = _deployed_env(client)
    pols = client.get(f"/api/environments/{env_id}/rotation").json()["policies"]
    # With the recovery pairs' second admins (A1).
    assert sorted(p["kind"] for p in pols) == ["domain_admin", "domain_admin2", "windows_admin", "windows_admin2"]
    assert all(p["interval_hours"] == 168 and p["enabled"] for p in pols)
    assert client.put(f"/api/environments/{env_id}/rotation/windows_admin",
                      json={"enabled": True, "interval_hours": 12}).status_code == 422    # a day at least
    r = client.put(f"/api/environments/{env_id}/rotation/domain_admin", json={"enabled": False, "interval_hours": 240})
    assert r.status_code == 200 and r.json()["enabled"] is False

    hosts = _fake_hosts(monkeypatch, env_id)
    r = client.post(f"/api/environments/{env_id}/rotation/windows_admin/rotate")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    with session_factory()() as db:
        job = db.get(Job, job_id)
        assert job.environment_id == env_id
        job.log_path = str(__import__("tempfile").mktemp())
        db.commit()
        assert rotation.run_job(db, job) == 0
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
        new = inv["all"]["hosts"]["app1"]["ansible_password"]
        assert new != "ThisIsMy_FAKE_Password6!" and len(new) == 20 and hosts.pw["app1"] == new
        assert inv["all"]["hosts"]["dc1"]["ansible_password"] == "ThisIsMy_FAKE_Password6!"   # the DC isn't a member
        # A2: the member's own, per VM; the environment-wide bootstrap value isn't touched.
        assert vmaccess.env_password_overrides(db, env_id) == {}
        assert get_secret(db, vm_passwords.name(env_id, "app1", "windows_admin")) == new
        assert f"app1: {new}" in get_secret(db, f"environment/{env_id}/credentials")
        assert not get_secret(db, f"environment/{env_id}/rotation-candidate/windows_admin/app1")
    change = hosts.calls[0]
    assert change[:2] == ("app1", "ansible.windows.win_user") and not change[4]     # same shell, not detached
    assert hosts.calls[1][1] == "ansible.windows.win_ping"

    # Domain admin: on the DC only, stored as domain_admin_password.
    hosts.calls.clear()
    with session_factory()() as db:
        vmaccess.rotate_env_password(db, db.get(Environment, env_id), "domain_admin", io.StringIO())
        over = vmaccess.env_password_overrides(db, env_id)
        assert set(over) == {"domain_admin_password"}
        assert hosts.pw["dc1"] == over["domain_admin_password"]
    assert hosts.calls[0][:2] == ("dc1", "microsoft.ad.user")

    # A disabled or undeployed environment's policy isn't due.
    with session_factory()() as db:
        for p in db.query(SecretPolicy).filter(SecretPolicy.name.startswith(f"env.{env_id}.")):
            p.next_due_at = None
        db.get(Environment, env_id).status = "destroyed"
        db.commit()
        assert not [p for p in rotation.due(db) if p.name.startswith(f"env.{env_id}.")]


def test_change_reported_failed_but_applied(client, monkeypatch):
    """Basic auth: the module changes its own account's password, then fails on its next request."""
    env_id = _deployed_env(client)
    hosts = _fake_hosts(monkeypatch, env_id, fail_after_change=True)
    with session_factory()() as db:
        vmaccess.rotate_env_password(db, db.get(Environment, env_id), "windows_admin", io.StringIO())
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
        assert inv["all"]["hosts"]["app1"]["ansible_password"] == hosts.pw["app1"] != "ThisIsMy_FAKE_Password6!"


def test_failed_change_keeps_old_password(client, monkeypatch):
    env_id = _deployed_env(client)
    hosts = _fake_hosts(monkeypatch, env_id, down={"app1"})
    with session_factory()() as db:
        try:
            vmaccess.rotate_env_password(db, db.get(Environment, env_id), "windows_admin", io.StringIO())
            raise AssertionError("expected AccessError")
        except vmaccess.AccessError as exc:
            assert "app1" in str(exc) and "check app1 by hand" in str(exc)    # can't tell: it's unreachable
            assert "rotation-candidate" in str(exc)
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
        assert inv["all"]["hosts"]["app1"]["ansible_password"] == "ThisIsMy_FAKE_Password6!"
        assert get_secret(db, vm_passwords.name(env_id, "app1", "windows_admin")) is None
        assert len(get_secret(db, f"environment/{env_id}/rotation-candidate/windows_admin/app1")) == 20
    assert hosts.pw["app1"] == "ThisIsMy_FAKE_Password6!"


def test_each_vm_changes_on_its_own(client, monkeypatch):
    """A2: each member VM has its own password; one VM failing doesn't undo the others."""
    env_id = _deployed_env(client)
    with session_factory()() as db:
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
        inv["all"]["hosts"]["app2"] = {**inv["all"]["hosts"]["app1"]}
        put_secret(db, f"environment/{env_id}/inventory", yaml.safe_dump(inv))
    hosts = _fake_hosts(monkeypatch, env_id)
    hosts.pw["app2"] = "a-password-nobody-stored"           # app2 rejects the stored password: it can't change
    with session_factory()() as db:
        try:
            vmaccess.rotate_env_password(db, db.get(Environment, env_id), "windows_admin", io.StringIO())
            raise AssertionError("expected AccessError")
        except vmaccess.AccessError as exc:
            assert "app2" in str(exc)                  # app2 takes neither: checked by hand
        inv = yaml.safe_load(get_secret(db, f"environment/{env_id}/inventory"))
        assert inv["all"]["hosts"]["app1"]["ansible_password"] == hosts.pw["app1"] != "ThisIsMy_FAKE_Password6!"
        assert get_secret(db, vm_passwords.name(env_id, "app1", "windows_admin")) == hosts.pw["app1"]
        assert inv["all"]["hosts"]["app2"]["ansible_password"] == "ThisIsMy_FAKE_Password6!"   # stored one unchanged


def test_push_vm_key(client, monkeypatch, tmp_path):
    _deployed_env(client)
    hist = tmp_path / "history"
    hist.mkdir()
    for i in range(8):
        (hist / f"labape_bootstrap-2026010{i}T000000Z").write_text("k")
        (hist / f"labape_bootstrap-2026010{i}T000000Z.pub").write_text(f"ssh-ed25519 OLD{i}")
    (tmp_path / "new").write_text("k")
    (tmp_path / "new.pub").write_text("ssh-ed25519 NEW")
    (tmp_path / "current").write_text("k")
    calls = []

    def fake(inv, pattern, module, args, extra=None, env=None, timeout=600, scrub=()):
        calls.append((pattern, module, args, dict(extra or {}), dict(env or {})))
        return {h: "changed" for h in pattern.split(":")}
    monkeypatch.setattr(vmaccess, "_ansible", fake)
    with session_factory()() as db:
        report = vmaccess.push_vm_key(db, tmp_path / "new", tmp_path / "current", hist, io.StringIO())
    assert any(v.get("updated") == ["web1"] for v in report["environments"].values())
    added = calls[0]
    assert added[0] == "web1" and added[3]["new_pub"] == "ssh-ed25519 NEW" and "-i" in added[4]["ANSIBLE_SSH_COMMON_ARGS"]
    assert calls[1][1] == "ansible.builtin.ping" and calls[1][3]["ansible_ssh_private_key_file"].endswith("new")
    removed = [c[3]["old_pub"] for c in calls if "old_pub" in c[3]]
    # The current key and the 6 newest previous ones stay accepted (7 in all); the 2 oldest go.
    assert sorted(removed) == ["ssh-ed25519 OLD0", "ssh-ed25519 OLD1"]


def test_one_job_per_environment(client):
    env_id = _deployed_env(client)
    with session_factory()() as db:
        db.add(Job(type="environment.refresh_addresses", environment_id=env_id, state="running", requested_by="t"))
        db.add(Job(type="secret.rotate", environment_id=env_id, params={}, requested_by="t"))
        db.commit()
        job = claim(db, "w1")
        assert job is None or job.environment_id != env_id


def test_ansible_recap_parsing(monkeypatch):
    seen = {}

    class R:
        returncode = 2
        stderr = ""
        stdout = ('fatal: [app1]: UNREACHABLE! => {"changed": false, "msg": "basic: credentials rejected Secret-1", '
                  '"unreachable": true}\n'
                  'fatal: [dc1]: FAILED! => {"changed": false, "msg": "boom"}\n\nPLAY RECAP ***\n'
                  "web1                       : ok=1    changed=1    unreachable=0    failed=0    skipped=0\n"
                  "app1                       : ok=0    changed=0    unreachable=1    failed=0    skipped=0\n"
                  "dc1                        : ok=0    changed=0    unreachable=0    failed=1    skipped=0\n"
                  "lin2                       : ok=1    changed=0    unreachable=0    failed=0    skipped=0\n")

    def run(cmd, **k):
        play = yaml.safe_load(open(cmd[-1]).read())
        seen["play"], seen["vars"] = play, yaml.safe_load(open(cmd[cmd.index("-e") + 1][1:]).read())
        return R()
    monkeypatch.setattr(vmaccess.subprocess, "run", run)
    out = vmaccess._ansible(INV, "all", "ansible.windows.win_user", {"name": "Administrator", "password": "{{ new_pw }}"},
                            {"new_pw": "Secret-1"})
    assert dict(out) == {"web1": "changed", "app1": "unreachable", "dc1": "failed", "lin2": "ok"}
    assert out.errors["app1"] == "basic: credentials rejected ***" and out.errors["dc1"] == "boom"
    assert seen["play"][0]["tasks"][0]["ansible.windows.win_user"]["password"] == "{{ new_pw }}"
    assert seen["vars"] == {"new_pw": "Secret-1"}      # in the 0600 vars file, not on the command line
    R.stdout, R.stderr = "", "ERROR! no inventory"
    try:
        vmaccess._ansible(INV, "all", "ping", {})
        raise AssertionError("expected AccessError")
    except vmaccess.AccessError as exc:
        assert "no inventory" in str(exc)


def test_destroy_clears_rotated_passwords(client):
    from labape.engine.runner import _finish
    env_id = _deployed_env(client)
    with session_factory()() as db:
        put_secret(db, f"environment/{env_id}/domain_admin", "x")
        job = Job(type="environment.destroy", environment_id=env_id, state="running", requested_by="t")
        db.add(job)
        db.commit()
        _finish(db, job, db.get(Environment, env_id), 0)
        assert db.get(Environment, env_id).status == "destroyed"
        assert not get_secret(db, f"environment/{env_id}/domain_admin")
