"""Environment templates made of other templates, and YAML export and import (design §69)."""
import yaml

from labape.db import session_factory
from labape.models import EnvTemplate

from test_api import _admin_session, _host

WEB = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "image_source": "packer_template",
       "template": "rocky9-base", "cpu_count": 2, "memory_mb": 2048}
DB = {"name": "Db", "os": "rocky9", "roles": ["linux_server"], "count": 1, "image_source": "packer_template",
      "template": "rocky9-base", "cpu_count": 4, "memory_mb": 8192}


def _env(client, name, groups):
    r = client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": groups, "draft": True})
    assert r.status_code == 201, r.text
    return r.json()["environment"]["id"]


def _tpl(client, name, groups, field_params=(), **kw):
    eid = _env(client, f"src-{name.lower().replace(' ', '-')}", groups)
    r = client.post("/api/env-templates", json={"name": name, "from_environment_id": eid, "shared": True,
                                                "params": [{"group": g, "field": f} for g, f in field_params], **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client):
    _admin_session(client)
    _host(client)
    web = _tpl(client, "Web part", [WEB], [("Web", "count")])
    db = _tpl(client, "Db part", [DB])
    return web, db


def test_a_template_made_of_parts_adds_their_groups_with_prefixes_and_parameters(client):
    web, db = _setup(client)
    r = client.post("/api/env-templates", json={"name": "Shop", "shared": True, "includes": [
        {"template_id": web, "prefix": "front", "label": "Front"}, {"template_id": web, "prefix": "back"},
        {"template_id": db, "prefix": "s"}]})
    assert r.status_code == 201, r.text
    t = r.json()
    assert [g["name"] for g in t["summary"]] == ["frontWeb", "backWeb", "sDb"]
    assert [p["key"] for p in t["params"]] == ["frontWeb.count", "backWeb.count"]
    assert t["params"][0]["label"] == "Front: Number of hosts in Web" and t["params"][1]["label"].startswith("back: ")
    got = client.post(f"/api/env-templates/{t['id']}/instantiate", json={"values": {"backWeb.count": "4"}}).json()
    assert [(g["name"], g["count"]) for g in got["host_groups"]] == [("frontWeb", 2), ("backWeb", 4), ("sDb", 1)]
    assert got["host_groups"][2]["memory_mb"] == 8192                                              # the part's own sizes carry over


def test_the_including_template_can_fix_values_of_a_part(client):
    web, _ = _setup(client)
    r = client.post("/api/env-templates", json={"name": "Big web", "includes": [
        {"template_id": web, "prefix": "w", "overrides": {"Web": {"count": 6, "memory_mb": 4096}}}]})
    t = r.json()
    assert t["params"][0]["default"] == 6
    g = client.post(f"/api/env-templates/{t['id']}/instantiate", json={}).json()["host_groups"][0]
    assert (g["count"], g["memory_mb"]) == (6, 4096)
    bad = client.post("/api/env-templates", json={"name": "Odd web", "includes": [
        {"template_id": web, "prefix": "w", "overrides": {"Web": {"roles": "x"}}}]})
    assert bad.status_code == 422 and "can't be set" in bad.text


def test_a_template_that_would_not_work_is_refused_when_saved(client):
    web, db = _setup(client)
    same = client.post("/api/env-templates", json={"name": "Twice", "includes": [
        {"template_id": web, "prefix": "a"}, {"template_id": db, "prefix": "a"}]})
    assert same.status_code == 422 and "used twice" in same.text
    clash = client.post("/api/env-templates", json={"name": "Clash", "includes": [{"template_id": web, "prefix": "x"}],
                                                    "from_environment_id": _env(client, "src-clash", [{**WEB, "name": "xWeb"}])})
    assert clash.status_code == 422 and "comes out twice" in clash.text
    wide = _tpl(client, "Wide part", [{**WEB, "name": "Webservers"}])
    long = client.post("/api/env-templates", json={"name": "Long", "includes": [{"template_id": wide, "prefix": "abc"}]})
    assert long.status_code == 422 and "longer than 12" in long.text
    assert client.post("/api/env-templates", json={"name": "Empty"}).status_code == 422
    assert client.post("/api/env-templates", json={"name": "Ghost", "includes": [{"template_id": 9999, "prefix": "g"}]}).status_code == 404


def test_templates_cannot_include_each_other_in_a_circle_or_be_deleted_while_used(client):
    web, db = _setup(client)
    shop = client.post("/api/env-templates", json={"name": "Shop", "includes": [{"template_id": web, "prefix": "w"}]}).json()["id"]
    # the part now includes the whole: a circle
    circ = client.put(f"/api/env-templates/{web}", json={"name": "Web part", "shared": True,
                                                         "includes": [{"template_id": shop, "prefix": "s"}]})
    assert circ.status_code == 422 and "circle" in circ.text
    assert client.get(f"/api/env-templates/{web}").json()["includes"] == []                          # nothing was saved
    gone = client.delete(f"/api/env-templates/{web}")
    assert gone.status_code == 409 and "Shop" in gone.text
    assert client.get(f"/api/env-templates/{web}").json()["used_by"] == ["Shop"]
    assert client.delete(f"/api/env-templates/{shop}").status_code == 200
    assert client.delete(f"/api/env-templates/{web}").status_code == 200


def test_two_domain_controllers_from_two_parts_is_a_warning(client):
    _admin_session(client)
    _host(client)
    dc = {"name": "Dc", "os": "windows_server_2022", "roles": ["domain_controller"], "image_source": "packer_template",
          "template": "win-base"}
    part = _tpl(client, "Dc part", [dc])
    r = client.post("/api/env-templates", json={"name": "Two forests", "includes": [
        {"template_id": part, "prefix": "a"}, {"template_id": part, "prefix": "b"}]})
    assert r.status_code == 201, r.text
    out = client.post(f"/api/env-templates/{r.json()['id']}/instantiate", json={}).json()
    assert any("domain controller" in w["message"] for w in out["warnings"])


def test_export_then_import_on_another_stack(client):
    web, db = _setup(client)
    shop = client.post("/api/env-templates", json={"name": "Shop", "description": "web and db", "shared": True, "includes": [
        {"template_id": web, "prefix": "w", "overrides": {"Web": {"count": 3}}}, {"template_id": db, "prefix": "d"}]}).json()["id"]
    one = client.get(f"/api/env-templates/{shop}/export")
    assert one.status_code == 200 and "attachment" in one.headers["content-disposition"]
    doc = yaml.safe_load(one.text)
    assert doc["format"] == "labape-environment-templates/1"
    assert [t["name"] for t in doc["templates"]] == ["Web part", "Db part", "Shop"]                # parts first, so it imports alone
    assert doc["templates"][2]["includes"][0] == {"template": "Web part", "prefix": "w", "overrides": {"Web": {"count": 3}}}
    assert "names" not in doc["templates"][0]["host_groups"][0]
    everything = yaml.safe_load(client.get("/api/env-templates/export").text)["templates"]
    assert {t["name"] for t in everything} == {"Web part", "Db part", "Shop"}                       # the starters are not exported
    # "another stack": remove them, then import the file
    for tid in (shop, web, db):
        assert client.delete(f"/api/env-templates/{tid}").status_code == 200
    r = client.post("/api/env-templates/import", json={"yaml": one.text, "shared": True})
    assert r.status_code == 201, r.text
    assert [c["name"] for c in r.json()["created"]] == ["Web part", "Db part", "Shop"]
    new = next(t for t in client.get("/api/env-templates").json() if t["name"] == "Shop")
    assert [g["name"] for g in new["summary"]] == ["wWeb", "dDb"] and new["params"][0]["default"] == 3
    again = client.post("/api/env-templates/import", json={"yaml": one.text})
    assert again.status_code == 422 and "already exists" in again.text                              # names are not overwritten


def test_a_bad_import_creates_nothing(client):
    _setup(client)
    before = len(client.get("/api/env-templates").json())
    bad = {"format": "labape-environment-templates/1", "templates": [
        {"name": "Fine one", "host_groups": [{"name": "A", "os": "rocky9", "roles": ["linux_server"]}]},
        {"name": "Broken one", "host_groups": [{"name": "B", "os": "rocky9", "roles": ["nonsense"]}]}]}
    r = client.post("/api/env-templates/import", json={"yaml": yaml.safe_dump(bad)})
    assert r.status_code == 422 and "Broken one" in r.text
    assert len(client.get("/api/env-templates").json()) == before                                   # the good one was rolled back too
    assert client.post("/api/env-templates/import", json={"yaml": "{{ nope"}).status_code == 422
    assert client.post("/api/env-templates/import", json={"yaml": "just: text"}).status_code == 422
    missing = {"templates": [{"name": "Needs part", "includes": [{"template": "Not here", "prefix": "n"}]}]}
    m = client.post("/api/env-templates/import", json={"yaml": yaml.safe_dump(missing)})
    assert m.status_code == 422 and "import it first" in m.text
    wrong = client.post("/api/env-templates/import", json={"yaml": yaml.safe_dump({"format": "other/9", "templates": [{}]})})
    assert wrong.status_code == 422 and "other/9" in wrong.text


def test_an_import_of_a_starter_name_is_refused(client):
    _admin_session(client)
    client.get("/api/env-templates")
    doc = {"templates": [{"name": "Kerberos lab", "host_groups": [{"name": "A", "os": "rocky9", "roles": ["linux_server"]}]}]}
    assert client.post("/api/env-templates/import", json={"yaml": yaml.safe_dump(doc)}).status_code == 422
    with session_factory()() as db:
        assert db.query(EnvTemplate).filter_by(name="Kerberos lab").count() == 1
