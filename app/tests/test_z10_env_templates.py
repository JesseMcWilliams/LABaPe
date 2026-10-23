"""Environment templates (design §68): save an environment, start from one with parameters, say what is missing, the
shipped starters, and who may change what."""
from labape.db import session_factory
from labape.models import CatalogItem, EnvTemplate, Template
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

G = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "image_source": "packer_template",
     "template": "rocky9-base", "names": ["w1", "w2"], "cpu_count": 2, "memory_mb": 2048}


def _env(client, name="tpl-src", groups=None):
    r = client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": groups or [G], "draft": True})
    assert r.status_code == 201, r.text
    return r.json()["environment"]["id"]


def _save(client, eid, name="Web pair", **kw):
    return client.post("/api/env-templates", json={"name": name, "from_environment_id": eid,
                                                   "params": [{"group": "Web", "field": "count"}], **kw})


def test_saving_drops_what_belongs_to_one_deployment(client):
    _admin_session(client)
    _host(client)
    r = _save(client, _env(client), description="two web hosts", shared=True)
    assert r.status_code == 201, r.text
    t = r.json()
    g = t["spec"]["host_groups"][0]
    assert "names" not in g and g["count"] == 2 and g["template"] == "rocky9-base" and g["memory_mb"] == 2048
    assert t["params"] == [{"key": "Web.count", "group": "Web", "field": "count", "default": 2,
                            "label": "Number of hosts in Web"}]
    assert _save(client, _env(client, "tpl-src2")).status_code == 422           # the name is taken
    assert client.post("/api/env-templates", json={"name": "No source"}).status_code == 422
    assert client.post("/api/env-templates", json={"name": "x", "from_environment_id": 1}).status_code == 422   # a bad name
    bad = client.post("/api/env-templates", json={"name": "Bad param", "from_environment_id": _env(client, "tpl-src3"),
                                                  "params": [{"group": "Nope", "field": "count"}]})
    assert bad.status_code == 422


def test_starting_from_a_template_applies_the_values_and_checks_them(client):
    _admin_session(client)
    _host(client)
    tid = _save(client, _env(client)).json()["id"]
    ok = client.post(f"/api/env-templates/{tid}/instantiate", json={"values": {"Web.count": "5"}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["host_groups"][0]["count"] == 5 and ok.json()["host_groups"][0]["names"] == []
    assert client.post(f"/api/env-templates/{tid}/instantiate", json={}).json()["host_groups"][0]["count"] == 2   # the default
    too_many = client.post(f"/api/env-templates/{tid}/instantiate", json={"values": {"Web.count": "99"}})
    assert too_many.status_code == 422 and "Host group Web" in too_many.text
    assert client.post(f"/api/env-templates/{tid}/instantiate", json={"values": {"Web.count": "x"}}).status_code == 422


def test_it_says_what_the_host_or_the_catalog_lacks_and_fills_an_os_template(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        db.add(CatalogItem(kind="application", name="ghost-app", display_name="Ghost", status="published",
                           definition={"dnf": "ghost"}))
        db.commit()
    groups = [{**G, "template": None, "image_source": None, "applications": ["ghost-app"]}]
    tid = _save(client, _env(client, "tpl-n", groups)).json()["id"]
    with session_factory()() as db:
        db.query(CatalogItem).filter_by(name="ghost-app").one().status = "retired"      # gone from the catalog since
        db.add(Template(kvm_host_id=1, name="rocky9-2026.10", os_key="rocky9"))
        db.add(Template(kvm_host_id=1, name="rocky9-2026.09", os_key="rocky9"))
        db.commit()
    r = client.post(f"/api/env-templates/{tid}/instantiate", json={"kvm_host_id": 1}).json()
    g = r["host_groups"][0]
    assert not g["template"]                                                                    # two candidates: the person picks
    assert any("2 templates for rocky9" in w["message"] and w["level"] == "info" for w in r["warnings"])
    assert any("ghost-app" in w["message"] and w["level"] == "warn" for w in r["warnings"])
    with session_factory()() as db:
        db.query(Template).filter_by(name="rocky9-2026.09").one().present = False               # one left (the other is gone)
        db.commit()
    g = client.post(f"/api/env-templates/{tid}/instantiate", json={"kvm_host_id": 1}).json()["host_groups"][0]
    assert (g["image_source"], g["template"]) == ("packer_template", "rocky9-2026.10")
    gone = client.post(f"/api/env-templates/{_save(client, _env(client, 'tpl-m'), name='Missing image').json()['id']}/instantiate",
                       json={"kvm_host_id": 1}).json()
    assert any("rocky9-base" in w["message"] for w in gone["warnings"])                          # the host has no such image


def test_the_starters_are_shipped_and_cannot_be_changed(client):
    _admin_session(client)
    rows = client.get("/api/env-templates").json()
    names = [t["name"] for t in rows]
    assert {"Domain with members", "Three-tier Linux (standalone)", "Kerberos lab"} <= set(names)
    assert all(t["builtin"] and not t["can_edit"] for t in rows if t["name"] in names[:3])
    domain = next(t for t in rows if t["name"] == "Domain with members")
    assert [g["role"] for g in domain["summary"]][0] == "domain_controller"
    assert client.delete(f"/api/env-templates/{domain['id']}").status_code == 403
    assert client.put(f"/api/env-templates/{domain['id']}", json={"name": "Renamed it"}).status_code == 403
    inst = client.post(f"/api/env-templates/{domain['id']}/instantiate", json={"values": {"linsrv.count": "3"}})
    assert inst.status_code == 200 and [g["count"] for g in inst.json()["host_groups"]] == [1, 1, 3]
    three = next(t for t in rows if t["name"].startswith("Three-tier"))
    got = client.post(f"/api/env-templates/{three['id']}/instantiate", json={}).json()["host_groups"]
    assert all(g["domain_member"] is False for g in got)
    client.get("/api/env-templates")                                                             # listing twice adds nothing
    with session_factory()() as db:
        assert db.query(EnvTemplate).filter_by(builtin=True).count() == 3


def test_who_sees_and_changes_a_template(client):
    _admin_session(client)
    _host(client)
    private = _save(client, _env(client, "tpl-p"), name="Private one").json()["id"]
    shared = _save(client, _env(client, "tpl-s"), name="Shared one", shared=True).json()["id"]
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username="someone", display_name="", roles={"deployer"})
    seen = {t["name"] for t in client.get("/api/env-templates").json()}
    assert "Shared one" in seen and "Private one" not in seen
    assert client.get(f"/api/env-templates/{private}").status_code == 404
    assert client.get(f"/api/env-templates/{shared}").status_code == 200
    assert client.delete(f"/api/env-templates/{shared}").status_code == 403                     # not theirs
    client.app.dependency_overrides.clear()
    _admin_session(client)
    upd = client.put(f"/api/env-templates/{private}", json={"name": "Private renamed", "shared": True,
                                                              "params": [{"group": "Web", "field": "cpu_count", "label": "CPUs"}]})
    assert upd.status_code == 200 and upd.json()["shared"] and upd.json()["params"][0]["label"] == "CPUs"
    assert client.delete(f"/api/env-templates/{private}").status_code == 200
    assert client.get(f"/api/env-templates/{private}").status_code == 404
