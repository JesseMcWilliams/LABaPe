"""Template history: the versions of a template, what changed between two, and going back to one (design §73)."""
from labape.db import session_factory
from labape.models import EnvTemplate
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

WEB = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "image_source": "packer_template",
       "template": "rocky9-base", "cpu_count": 2, "memory_mb": 2048}


def _tpl(client, name="Web part", groups=None, params=("count",)):
    r = client.post("/api/environments", json={"name": f"src-{name.lower().replace(' ', '-')}", "kvm_host_id": 1,
                                               "host_groups": groups or [WEB], "draft": True})
    assert r.status_code == 201, r.text
    t = client.post("/api/env-templates", json={"name": name, "shared": True, "from_environment_id": r.json()["environment"]["id"],
                                                "params": [{"group": "Web", "field": f} for f in params]})
    assert t.status_code == 201, t.text
    return t.json()["id"]


def _set_params(client, tid, fields, **kw):
    cur = client.get(f"/api/env-templates/{tid}").json()
    body = {"name": cur["name"], "description": cur["description"], "shared": cur["shared"],
            "params": [{"group": "Web", "field": f} for f in fields], "includes": kw.get("includes", [])}
    r = client.put(f"/api/env-templates/{tid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_versions_are_listed_newest_first_with_who_and_what(client):
    _admin_session(client)
    _host(client)
    tid = _tpl(client)
    _set_params(client, tid, ["count", "memory_mb"])
    r = client.get(f"/api/env-templates/{tid}/versions").json()
    assert r["current"] == 2 and r["can_restore"] is True
    assert [v["version"] for v in r["versions"]] == [2, 1] and [v["current"] for v in r["versions"]] == [True, False]
    assert r["versions"][1]["params"] == 1 and r["versions"][0]["params"] == 2 and r["versions"][0]["created_by"]
    assert "Web 2×rocky9" in r["versions"][0]["summary"]


def test_the_diff_says_what_changed_by_name(client):
    _admin_session(client)
    _host(client)
    part = _tpl(client, "Db part")
    tid = _tpl(client, "Shop")
    _set_params(client, tid, ["count"])
    _set_params(client, tid, ["count", "memory_mb"], includes=[{"template_id": part, "prefix": "d"}])
    d = client.get(f"/api/env-templates/{tid}/diff?from=1&to=2").json()
    assert (d["from"], d["to"]) == (1, 2) and d["same"] is False
    assert d["params"]["added"] == ["Web.memory_mb"] and d["params"]["removed"] == []
    assert d["parts"]["added"] == ["d"] and d["groups"] == {"added": [], "removed": [], "changed": []}
    # a pin, an override and a label are changes to a part
    _set_params(client, tid, ["count", "memory_mb"], includes=[{"template_id": part, "prefix": "d", "label": "Database",
                                                                "overrides": {"Web": {"count": 3}}, "version": 1}])
    d2 = client.get(f"/api/env-templates/{tid}/diff").json()                           # defaults: the one before, to now
    assert (d2["from"], d2["to"]) == (2, 3)
    ch = d2["parts"]["changed"][0]
    assert ch["prefix"] == "d" and ch["from"]["version"] is None and ch["to"]["version"] == 1
    assert ch["to"]["label"] == "Database" and ch["to"]["overrides"] == {"Web": {"count": 3}}
    same = client.get(f"/api/env-templates/{tid}/diff?from=3&to=3").json()
    assert same["same"] is True
    assert client.get(f"/api/env-templates/{tid}/diff?from=1&to=9").status_code == 404


def test_a_group_change_is_shown_field_by_field(client):
    _admin_session(client)
    _host(client)
    tid = _tpl(client)
    with session_factory()() as db:                      # the groups come from the environment; change them as a new version
        from labape.api import env_templates as et
        t = db.get(EnvTemplate, tid)
        spec = {**t.spec, "host_groups": [{**t.spec["host_groups"][0], "count": 5, "memory_mb": 4096}]}
        t.spec = spec
        et.bump_if_changed(db, t, "tester")
        db.commit()
    d = client.get(f"/api/env-templates/{tid}/diff?from=1&to=2").json()
    assert d["groups"]["changed"] == [{"name": "Web", "fields": [{"field": "count", "from": 2, "to": 5},
                                                                  {"field": "memory_mb", "from": 2048, "to": 4096}]}]


def test_restoring_makes_the_old_content_a_new_version_and_keeps_the_history(client):
    _admin_session(client)
    _host(client)
    tid = _tpl(client)
    _set_params(client, tid, ["count", "memory_mb"])
    client.put(f"/api/env-templates/{tid}", json={"name": "Web part", "description": "kept", "shared": False,
                                                  "params": [{"group": "Web", "field": "count"}, {"group": "Web", "field": "memory_mb"}]})
    r = client.post(f"/api/env-templates/{tid}/restore", json={"version": 1})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["version"] == 3 and [q["key"] for q in t["own_params"]] == ["Web.count"]            # v1's content, as v3
    assert t["description"] == "kept" and t["shared"] is False                                  # not part of a version
    versions = client.get(f"/api/env-templates/{tid}/versions").json()["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1]                                       # nothing was rewritten
    assert client.get(f"/api/env-templates/{tid}/diff?from=1&to=3").json()["same"] is True
    again = client.post(f"/api/env-templates/{tid}/restore", json={"version": 1})
    assert again.status_code == 409 and "already is what version 1 held" in again.text
    assert client.post(f"/api/env-templates/{tid}/restore", json={"version": 9}).status_code == 404
    assert "env_template.restore" in [a["action"] for a in client.get("/api/admin/audit").json()]


def test_a_restore_that_needs_a_part_that_has_gone_is_refused_and_changes_nothing(client):
    _admin_session(client)
    _host(client)
    part = _tpl(client, "Db part")
    tid = _tpl(client, "Shop")
    _set_params(client, tid, ["count"], includes=[{"template_id": part, "prefix": "d"}])        # v2 includes the part
    _set_params(client, tid, ["count"], includes=[])                                           # v3 does not
    assert client.delete(f"/api/env-templates/{part}").status_code == 200                      # nothing includes it now
    bad = client.post(f"/api/env-templates/{tid}/restore", json={"version": 2})
    assert bad.status_code == 422 and "no longer exists" in bad.text
    cur = client.get(f"/api/env-templates/{tid}").json()
    assert cur["version"] == 3 and cur["includes"] == []


def test_who_may_restore(client):
    _admin_session(client)
    _host(client)
    tid = _tpl(client)
    _set_params(client, tid, ["count", "memory_mb"])
    client.app.dependency_overrides[current_principal] = lambda: Principal(user_id=99, username="someone", display_name="", roles={"deployer"})
    assert client.get(f"/api/env-templates/{tid}/versions").json()["can_restore"] is False       # shared, so visible, not theirs
    assert client.post(f"/api/env-templates/{tid}/restore", json={"version": 1}).status_code == 403
    client.app.dependency_overrides.clear()
    _admin_session(client)
    starter = next(t for t in client.get("/api/env-templates").json() if t["builtin"])
    assert client.get(f"/api/env-templates/{starter['id']}/versions").json()["versions"][0]["version"] == 1   # a starter's history can be read
    assert client.post(f"/api/env-templates/{starter['id']}/restore", json={"version": 1}).status_code == 403
