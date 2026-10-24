"""Questions 201 and 202: the Users page and sharing / handing over an environment."""
from labape import tokens
from labape.db import session_factory
from labape.models import ApiToken, AuditLog, Environment, Grant, KvmHost, User
from labape.security import Principal, current_principal, subject_key


def _as(client, user_id, name, roles):
    client.app.dependency_overrides[current_principal] = lambda: Principal(user_id, name, name, [], set(roles))


def _setup():
    with session_factory()() as db:
        host = KvmHost(name="kh", libvirt_uri="qemu:///system")
        db.add(host)
        db.commit()
        users = {n: User(provider="oidc", subject=n, username=n, display_name=n.title(), groups=[])
                 for n in ("alice", "bob", "carol")}
        db.add_all(users.values())
        db.commit()
        env = Environment(name="shared1", kvm_host_id=host.id, spec={}, created_by="alice")
        db.add(env)
        db.commit()
        db.add(Grant(object_type="environment", object_id=env.id, principal_type="user", principal="alice", level="owner",
                     subject=subject_key(users["alice"])))
        db.commit()
        return env.id, {n: u.id for n, u in users.items()}


def test_an_owner_shares_with_a_user_and_a_group_and_the_user_sees_it(client):
    env_id, ids = _setup()
    _as(client, ids["alice"], "alice", {"deployer"})
    assert client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "user", "principal": "bob"}).status_code == 201
    assert client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "group", "principal": "qa"}).status_code == 201
    assert client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "user", "principal": "bob"}).status_code == 409
    assert client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "user", "principal": "nobody"}).status_code == 422
    grants = client.get(f"/api/environments/{env_id}/sharing").json()["grants"]
    assert [(g["principal"], g["level"]) for g in grants] == [("alice", "owner"), ("qa", "user"), ("bob", "user")]
    _as(client, ids["bob"], "bob", {"viewer"})
    assert [e["id"] for e in client.get("/api/environments").json()] == [env_id]       # a viewer sees it now
    assert client.get(f"/api/environments/{env_id}/sharing").status_code == 403         # but can't change who has access
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "bob"}).status_code == 403


def test_unshare_and_owners_are_protected(client):
    env_id, ids = _setup()
    _as(client, ids["alice"], "alice", {"deployer"})
    gid = client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "user", "principal": "bob"}).json()["id"]
    owner = next(g["id"] for g in client.get(f"/api/environments/{env_id}/sharing").json()["grants"] if g["level"] == "owner")
    assert client.delete(f"/api/environments/{env_id}/sharing/{owner}").status_code == 409
    assert client.delete(f"/api/environments/{env_id}/sharing/{gid}").status_code == 200
    _as(client, ids["bob"], "bob", {"viewer"})
    assert client.get("/api/environments").json() == []


def test_hand_over_keeps_the_old_owner_as_a_user_by_default(client):
    env_id, ids = _setup()
    _as(client, ids["alice"], "alice", {"deployer"})
    client.post(f"/api/environments/{env_id}/sharing", json={"principal_type": "user", "principal": "bob"})
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "bob"}).status_code == 200
    with session_factory()() as db:
        levels = {g.principal: g.level for g in db.query(Grant).filter_by(object_id=env_id)}
        assert levels == {"alice": "user", "bob": "owner"}
        assert db.query(AuditLog).filter_by(action="environment.transfer").count() == 1
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "carol", "keep_access": False}).status_code == 403
    _as(client, ids["bob"], "bob", {"deployer"})
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "carol", "keep_access": False}).status_code == 200
    with session_factory()() as db:
        assert {g.principal: g.level for g in db.query(Grant).filter_by(object_id=env_id)} == {"alice": "user", "carol": "owner"}


def test_an_admin_can_rescue_an_environment_whose_owner_left(client):
    env_id, ids = _setup()
    _as(client, ids["carol"], "carol", {"admin"})
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "carol", "keep_access": False}).status_code == 200
    with session_factory()() as db:
        assert {g.principal: g.level for g in db.query(Grant).filter_by(object_id=env_id)} == {"carol": "owner"}
        db.get(User, ids["bob"]).disabled_at = tokens.utcnow()
        db.commit()
    assert client.post(f"/api/environments/{env_id}/transfer", json={"username": "bob"}).status_code == 422     # disabled


def test_users_page_lists_and_disables(client):
    _, ids = _setup()
    _as(client, ids["alice"], "alice", {"admin"})
    with session_factory()() as db:
        tokens.create(db, db.get(User, ids["bob"]), "ci", "personal", ["viewer"], 30)
    r = client.get("/api/admin/users", params={"q": "bo"}).json()
    assert r["total"] == 1 and r["users"][0]["username"] == "bob" and r["users"][0]["active_tokens"] == 1
    assert client.post(f"/api/admin/users/{ids['alice']}/disable").status_code == 409          # not yourself
    out = client.post(f"/api/admin/users/{ids['bob']}/disable").json()
    assert out["tokens_revoked"] == 1
    with session_factory()() as db:
        bob = db.get(User, ids["bob"])
        assert bob.disabled_at is not None and bob.disabled_reason == "admin"
        assert db.query(ApiToken).filter(ApiToken.revoked_at.is_(None)).count() == 0
    assert client.get("/api/admin/users", params={"disabled": "true"}).json()["total"] == 1
    assert client.post(f"/api/admin/users/{ids['bob']}/enable").status_code == 200
    with session_factory()() as db:
        assert db.get(User, ids["bob"]).disabled_at is None


def test_users_page_is_for_admins_and_auditors(client):
    _, ids = _setup()
    _as(client, ids["bob"], "bob", {"deployer"})
    assert client.get("/api/admin/users").status_code == 403
    _as(client, ids["bob"], "bob", {"auditor"})
    assert client.get("/api/admin/users").status_code == 403
    assert client.post(f"/api/admin/users/{ids['carol']}/disable").status_code == 403
