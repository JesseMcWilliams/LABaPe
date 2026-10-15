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
