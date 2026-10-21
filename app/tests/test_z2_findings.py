"""Fixes from the 2026-10-20 manual checks: a published package keeps only published items; revoked and expired
API tokens are hidden from the list unless asked, and removed after the retention period."""
import datetime as dt

from labape.db import session_factory
from labape.engine import maintenance
from labape.models import ApiToken, utcnow

from test_a1_tokens import _admin_session
from test_catalog import _item


def test_saving_a_published_package_with_a_draft_item_is_refused(client):
    _admin_session(client)
    _item(client, "application", "tool", {"apt": "tool"})                      # published
    draft = _item(client, "application", "newtool", {"apt": "newtool"}, publish=False)
    pkg = _item(client, "package", "bundle", {"items": [{"kind": "application", "name": "tool"}]})     # published
    body = {"kind": "package", "name": "bundle", "display_name": "bundle",
            "definition": {"items": [{"kind": "application", "name": "tool"}, {"kind": "application", "name": "newtool"}]}}
    r = client.put(f"/api/catalog/{pkg}", json=body)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "items"
    assert "Publish application newtool first" in r.json()["errors"][0]["message"]
    got = client.get(f"/api/catalog/{pkg}").json()
    assert [i["name"] for i in got["definition"]["items"]] == ["tool"]               # unchanged
    # once the item is published the same save goes through; a draft package may hold a draft item
    assert client.put(f"/api/catalog/{draft}/status", json={"status": "published"}).status_code == 200
    assert client.put(f"/api/catalog/{pkg}", json=body).status_code == 200
    drafty = _item(client, "package", "wip", {"items": [{"kind": "application", "name": "tool"}]}, publish=False)
    other = _item(client, "application", "other", {"apt": "other"}, publish=False)
    assert other and client.put(f"/api/catalog/{drafty}", json={**body, "name": "wip", "display_name": "wip", "definition": {
        "items": [{"kind": "application", "name": "other"}]}}).status_code == 200


def test_revoked_and_expired_tokens_are_hidden_then_removed(client):
    _admin_session(client)
    live = client.post("/api/tokens", json={"name": "live", "roles": ["admin"]}).json()
    gone = client.post("/api/tokens", json={"name": "gone", "roles": ["admin"]}).json()
    old = client.post("/api/tokens", json={"name": "old", "roles": ["admin"]}).json()
    assert client.delete(f"/api/tokens/{gone['id']}").status_code == 200
    names = lambda q="": sorted(t["name"] for t in client.get("/api/tokens" + q).json()["tokens"])      # noqa: E731
    assert names() == ["live", "old"]                                              # the revoked one is hidden
    assert names("?inactive=true") == ["gone", "live", "old"]                      # shown on request
    with session_factory()() as db:                                                # "old" expired 40 days ago, "gone" was revoked 40 days ago
        t = db.get(ApiToken, old["id"])
        t.expires_at = utcnow() - dt.timedelta(days=40)
        g = db.get(ApiToken, gone["id"])
        g.revoked_at = utcnow() - dt.timedelta(days=40)
        db.commit()
    assert names() == ["live"] and names("?inactive=true") == ["gone", "live", "old"]
    with session_factory()() as db:
        assert maintenance.purge_history(db)["tokens"] == 2                        # past the 30-day default
    assert names("?inactive=true") == ["live"]
    s = client.get("/api/admin/settings").json()["retention"]
    assert s["inactive_tokens_days"] == 30 and live


def test_error_labels_match_the_token_dialog(client):
    _admin_session(client)
    r = client.post("/api/tokens", json={"name": "x", "roles": ["console"], "rate_per_minute": 0})
    assert r.status_code == 422 and r.json()["errors"][0]["label"] == "Calls per minute"
    r = client.post("/api/tokens", json={"name": "x", "roles": ["console"], "max_output_kb": 0})
    assert r.json()["errors"][0]["label"] == "Output KB"


def test_a_refused_account_is_named():
    from labape import env_accounts
    from labape.models import Environment
    env = Environment(name="e", spec={"host_groups": [{"name": "w", "count": 1, "roles": ["windows_server"]}]})
    try:
        env_accounts.validate(env, [{"name": "zzsudo", "kind": "local", "hosts": ["windows_server"], "groups": [],
                                     "sudo_nopasswd": True}])
    except env_accounts.AccountError as exc:
        assert "account 1 (zzsudo)" in str(exc) and "passwordless sudo" in str(exc)
    else:
        raise AssertionError("a Windows-only account with sudo was accepted")
