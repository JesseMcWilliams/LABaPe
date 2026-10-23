"""A part can be pinned to a version of the template it includes (design §71)."""
import yaml

from labape.db import session_factory
from labape.models import EnvTemplateVersion

from test_api import _admin_session, _host

WEB = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "image_source": "packer_template",
       "template": "rocky9-base", "cpu_count": 2, "memory_mb": 2048}


def _part(client):
    r = client.post("/api/environments", json={"name": "src-part", "kvm_host_id": 1, "host_groups": [WEB], "draft": True})
    assert r.status_code == 201, r.text
    t = client.post("/api/env-templates", json={"name": "Web part", "shared": True, "from_environment_id": r.json()["environment"]["id"],
                                                "params": [{"group": "Web", "field": "count"}]})
    assert t.status_code == 201, t.text
    return t.json()["id"]


def _edit_part(client, tid, fields):
    """A change that is a new version: a different set of parameters."""
    cur = client.get(f"/api/env-templates/{tid}").json()
    r = client.put(f"/api/env-templates/{tid}", json={"name": cur["name"], "description": cur["description"], "shared": cur["shared"],
                                                      "params": [{"group": "Web", "field": f} for f in fields]})
    assert r.status_code == 200, r.text
    return r.json()


def _shop(client, part, version=None, name="Shop"):
    r = client.post("/api/env-templates", json={"name": name, "includes": [{"template_id": part, "prefix": "w", "version": version}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_content_change_is_a_new_version_and_a_rename_is_not(client):
    _admin_session(client)
    _host(client)
    part = _part(client)
    assert client.get(f"/api/env-templates/{part}").json()["version"] == 1
    renamed = client.put(f"/api/env-templates/{part}", json={"name": "Web part", "description": "now described", "shared": False,
                                                              "params": [{"group": "Web", "field": "count"}]})
    assert renamed.json()["version"] == 1                                             # description and sharing are not content
    assert _edit_part(client, part, ["count", "memory_mb"])["version"] == 2
    assert _edit_part(client, part, ["count", "memory_mb"])["version"] == 2           # the same again changes nothing
    with session_factory()() as db:
        assert [v.version for v in db.query(EnvTemplateVersion).filter_by(template_id=part).order_by(EnvTemplateVersion.version)] == [1, 2]
    starters = [t for t in client.get("/api/env-templates").json() if t["builtin"]]
    assert starters and all(t["version"] == 1 for t in starters)


def test_a_pinned_part_stays_on_its_version_while_a_following_one_moves(client):
    _admin_session(client)
    _host(client)
    part = _part(client)
    pinned = _shop(client, part, version=1, name="Pinned shop")
    following = _shop(client, part, name="Following shop")
    _edit_part(client, part, ["count", "memory_mb"])                                     # version 2 asks for memory too
    got = {t["name"]: t for t in client.get("/api/env-templates").json()}
    assert [p["key"] for p in got["Pinned shop"]["params"]] == ["wWeb.count"]
    assert [p["key"] for p in got["Following shop"]["params"]] == ["wWeb.count", "wWeb.memory_mb"]
    inc = got["Pinned shop"]["includes"][0]
    assert (inc["version"], inc["latest"], inc["behind"]) == (1, 2, True)
    assert got["Following shop"]["includes"][0]["behind"] is False
    out = client.post(f"/api/env-templates/{pinned['id']}/instantiate", json={}).json()
    assert out["host_groups"][0]["memory_mb"] == 2048
    assert following["id"] != pinned["id"]


def test_a_pin_must_name_a_version_that_exists(client):
    _admin_session(client)
    _host(client)
    part = _part(client)
    bad = client.post("/api/env-templates", json={"name": "Future", "includes": [{"template_id": part, "prefix": "w", "version": 4}]})
    assert bad.status_code == 422 and "no version 4" in bad.text
    assert client.post("/api/env-templates", json={"name": "Zero", "includes": [{"template_id": part, "prefix": "w", "version": 0}]}).status_code == 422
    # a pin can be moved on, or taken off, by editing the including template
    shop = _shop(client, part, version=1)
    _edit_part(client, part, ["count", "cpu_count"])
    moved = client.put(f"/api/env-templates/{shop['id']}", json={"name": "Shop", "includes": [{"template_id": part, "prefix": "w", "version": 2}]})
    assert moved.status_code == 200 and moved.json()["includes"][0]["behind"] is False
    assert moved.json()["version"] == 2                                                # the pin is part of the shop's content
    free = client.put(f"/api/env-templates/{shop['id']}", json={"name": "Shop", "includes": [{"template_id": part, "prefix": "w"}]})
    assert free.json()["includes"][0]["version"] is None


def test_an_export_writes_a_pinned_part_as_the_content_it_was_pinned_to(client):
    _admin_session(client)
    _host(client)
    part = _part(client)
    shop = _shop(client, part, version=1)
    _edit_part(client, part, ["count", "memory_mb"])
    text = client.get(f"/api/env-templates/{shop['id']}/export").text
    docs = yaml.safe_load(text)["templates"]
    assert [d["name"] for d in docs] == ["Web part v1", "Shop"]                          # the old content under its own name
    assert [q["field"] for q in docs[0]["params"]] == ["count"]
    assert docs[1]["includes"][0]["template"] == "Web part v1" and "version" not in docs[1]["includes"][0]
    for t in (shop["id"], part):
        client.delete(f"/api/env-templates/{t}")
    r = client.post("/api/env-templates/import", json={"yaml": text})
    assert r.status_code == 201, r.text
    again = next(t for t in client.get("/api/env-templates").json() if t["name"] == "Shop")
    assert [p["key"] for p in again["params"]] == ["wWeb.count"]                         # the same as the pinned original


def test_one_part_at_two_versions_is_two_documents_and_deleting_cleans_up(client):
    _admin_session(client)
    _host(client)
    part = _part(client)
    old = _shop(client, part, version=1, name="Old shop")
    _edit_part(client, part, ["count", "memory_mb"])
    new = _shop(client, part, name="New shop")
    both = client.post("/api/env-templates", json={"name": "Both", "includes": [
        {"template_id": old["id"], "prefix": "o"}, {"template_id": new["id"], "prefix": "n"}]})
    assert both.status_code == 201, both.text
    docs = yaml.safe_load(client.get(f"/api/env-templates/{both.json()['id']}/export").text)["templates"]
    assert [d["name"] for d in docs] == ["Web part v1", "Old shop", "Web part", "New shop", "Both"]
    for t in (both.json()["id"], old["id"], new["id"]):
        assert client.delete(f"/api/env-templates/{t}").status_code == 200
    assert client.delete(f"/api/env-templates/{part}").status_code == 200
    with session_factory()() as db:
        assert db.query(EnvTemplateVersion).filter_by(template_id=part).count() == 0
