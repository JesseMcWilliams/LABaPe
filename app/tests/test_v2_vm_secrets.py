"""V2 (design §49.3, questions 165 a, R21): VMs read their secrets with their platform certificate."""
from pathlib import Path

from labape import appsettings, openbao_vms
from labape.db import session_factory
from labape.models import Environment, Secret
from labape.security import put_secret

from test_api import _admin_session, _host

ROOT = Path(__file__).resolve().parents[2]
SPEC = {"host_groups": [{"name": "app", "os": "rocky9", "roles": ["linux_server"], "count": 2},
                        {"name": "win", "os": "windows_server_2022", "roles": ["windows_server"]}]}


def _on(db):
    cfg = appsettings.get(db, "store_openbao")
    appsettings.put(db, "store_openbao", cfg.model_copy(update={"enabled": True, "url": "https://labape-openbao:8200",
                                                                 "vm_url": "https://172.21.20.11:8210"}))


def _env(db):
    e = Environment(name="lab7", status="deployed", kvm_host_id=1, created_by="admin", spec=SPEC)
    db.add(e)
    db.commit()
    put_secret(db, f"environment/{e.id}/inventory", "all:\n  vars: {domain_name: lab.test}\n  hosts: {}\n")
    db.add(Secret(name=f"environment/{e.id}/windows_admin", value="", store="openbao"))
    db.commit()
    return e


def test_desired_and_asking_the_agent(db_reset, monkeypatch):
    asked = []
    monkeypatch.setattr(openbao_vms.update, "agent_installed", lambda: True)
    monkeypatch.setattr(openbao_vms.update, "busy", lambda: False)
    monkeypatch.setattr(openbao_vms.update, "write_request", lambda a, t, by, **kw: asked.append((a, kw["vms"])))
    with session_factory()() as db:
        assert openbao_vms.sync(db) == []                          # off without a VM address
        _on(db)
        e = _env(db)
        monkeypatch.setattr("labape.security.env_store", lambda db, env_id: "openbao")
        db.add_all([Secret(name=f"environment/{e.id}/user/db-pass", value="", store="openbao", vms=["app1", "app2"]),
                    Secret(name=f"environment/{e.id}/user/api-key", value="", store="openbao", vms=["win1"]),
                    Secret(name=f"environment/{e.id}/user/people-only", value="", store="openbao", vms=[])])
        db.commit()
        want = {v["vm"]: v for v in openbao_vms.desired(db)}
        assert want["app1"] == {"env": "lab7", "vm": "app1", "cn": "app1.lab.test", "user": ["db-pass"]}
        assert want["win1"]["user"] == ["api-key"] and want["app2"]["user"] == ["db-pass"]
        assert openbao_vms.sync(db) and len(asked) == 1 and asked[0][0] == "openbao_vms"
        assert openbao_vms.sync(db) == [] and len(asked) == 1      # nothing changed: not asked again
        db.get(Secret, f"environment/{e.id}/user/people-only").vms = ["app2"]
        db.commit()
        openbao_vms.sync(db)
        assert len(asked) == 2 and {v["vm"]: v["user"] for v in asked[1][1]}["app2"] == ["db-pass", "people-only"]
        hv = openbao_vms.host_vars(db, e)
        assert hv["app1"]["labape_secret_client"] == {"url": "https://172.21.20.11:8210", "role": "labape-vm-lab7-app1",
                                                      "mount": "labape", "env": "lab7", "vm": "app1"}


def test_the_readers_through_the_api(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = Environment(name="lab8", status="deployed", kvm_host_id=1, created_by="admin", spec=SPEC)
        db.add(e)
        db.commit()
        env_id = e.id
    base = f"/api/environments/{env_id}/secrets"
    r = client.put(base, json={"name": "db-pass", "value": "ThisIsMy_FAKE_Password6!", "readers": ["app1", "app2"]})
    assert r.status_code == 200, r.text
    assert client.put(base, json={"name": "x", "value": "y", "readers": ["web9"]}).status_code == 422
    assert client.put(base, json={"name": "x", "vm": "app1", "value": "y", "readers": ["app2"]}).status_code == 422
    assert client.put(base, json={"name": "new-one", "value": ""}).status_code == 422          # a new one needs a value
    r = client.put(base, json={"name": "db-pass", "value": "", "readers": ["app2"]})            # only the VMs change
    assert r.status_code == 200 and r.json()["replaced"] is True
    rows = {s["name"]: s for s in client.get(base).json()["secrets"]}
    assert rows["user/db-pass"]["readers"] == ["app2"]
    assert client.post(f"{base}/reveal", json={"name": "user/db-pass"}).json()["value"] == "ThisIsMy_FAKE_Password6!"


def test_the_template_and_the_helpers():
    sh = (ROOT / "container" / "stores" / "stores.sh").read_text(encoding="utf-8")
    assert 'path "labape/data/env/{env}/vm/{vm}/*" {{ capabilities = ["read"] }}' in sh
    assert 'path "labape/data/env/{env}/user/{u}" {{ capabilities = ["read"] }}' in sh
    assert '"allowed_common_names": [cn]' in sh and "auth/cert/certs/$name" in sh
    assert 'NAME = re.compile(r"^[a-z][a-z0-9-]{2,39}$")' in sh                               # every name checked
    hcl = (ROOT / "container" / "stores" / "openbao" / "config.hcl").read_text(encoding="utf-8")
    assert 'address       = "0.0.0.0:8210"' in hcl
    helper = (ROOT / "ansible" / "roles" / "labape_secret_client" / "files" / "labape-secret").read_text(encoding="utf-8")
    assert "-H @\"$HDR\"" in helper and "revoke-self" in helper                               # the token off the command line
    psm = (ROOT / "ansible" / "roles" / "labape_secret_client" / "files" / "LabapeSecret.psm1").read_bytes()
    assert psm.isascii()                                                                      # Windows PowerShell 5.1 reads it as ANSI
