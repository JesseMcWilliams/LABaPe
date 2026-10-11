"""Catalog: seed, validation, nesting, publish rules, import/export, requests, host groups, runner inputs."""
import yaml

from labape.db import session_factory
from labape.engine.runner import write_catalog_inputs
from labape.models import CatalogItem, Environment

from test_api import _admin_session, _host


def _item(client, kind, name, definition, publish=True):
    r = client.post("/api/catalog", json={"kind": kind, "name": name, "display_name": name, "definition": definition})
    assert r.status_code == 201, r.text
    if publish:
        assert client.put(f"/api/catalog/{r.json()['id']}/status", json={"status": "published"}).status_code == 200
    return r.json()["id"]


def test_seed_and_validation(client):
    _admin_session(client)
    names = {(i["kind"], i["name"]) for i in client.get("/api/catalog").json()}
    assert ("service", "iis") in names and ("service", "nginx") in names
    bad = [
        ("application", {}, "at least one install method"),
        ("application", {"windows": {"type": "zip", "source": "url", "url": "https://x"}}, "windows.type"),
        ("application", {"windows": {"type": "msi", "source": "url", "url": "https://x/a.msi"}}, "product_id"),
        ("service", {"windows": {}}, "features"),
        ("package", {"items": [{"kind": "application", "name": "nope"}]}, "no application named"),
    ]
    for kind, definition, text in bad:
        r = client.post("/api/catalog", json={"kind": kind, "name": f"bad-{kind}", "display_name": "x", "definition": definition})
        assert r.status_code == 422 and text in r.json()["errors"][0]["message"], (kind, r.text)


def test_package_nesting_and_publish_rules(client):
    _admin_session(client)
    _item(client, "application", "tool", {"apt": "tool", "dnf": "tool"})
    inner = _item(client, "package", "inner", {"items": [{"kind": "application", "name": "tool"}]})
    _item(client, "package", "outer", {"items": [{"kind": "package", "name": "inner"}, {"kind": "service", "name": "nginx"}]})
    r = client.post("/api/catalog", json={"kind": "package", "name": "toodeep", "display_name": "x",
                                          "definition": {"items": [{"kind": "package", "name": "outer"}]}})
    assert r.status_code == 422 and "one level" in r.json()["errors"][0]["message"]
    draft = _item(client, "application", "drafty", {"apt": "drafty"}, publish=False)
    pkg = _item(client, "package", "needsdraft", {"items": [{"kind": "application", "name": "drafty"}]}, publish=False)
    r = client.put(f"/api/catalog/{pkg}/status", json={"status": "published"})
    assert r.status_code == 422 and "Publish application drafty first" in r.json()["errors"][0]["message"]
    assert draft and inner


def test_export_import_roundtrip(client):
    _admin_session(client)
    _item(client, "application", "tool", {"apt": "tool"})
    pkg = _item(client, "package", "bundle", {"items": [{"kind": "application", "name": "tool"}]})
    r = client.get(f"/api/catalog/export?ids={pkg}")
    doc = yaml.safe_load(r.text)
    assert doc["labape_catalog"] == 1 and [a["name"] for a in doc["applications"]] == ["tool"]  # dependency included
    with session_factory()() as db:
        db.query(CatalogItem).filter(CatalogItem.name.in_(["tool", "bundle"])).delete()
        db.commit()
    r = client.post("/api/catalog/import?publish=true", files={"file": ("c.yaml", r.content, "application/yaml")})
    assert r.status_code == 200 and sorted(r.json()["created"]) == ["application:tool", "package:bundle"]
    r = client.post("/api/catalog/import", files={"file": ("c.yaml", b"not: a catalog", "application/yaml")})
    assert r.status_code == 422 and "labape_catalog" in r.json()["errors"][0]["message"]


def test_requests(client):
    _admin_session(client)
    r = client.post("/api/catalog/requests", json={"kind": "application", "title": "SQL Server Express", "details": "2022"})
    assert r.status_code == 201
    rid = r.json()["id"]
    assert client.put(f"/api/catalog/requests/{rid}", json={"status": "done", "response": "Published as sqlexpress"}).json()["status"] == "done"
    assert client.get("/api/catalog/requests").json()[0]["response"].startswith("Published")


def test_host_group_choices_and_runner_inputs(client, tmp_root):
    _admin_session(client)
    _host(client)
    _item(client, "application", "tool", {"apt": "tool", "dnf": "tool"})
    _item(client, "package", "webstack", {"items": [{"kind": "service", "name": "nginx"}, {"kind": "application", "name": "tool"}]})
    g = {"name": "web", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template", "template": "rocky9-base"}
    r = client.post("/api/environments", json={"name": "catenv", "kvm_host_id": 1,
                                               "host_groups": [{**g, "packages": ["webstack"], "services": ["cockpit"]}]})
    assert r.status_code == 201, r.text
    env_id = r.json()["environment"]["id"]
    # A Linux-only service on a Windows group, and an unpublished item, are refused.
    r = client.post("/api/environments", json={"name": "catenv2", "kvm_host_id": 1, "host_groups": [
        {**g, "name": "win", "os": "windows_server_2022", "roles": ["windows_server"], "services": ["nginx"]}]})
    assert r.status_code == 422 and "nothing to install on windows" in r.json()["errors"][0]["message"]
    r = client.post("/api/environments", json={"name": "catenv3", "kvm_host_id": 1, "host_groups": [
        {**g, "name": "x", "applications": ["missing"]}]})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.0.applications"
    work = tmp_root / "catwork"
    (work / "ansible").mkdir(parents=True, exist_ok=True)
    (work / "ansible" / "package_catalog.yml").write_text("packages:\n  firefox: {apt: firefox}\n")
    with session_factory()() as db:
        write_catalog_inputs(db, db.get(Environment, env_id), work)
    hv = yaml.safe_load((work / "ansible/inventory/host_vars/web1.yml").read_text())
    assert sorted(hv["labape_services"]) == ["cockpit", "nginx"] and hv["labape_extra_packages"] == ["tool"]
    cat = yaml.safe_load((work / "ansible/package_catalog.yml").read_text())["packages"]
    assert "firefox" in cat and cat["tool"] == {"apt": "tool", "dnf": "tool"}
    gv = yaml.safe_load((work / "ansible/inventory/group_vars/all/labape.yml").read_text())
    assert gv["software_store_path"] == "/data/installers"
