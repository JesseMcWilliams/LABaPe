"""Batch E3 (design §42): an environment's own domain and local accounts."""
import yaml

import pytest

from labape import env_accounts as EA
from labape.db import session_factory
from labape.engine import rotation, vmaccess
from labape.models import Environment, SecretPolicy
from labape.security import get_secret, put_secret

from test_api import _admin_session, _host

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "count": 1, "roles": ["domain_controller"]},
                        {"name": "web", "os": "rocky9", "count": 2, "roles": ["linux_server"]}]}


def _env(db, **kw):
    e = Environment(name="lab9", kvm_host_id=1, created_by="admin", status="deployed", spec=dict(SPEC), **kw)
    db.add(e)
    db.commit()
    return e


def test_validation_and_passwords(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = _env(db)
        for bad, msg in (([{"name": "Administrator", "kind": "domain"}], "built-in|lowercase"),
                         ([{"name": "sam", "kind": "local", "hosts": ["windows_server"]}], "roles"),
                         ([{"name": "sam", "kind": "domain"}, {"name": "sam", "kind": "domain"}], "twice"),
                         ([{"name": "sam", "kind": "remote"}], "kind")):
            with pytest.raises(EA.AccountError, match=msg):
                EA.set_accounts(db, e, bad)
        items = EA.set_accounts(db, e, [{"name": "jdoe", "kind": "domain", "display_name": "Jane", "groups": ["Domain Admins"]},
                                        {"name": "tester", "kind": "local", "hosts": ["linux_server"]}])
        assert [a["name"] for a in items] == ["jdoe", "tester"] and e.spec["accounts"] == items
        pw = get_secret(db, EA.secret_name(e.id, "jdoe"))
        assert len(pw) == 20
        EA.set_accounts(db, e, [{"name": "jdoe", "kind": "domain"}])          # kept: same password; tester forgotten
        assert get_secret(db, EA.secret_name(e.id, "jdoe")) == pw and not get_secret(db, EA.secret_name(e.id, "tester"))
        e2 = Environment(name="nodc", kvm_host_id=1, created_by="admin", status="new",
                         spec={"host_groups": [SPEC["host_groups"][1]]})
        db.add(e2)
        db.commit()
        with pytest.raises(EA.AccountError, match="domain controller"):
            EA.set_accounts(db, e2, [{"name": "jdoe", "kind": "domain"}])


def test_deploy_manifest_vault_and_sheet(client, tmp_path):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = _env(db)
        EA.set_accounts(db, e, [{"name": "jdoe", "kind": "domain", "display_name": "Jane"},
                                {"name": "tester", "kind": "local", "hosts": ["linux_server"], "groups": ["wheel"]}])
        m = tmp_path / "ansible" / "directory-manifest.yml"
        m.parent.mkdir()
        m.write_text(yaml.safe_dump({"domain_users": [{"name": "global1", "ou": "OU=X"}]}))
        assert EA.merge_manifest(m, e, "labape.test") == 2
        data = yaml.safe_load(m.read_text())
        assert [u["name"] for u in data["domain_users"]] == ["global1", "jdoe"]
        assert data["domain_users"][1]["ou"] == "CN=Users,DC=labape,DC=test"
        assert data["local_users"][0] == {"name": "tester", "hosts": ["linux_server"],
                                          "password_vault_key": "labape_account_tester_password", "groups": ["wheel"]}
        v = EA.vault_overrides(db, e)
        assert v["labape_account_jdoe_password"] == get_secret(db, EA.secret_name(e.id, "jdoe"))
        sheet = EA.apply_sheet("## Windows\nUser: Administrator\nPassword: x\n", EA.sheet_section(db, e, "LABAPE"))
        assert "User: LABAPE\\jdoe (domain)" in sheet and "User: tester (local on linux_server)" in sheet
        assert EA.apply_sheet(sheet, EA.sheet_section(db, e, "LABAPE")).count("## Environment accounts") == 1
        assert "## Environment accounts" not in EA.apply_sheet(sheet, "")


def test_rotation_policy_and_rotate(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = _env(db)
        EA.set_accounts(db, e, [{"name": "tester", "kind": "local", "hosts": ["linux_server"]}])
        inv = {"all": {"hosts": {"web1": {}, "web2": {}, "dc1": {"ansible_connection": "winrm"}},
                       "children": {"linux_server": {"hosts": {"web1": {}, "web2": {}}},
                                    "domain_controller": {"hosts": {"dc1": {}}}}}}
        put_secret(db, f"environment/{e.id}/inventory", yaml.safe_dump(inv))
        put_secret(db, f"environment/{e.id}/credentials", "## Windows\nPassword: x\n")
        rotation.ensure_env_policies(db)
        p = db.query(SecretPolicy).filter_by(name=f"env.{e.id}.account.tester").one()
        assert p.kind == "env_account" and rotation.policy_secret(p) == EA.secret_name(e.id, "tester")
        calls = []

        def fake(inv_, pattern, module, args, extra=None, **kw):
            calls.append((pattern, module))
            return {h: "changed" for h in pattern.split(":")}
        monkeypatch.setattr(vmaccess, "_ansible", fake)
        old = get_secret(db, EA.secret_name(e.id, "tester"))
        log = type("L", (), {"write": lambda self, m: None})()
        assert EA.rotate(db, e, "tester", log) == "2 host(s) updated"
        assert calls == [("web1:web2", "ansible.builtin.user")]
        new = get_secret(db, EA.secret_name(e.id, "tester"))
        assert new != old and new in get_secret(db, f"environment/{e.id}/credentials")
        monkeypatch.setattr(vmaccess, "_ansible", lambda inv_, pattern, *a, **k: {h: "failed" for h in pattern.split(":")})
        with pytest.raises(vmaccess.AccessError, match="couldn't set tester"):
            EA.rotate(db, e, "tester", log)
        assert get_secret(db, EA.secret_name(e.id, "tester")) == new          # unchanged on failure


def test_the_api(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        eid = _env(db).id
    r = client.get(f"/api/environments/{eid}/accounts").json()
    assert r["accounts"] == [] and r["roles"] == ["domain_controller", "linux_server"] and r["can_edit"]
    bad = client.put(f"/api/environments/{eid}/accounts", json={"accounts": [{"name": "x y", "kind": "domain"}]})
    assert bad.status_code == 422
    ok = client.put(f"/api/environments/{eid}/accounts", json={"accounts": [{"name": "jdoe", "kind": "domain"}]})
    assert ok.status_code == 200 and ok.json()["accounts"][0]["name"] == "jdoe"
