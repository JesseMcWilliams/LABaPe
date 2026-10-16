"""E2 (design §50.4, question 167): browser sessions signed in as one of the environment's accounts."""
import pytest

from labape import remote
from labape.db import session_factory
from labape.models import Environment, RemoteSession
from labape.security import put_secret

from test_api import _admin_session, _host

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"], "count": 2},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"]}],
        "accounts": [{"name": "jdoe", "kind": "domain"}, {"name": "boss", "kind": "domain", "groups": ["Domain Admins"]},
                     {"name": "tester", "kind": "local", "hosts": ["linux_server"]}]}
INV = """all:
  vars: {domain_name: lab.test, netbios_name: LAB}
  hosts:
    dc1: {ansible_host: 172.21.50.1}
    app1: {ansible_host: 172.21.50.2}
    app2: {ansible_host: 172.21.50.3}
    web1: {ansible_host: 172.21.50.4, ansible_user: labape}
"""
PW = "ThisIsMy_FAKE_Password6!"


def _env():
    return Environment(id=5, name="lab5", status="deployed", kvm_host_id=1, created_by="t", spec=SPEC)


def test_who_connects_where():
    e = _env()
    t = lambda rest: sorted((x["vm"], x["protocol"]) for x in remote.account_targets(e, rest))  # noqa: E731
    assert t("vm/app1/windows_admin") == [("app1", "rdp")]                  # local Windows: its VM, RDP
    assert t("vm/app2/windows_admin2") == [("app2", "rdp")]
    assert t("vm/web1/account/tester") == [("web1", "ssh")]                 # local Linux: its VM, SSH
    assert t("domain_admin") == [("app1", "rdp"), ("app2", "rdp"), ("dc1", "rdp"), ("web1", "ssh")]
    assert t("account/jdoe") == [("app1", "rdp"), ("app2", "rdp"), ("web1", "ssh")]   # no DCs for a user
    assert t("account/boss") == [("app1", "rdp"), ("app2", "rdp"), ("dc1", "rdp"), ("web1", "ssh")]
    for rest in ("credentials", "inventory", "user/db-pass", "windows_admin", "rotation-candidate/windows_admin/app1"):
        assert t(rest) == []


def test_the_credentials_a_session_uses(db_reset):
    with session_factory()() as db:
        e = _env()
        db.add(e)
        db.commit()
        put_secret(db, f"environment/{e.id}/inventory", INV)
        for rest in ("vm/app1/windows_admin", "domain_admin", "account/jdoe", "vm/web1/account/tester"):
            put_secret(db, f"environment/{e.id}/{rest}", PW)
        local = remote.vm_target(db, e, "app1", "vm/app1/windows_admin", "rdp")["params"]
        assert (local["username"], local["domain"], local["password"]) == ("Administrator", "", PW)
        da = remote.vm_target(db, e, "dc1", "domain_admin", "rdp")["params"]
        assert (da["username"], da["domain"]) == ("Administrator", "LAB")
        ssh = remote.vm_target(db, e, "web1", "account/jdoe", "ssh")
        assert ssh["protocol"] == "ssh" and ssh["params"]["username"] == "jdoe@lab.test" and "private-key" not in ssh["params"]
        assert remote.vm_target(db, e, "web1", "vm/web1/account/tester", "ssh")["params"]["username"] == "tester"
        with pytest.raises(remote.RemoteError, match="can't sign in"):
            remote.vm_target(db, e, "dc1", "account/jdoe", "rdp")              # a user isn't offered the DC
        with pytest.raises(remote.RemoteError, match="can't sign in"):
            remote.vm_target(db, e, "app2", "vm/app1/windows_admin", "rdp")    # another VM's local account


