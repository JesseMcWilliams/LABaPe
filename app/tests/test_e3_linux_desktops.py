"""E3 (design §50.5, question 168): xrdp and VNC on Linux VMs, and connecting to them as an account."""
from labape import linux_desktop, remote, vm_passwords
from labape.db import session_factory
from labape.models import Environment
from labape.security import put_secret

from test_api import _admin_session, _host

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 2,
                         "remote_desktop": ["xrdp", "vnc"]},
                        {"name": "db", "os": "rocky9", "roles": ["linux_workstation"]}],
        "accounts": [{"name": "zed", "kind": "local", "hosts": ["linux_server"]},
                     {"name": "amy", "kind": "local", "hosts": ["linux_server", "linux_workstation"]},
                     {"name": "jdoe", "kind": "domain"}]}
INV = """all:
  vars: {domain_name: lab.test, netbios_name: LAB}
  hosts:
    dc1: {ansible_host: 172.21.50.1}
    web1: {ansible_host: 172.21.50.4, ansible_user: labape}
    web2: {ansible_host: 172.21.50.5, ansible_user: labape}
    db1: {ansible_host: 172.21.50.6, ansible_user: labape}
"""
PW = "ThisIsMy_FAKE_Password6!"


def _env(spec=None):
    return Environment(name="lab6", status="deployed", kvm_host_id=1, created_by="admin", spec=spec or SPEC)


def test_what_each_vm_offers_and_the_host_vars(db_reset, tmp_path):
    with session_factory()() as db:
        e = _env()
        db.add(e)
        db.commit()
        assert linux_desktop.desktops(e, "web1") == ["xrdp", "vnc"] and linux_desktop.desktops(e, "db1") == []
        assert linux_desktop.desktops(e, "dc1") == []
        assert linux_desktop.vnc_accounts(e, "web1") == ["amy", "zed"]          # displays :1, :2 by name
        assert linux_desktop.vnc_display(e, "web1", "zed") == 2 and linux_desktop.vnc_display(e, "db1", "amy") is None
        for vm in ("web1", "web2"):
            for n in ("amy", "zed"):
                put_secret(db, vm_passwords.name(e.id, vm, f"account/{n}"), f"{PW}{vm}{n}")
        hv = linux_desktop.host_vars(db, e)
        assert set(hv) == {"web1", "web2"}
        assert hv["web1"]["labape_remote_desktop"] == ["xrdp", "vnc"]
        assert [(u["name"], u["display"], u["password"]) for u in hv["web1"]["labape_vnc_users"]] == \
            [("amy", 1, f"{PW}web1amy"), ("zed", 2, f"{PW}web1zed")]
        assert hv["web1"]["labape_desktop_ports"] == ["3389", "5901", "5902"]
        import yaml
        assert linux_desktop.write_host_vars(db, e, tmp_path) == 2
        f = tmp_path / "ansible" / "inventory" / "host_vars" / "web1.yml"
        assert yaml.safe_load(f.read_text())["labape_desktop_ports"] == ["3389", "5901", "5902"]


def test_connect_offers_and_uses_the_desktops(db_reset):
    with session_factory()() as db:
        e = _env()
        db.add(e)
        db.commit()
        put_secret(db, f"environment/{e.id}/inventory", INV)
        put_secret(db, vm_passwords.name(e.id, "web1", "account/zed"), PW)
        put_secret(db, f"environment/{e.id}/account/jdoe", PW)
        t = lambda rest: sorted((x["vm"], x["protocol"]) for x in remote.account_targets(e, rest))  # noqa: E731
        assert t("vm/web1/account/zed") == [("web1", "rdp"), ("web1", "ssh"), ("web1", "vnc")]
        assert t("vm/db1/account/amy") == [("db1", "ssh")]                       # no desktops there
        assert ("web1", "rdp") in t("account/jdoe") and ("web1", "vnc") not in t("account/jdoe")
        assert ("db1", "rdp") not in t("account/jdoe")
        vnc = remote.vm_target(db, e, "web1", "vm/web1/account/zed", "vnc")
        assert vnc["protocol"] == "vnc" and vnc["params"] == {"hostname": "172.21.50.4", "port": "5902", "password": PW}
        rdp = remote.vm_target(db, e, "web1", "account/jdoe", "rdp")["params"]
        assert rdp["username"] == "jdoe@lab.test" and rdp["domain"] == ""        # xrdp: sssd's user name


def test_setting_desktops_on_an_existing_environment(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = _env(spec={**SPEC, "host_groups": [dict(g, remote_desktop=[]) for g in SPEC["host_groups"]]})
        db.add(e)
        db.commit()
        env_id = e.id
    r = client.put(f"/api/environments/{env_id}/desktops", json={"groups": [{"name": "db", "remote_desktop": ["xrdp"]}]})
    assert r.status_code == 200 and {"name": "db", "remote_desktop": ["xrdp"]} in r.json()["host_groups"]
    bad = client.put(f"/api/environments/{env_id}/desktops", json={"groups": [{"name": "dc", "remote_desktop": ["vnc"]}]})
    assert bad.status_code == 422
    assert client.put(f"/api/environments/{env_id}/desktops",
                      json={"groups": [{"name": "nope", "remote_desktop": []}]}).status_code == 422
