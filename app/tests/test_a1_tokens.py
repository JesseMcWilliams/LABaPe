"""Batch A1 (design §37): personal and service API tokens."""
import datetime as dt

from labape.db import session_factory
from labape.models import ApiToken, AuditLog, RoleBinding, User
from labape.security import Principal, current_principal


def _admin_session(client):
    """A normal (not break-glass) admin: a user with an admin binding, signed in."""
    with session_factory()() as db:
        u = db.query(User).filter_by(username="alice").one_or_none()
        if u is None:
            u = User(provider="oidc", subject="alice", username="alice", display_name="Alice", groups=[])
            db.add(u)
            db.add(RoleBinding(principal_type="user", principal="alice", role="admin"))
            db.commit()
        uid = u.id
    client.app.dependency_overrides[current_principal] = lambda: Principal(uid, "alice", "Alice", [], {"admin"})


def _bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def _no_session(client):
    client.app.dependency_overrides.pop(current_principal, None)
    client.cookies.clear()


def test_a_personal_token_acts_as_its_owner_within_its_roles(client):
    _admin_session(client)
    r = client.post("/api/tokens", json={"name": "ci", "roles": ["viewer"], "days": 30})
    assert r.status_code == 400                                         # alice is admin only: no viewer role
    r = client.post("/api/tokens", json={"name": "ci", "roles": ["admin"], "days": 30})
    assert r.status_code == 200 and r.json()["secret"].startswith("lpat_") and r.json()["state"] == "active"
    secret, tid = r.json()["secret"], r.json()["id"]
    assert "secret" not in client.get("/api/tokens").json()["tokens"][0]
    _no_session(client)
    me = client.get("/api/hosts", headers=_bearer(secret))
    assert me.status_code == 200
    assert client.get("/api/admin/settings", headers=_bearer(secret)).status_code == 200    # alice's admin role
    assert client.get("/api/hosts", headers=_bearer("lpat_nope")).status_code == 401
    assert client.post("/api/tokens", json={"name": "x"}, headers=_bearer(secret)).status_code == 403  # not from a token
    with session_factory()() as db:
        t = db.get(ApiToken, tid)
        assert t.last_used_at is not None and t.token_hash != secret
        assert db.query(AuditLog).filter_by(action="token.use").count() == 1               # the refused POST was a write
        t.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    assert client.get("/api/hosts", headers=_bearer(secret)).status_code == 401              # expired


def test_roles_limits_and_service_tokens(client):
    _admin_session(client)
    assert client.post("/api/tokens", json={"name": "x", "days": 400}).status_code == 422
    assert client.post("/api/tokens", json={"name": "x", "roles": ["wizard"]}).status_code == 400
    assert client.post("/api/tokens", json={"name": "svc", "kind": "service"}).status_code == 400   # needs a role
    s = client.post("/api/tokens", json={"name": "prometheus", "kind": "service", "roles": ["auditor"]}).json()
    assert s["secret"].startswith("lpst_")
    assert client.get("/api/tokens").json()["tokens"] == []                                  # mine: personal only
    assert client.get("/api/tokens?all=true").json()["tokens"][0]["name"] == "prometheus"
    _no_session(client)
    assert client.get("/api/dashboard/monitoring", headers=_bearer(s["secret"])).status_code == 200
    assert client.get("/api/admin/settings", headers=_bearer(s["secret"])).status_code == 403
    _admin_session(client)
    assert client.delete(f"/api/tokens/{s['id']}").json()["state"] == "revoked"
    _no_session(client)
    assert client.get("/api/dashboard/monitoring", headers=_bearer(s["secret"])).status_code == 401


def test_a_personal_token_never_exceeds_its_owners_current_roles(client):
    from labape import tokens
    with session_factory()() as db:
        u = User(provider="oidc", subject="dev", username="dev", display_name="Dev", groups=[])
        db.add(u)
        db.commit()
        t, secret = tokens.create(db, u, "mine", "personal", ["admin"], 10)
        assert tokens.effective_roles(t, {"viewer"}) == set()                    # granted admin, has only viewer
        t2, _ = tokens.create(db, u, "all", "personal", [], 10)
        assert tokens.effective_roles(t2, {"viewer", "deployer"}) == {"viewer", "deployer"}
