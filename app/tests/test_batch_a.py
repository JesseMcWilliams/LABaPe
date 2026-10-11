"""Batch A: VM names, template checks, clear errors and their log, audit export, credentials."""
import json

from labape.api.environments import parse_credentials
from labape.db import session_factory
from labape.engine.templates import infer
from labape.models import Template, UserError

from test_api import _admin_session, _host


def _group(name, **kw):
    return {"name": name, "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template",
            "template": "rocky9-base", **kw}


def _env(name, groups):
    return {"name": name, "kvm_host_id": 1, "host_groups": groups}


def test_custom_names_and_host_wide_uniqueness(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("one", [_group("app", count=3, names=["web-a", "", "web-c"])]))
    assert r.status_code == 201, r.text
    assert sorted(r.json()["environment"]["addresses"]) == ["app2", "web-a", "web-c"]
    # Another environment on the same host can't reuse a VM name...
    r = client.post("/api/environments", json=_env("two", [_group("x", names=["web-a"])]))
    assert r.status_code == 422
    body = r.json()
    assert body["errors"][0]["field"] == "host_groups.0.names.0"
    assert "already used by environment one" in body["errors"][0]["message"]
    # ...nor a generated one.
    r = client.post("/api/environments", json=_env("three", [_group("app", count=2)]))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.0.name"
    # More names than hosts, and an invalid host name.
    r = client.post("/api/environments", json=_env("four", [_group("q", count=1, names=["a1", "a2"])]))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.0.names"
    r = client.post("/api/environments", json=_env("five", [_group("q", names=["9bad"])]))
    assert r.status_code == 422 and "15" in r.json()["errors"][0]["message"]


def test_template_must_exist_and_match_os(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        db.add(Template(kvm_host_id=1, name="win2022-core-2026.10", os_key="windows_server_2022", disk_gb=60))
        db.commit()
    r = client.post("/api/environments", json=_env("tpl1", [_group("a", template="win2022-core-2026.10")]))
    assert r.status_code == 422 and "holds windows_server_2022" in r.json()["errors"][0]["message"]
    r = client.post("/api/environments", json=_env("tpl2", [_group("a", template="nope")]))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.0.template"
    assert client.get("/api/templates?kvm_host_id=1&os=windows_server_2022").json()[0]["disk_gb"] == 60


def test_validation_errors_are_clear_and_logged(client):
    _admin_session(client)
    r = client.post("/api/networks", json={"name": "Bad Name", "cidr": "172.21.48.0/22"})
    assert r.status_code == 422
    err = r.json()["errors"][0]
    assert err["field"] == "name" and err["label"] == "Name"
    assert "lower-case letters, digits and hyphens" in err["message"]
    r = client.post("/api/networks", json={"name": "n1", "cidr": "172.21.48.0/22", "addressing": ["static"],
                                           "static_pools": ["172.21.48.10-172.21.48.20"],
                                           "dhcp_ranges": ["172.21.48.16-172.21.49.254"]})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "static_pools" and "DHCP scope" in r.json()["errors"][0]["message"]
    client.post("/api/errors/report", json={"page": "/networks", "fields": [
        {"field": "cidr", "label": "CIDR", "message": "Use an IPv4 network such as 172.21.48.0/22."}]})
    logged = client.get("/api/admin/errors").json()
    assert logged["retention_days"] == 90
    origins = {e["origin"] for e in logged["errors"]}
    assert origins == {"server", "browser"}
    assert client.put("/api/admin/errors/retention", json={"days": 30}).json()["retention_days"] == 30
    with session_factory()() as db:
        assert db.query(UserError).filter(UserError.username == "breakglass").count() >= 3


def test_audit_export_json(client):
    _admin_session(client)
    r = client.get("/api/admin/audit/export?format=json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert any(row["action"] == "auth.breakglass" for row in json.loads(r.text))
    assert client.get("/api/admin/audit/export?format=csv").status_code == 422


def test_parse_credentials():
    text = ("# LABaPe: generated\n## Windows (dc1, ws1)\nLocal Administrator: Administrator\nPassword: s3cret!\n"
            "Also valid as Domain Administrator (LAB\\\\Administrator) on domain-joined hosts.\n\n"
            "## Linux (lin1)\nUser: labape (SSH key auth)\nssh -i /k labape@<host-ip>\n")
    secs = parse_credentials(text)
    assert [s["title"] for s in secs] == ["Windows (dc1, ws1)", "Linux (lin1)"]
    pw = [i for i in secs[0]["items"] if i["label"] == "Password"][0]
    assert pw["secret"] and pw["value"] == "s3cret!"
    assert secs[1]["notes"] == ["ssh -i /k labape@<host-ip>"]


def test_template_inference():
    assert infer("win2022-core-2026.10")["os_key"] == "windows_server_2022"
    assert infer("win2022-core-2026.10")["edition"] == "core"
    assert infer("win11-packer-2026.10")["min_memory_mb"] == 4096
    assert infer("rocky9-packer-2026.10.1")["os_key"] == "rocky9"
    assert infer("ubuntu2604-packer-2026.10")["os_key"] == "ubuntu_26"