def test_a_session_as_an_account(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = Environment(name="lab5", status="deployed", kvm_host_id=1, created_by="admin", spec=SPEC)
        db.add(e)
        db.commit()
        env_id = e.id
        put_secret(db, f"environment/{env_id}/inventory", INV)
        put_secret(db, f"environment/{env_id}/account/jdoe", PW)
    listed = {s["name"]: s for s in client.get(f"/api/environments/{env_id}/secrets").json()["secrets"]}
    assert {(t["vm"], t["protocol"]) for t in listed["account/jdoe"]["connect"]} == {("app1", "rdp"), ("app2", "rdp"),
                                                                                    ("web1", "ssh")}
    assert listed["inventory"]["connect"] == []
    r = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "ssh", "environment_id": env_id,
                                                  "vm": "web1", "account": "account/jdoe"})
    assert r.status_code == 200, r.text
    with session_factory()() as db:
        s = db.get(RemoteSession, r.json()["id"])
        assert s.account == "account/jdoe" and s.address == "172.21.50.4"
    bad = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "rdp", "environment_id": env_id,
                                                    "vm": "dc1", "account": "account/jdoe"})
    assert bad.status_code == 422 and "can't sign in" in bad.text


def test_the_user_name_to_type_is_not_the_secrets_name(db_reset):
    bs = chr(92)
    with session_factory()() as db:
        e = _env()
        db.add(e)
        db.commit()
        put_secret(db, f"environment/{e.id}/inventory", INV)
        netbios, dns = remote.domain_names(db, e)
        assert (netbios, dns) == ("LAB", "lab.test")
        login = lambda rest: remote.login_of(e, rest, netbios, dns)  # noqa: E731
        assert login("vm/app1/windows_admin") == f"APP1{bs}Administrator"          # a member's local account: its computer
        assert login("vm/app2/windows_admin2") == f"APP2{bs}labape-admin2"
        assert login("vm/dc1/windows_admin") == f"LAB{bs}Administrator"            # a controller has no local accounts
        assert login("domain_admin") == f"LAB{bs}Administrator"
        assert login("account/jdoe") == f"LAB{bs}jdoe"
        assert login("vm/web1/account/tester") == "tester"                         # Linux: the bare name
        assert login("credentials") == "" and login("inventory") == ""
        assert login("windows_admin") == f"<VM name>{bs}Administrator"              # the shared copy
        assert remote.rdp_domain(db, e, "app1") == "APP1" and remote.rdp_domain(db, e, "dc1") == "LAB"
        assert remote.computer_name("a-very-long-host-name-x") == "A-VERY-LONG-HOS"   # Windows keeps 15 characters


def test_the_secrets_list_carries_the_login(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={
        "name": "logins", "kvm_host_id": host_id, "host_groups": [{
            "name": "win", "os": "windows_server_2022", "roles": ["windows_server"], "image_source": "packer_template",
            "template": "t", "count": 1, "cpu_count": 2, "memory_mb": 4096, "disk_gb": 40, "addressing": "static"}]})
    if r.status_code != 201:
        pytest.skip("the creation form needs a template on the host: " + r.text[:100])
    env_id = r.json()["environment"]["id"]
    with session_factory()() as db:
        put_secret(db, f"environment/{env_id}/vm/win1/windows_admin", PW)
    rows = client.get(f"/api/environments/{env_id}/secrets").json()["secrets"]
    assert any(s["name"].endswith("windows_admin") and s["login"].endswith("Administrator") for s in rows)


def test_the_credentials_sheet_names_the_user_to_type():
    from labape.api.environments import parse_credentials, sheet_hints
    bs = chr(92)
    text = ("## Windows (app1, dc1)\nLocal Administrator: Administrator\nPassword: boot\n"
            "## Windows Administrator, per VM\n\napp1: s3cret\n")
    secs = {s["title"]: s for s in sheet_hints(parse_credentials(text))}
    assert secs["Windows (app1, dc1)"]["items"][0]["value"] == f".{bs}Administrator"
    per_vm = secs["Windows Administrator, per VM"]["items"]
    assert [(i["label"], i["value"], i["secret"]) for i in per_vm] == [
        ("app1 user name", f"APP1{bs}Administrator", False), ("app1", "s3cret", True)]
