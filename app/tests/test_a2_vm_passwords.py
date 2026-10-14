"""A2: a unique password per VM for its local accounts."""
from pathlib import Path

import yaml

from labape import vm_passwords as VP
from labape.api.secrets import classify
from labape.db import session_factory
from labape.models import Environment, KvmHost
from labape.security import get_secret, put_secret


def _env(db):
    db.merge(KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t",
                     bridge="br1", concurrency_limit=2, enabled=True))
    e = Environment(name="vmpw", kvm_host_id=1, created_by="t", status="deployed", spec={
        "host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"], "count": 2},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"]}],
        "accounts": [{"name": "svc", "kind": "local", "hosts": ["windows_server", "linux_server"]}]})
    db.add(e)
    db.commit()
    return e


def test_host_vars_pending_then_own(db_reset, tmp_path):
    with session_factory()() as db:
        e = _env(db)
        assert VP.member_vms(e) == ["app1", "app2"]
        work = tmp_path / "w"
        (work / "ansible" / "inventory" / "host_vars").mkdir(parents=True)
        (work / "ansible" / "inventory" / "host_vars" / "app1.yml").write_text("labape_services: [iis]\n")
        assert VP.write_host_vars(db, e, work) == 3
        app1 = yaml.safe_load((work / "ansible/inventory/host_vars/app1.yml").read_text())
        app2 = yaml.safe_load((work / "ansible/inventory/host_vars/app2.yml").read_text())
        assert app1["labape_services"] == ["iis"]                          # the catalog's stay
        assert "ansible_password" not in app1                              # not its own yet: bootstrap sign-in
        assert app1["labape_admin_password"] != app2["labape_admin_password"]          # unique per VM
        assert app1["labape_recovery_local_password"] != app2["labape_recovery_local_password"]
        svc = {vm: yaml.safe_load((work / f"ansible/inventory/host_vars/{vm}.yml").read_text())["labape_host_passwords"]["svc"]
               for vm in ("app1", "app2", "web1")}
        assert len(set(svc.values())) == 3                                 # the local account too
        # The deploy marks app1 only: app1's becomes its own, app2 keeps waiting.
        (work / VP.APPLIED_DIR / "app1").write_text("")
        assert VP.promote(db, e, work) == ["app1"]
        assert get_secret(db, VP.name(e.id, "app1", "windows_admin")) == app1["labape_admin_password"]
        assert not get_secret(db, VP.name(e.id, "app2", "windows_admin"))
        VP.write_host_vars(db, e, work)
        again = yaml.safe_load((work / "ansible/inventory/host_vars/app1.yml").read_text())
        assert again["ansible_password"] == again["labape_admin_password"] == app1["labape_admin_password"]
        # The stored inventory and the sheet follow.
        inv = yaml.safe_dump({"all": {"hosts": {"app1": {"ansible_password": "boot"}, "app2": {"ansible_password": "boot"}}}})
        out = yaml.safe_load(VP.apply_to_inventory(db, e, inv))["all"]["hosts"]
        assert out["app1"]["ansible_password"] == app1["labape_admin_password"] and out["app2"]["ansible_password"] == "boot"
        put_secret(db, f"environment/{e.id}/windows_admin", "boot")
        sheet = VP.apply_to_sheet(db, e, "## Windows (app1, app2, dc1)\nLocal Administrator: Administrator\nPassword: boot\n")
        assert "Password: member servers have their own" in sheet and f"app1: {app1['labape_admin_password']}" in sheet
        assert "app2: boot" in sheet


def test_secrets_page_names(db_reset):
    with session_factory()() as db:
        e = _env(db)
        envs = {e.id: e}
        r = classify(f"environment/{e.id}/vm/app1/windows_admin", envs)
        assert r["vms"] == ["app1"] and r["scope"] == "local" and not r["attempt"]
        assert classify(f"environment/{e.id}/vm/app2/windows_admin.pending", envs)["attempt"] is True
        assert classify(f"environment/{e.id}/vm/web1/account/svc", envs)["vms"] == ["web1"]
        assert classify(f"environment/{e.id}/rotation-candidate/windows_admin/app1", envs)["vms"] == ["app1"]


def test_the_roles_use_the_per_vm_values():
    root = Path(__file__).resolve().parents[2] / "ansible" / "roles"
    win = (root / "windows_common" / "tasks" / "main.yml").read_text(encoding="utf-8")
    assert "labape_host_passwords" in win
    tasks = yaml.safe_load((root.parent / "playbooks" / "tasks" / "labape_admin_password.yml").read_text(encoding="utf-8"))
    # Decided once (a `when` is checked again before every task, after ansible_password changed), then the
    # probe for an earlier run, the wait, the detached change, the switch, the wait, the marker.
    assert "labape_admin_change" in tasks[0]["ansible.builtin.set_fact"]
    mods = [next(k for k in t if "." in k) for t in tasks]
    assert mods == ["ansible.builtin.set_fact", "ansible.windows.win_ping", "ansible.builtin.set_fact",
                    "ansible.builtin.wait_for_connection", "ansible.windows.win_powershell", "ansible.builtin.set_fact",
                    "ansible.builtin.wait_for_connection", "ansible.builtin.set_fact", "ansible.builtin.set_fact",
                    "ansible.builtin.fail", "ansible.builtin.copy"]
    change = tasks[4]
    # In the same shell (a detached process lost the account database), its refused reply ignored.
    assert "async" not in change and change["ignore_errors"] and change["ignore_unreachable"] and change["no_log"]
    assert "Password" in change["ansible.windows.win_powershell"]["parameters"]       # not on a command line
    assert all("labape_admin_password != ansible_password" not in str(t.get("when", "")) for t in tasks[1:])
    assert "labape_host_passwords" in (root / "linux_common" / "tasks" / "main.yml").read_text(encoding="utf-8")
