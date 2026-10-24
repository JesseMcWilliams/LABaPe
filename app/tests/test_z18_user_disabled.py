"""Question 196: a personal API token stops working when its owner is deactivated or removed in the provider."""
import pytest

from labape import tokens, userstatus
from labape.db import session_factory
from labape.models import ApiToken, AuditLog, User
from labape.security import Principal, current_principal


def _owner_with_token(client, kind="personal"):
    with session_factory()() as db:
        u = User(provider="oidc", subject="bob", username="bob", display_name="Bob", groups=["labape-admins"])
        db.add(u)
        db.commit()
        _, secret = tokens.create(db, u, "ci", kind, ["admin"], 30)
        return u.id, secret


def _get(client, secret):
    client.app.dependency_overrides.pop(current_principal, None)
    client.cookies.clear()
    return client.get("/api/hosts", headers={"Authorization": f"Bearer {secret}"}).status_code


@pytest.fixture()
def provider(monkeypatch):
    state = {"value": "active"}
    monkeypatch.setattr(userstatus, "lookup", lambda db, user: state["value"])
    return state


def test_sweep_disables_a_deactivated_owner_and_revokes_tokens(client, provider):
    uid, secret = _owner_with_token(client)
    with session_factory()() as db:
        db.query(User).filter_by(id=uid).update({"groups": ["labape-admins"]})
        db.commit()
    assert _get(client, secret) in (200, 403)          # works (or is merely refused by role), not 401
    provider["value"] = "inactive"
    with session_factory()() as db:
        done = userstatus.sweep(db)
        assert done and "inactive" in done[0]
        u = db.get(User, uid)
        assert u.disabled_at is not None and u.disabled_reason == "provider"
        assert db.query(ApiToken).filter(ApiToken.user_id == uid, ApiToken.revoked_at.is_(None)).count() == 0
        assert db.query(AuditLog).filter_by(action="user.disable").count() == 1
    assert _get(client, secret) == 401


def test_a_removed_owner_is_disabled_too(client, provider):
    uid, _ = _owner_with_token(client)
    provider["value"] = "gone"
    with session_factory()() as db:
        assert userstatus.sweep(db)
        assert db.get(User, uid).disabled_at is not None


def test_unknown_changes_nothing(client, provider):
    uid, secret = _owner_with_token(client)
    provider["value"] = None                          # Authentik can't be asked
    with session_factory()() as db:
        assert userstatus.sweep(db) == []
        assert db.get(User, uid).disabled_at is None


def test_disabled_owner_token_refused_even_if_not_revoked(client):
    uid, secret = _owner_with_token(client)
    with session_factory()() as db:
        u = db.get(User, uid)
        u.disabled_at, u.disabled_reason = userstatus.utcnow(), "admin"
        db.commit()
    assert _get(client, secret) == 401


def test_service_token_survives_its_makers_disabling(client):
    uid, secret = _owner_with_token(client, kind="service")
    with session_factory()() as db:
        u = db.get(User, uid)
        u.disabled_at, u.disabled_reason = userstatus.utcnow(), "provider"
        db.commit()
    assert _get(client, secret) != 401


def test_sweep_clears_only_its_own_mark_when_active_again(client, provider):
    uid, _ = _owner_with_token(client)
    provider["value"] = "inactive"
    with session_factory()() as db:
        userstatus.sweep(db)
    provider["value"] = "active"
    with session_factory()() as db:
        assert "active again" in userstatus.sweep(db)[0]
        assert db.get(User, uid).disabled_at is None
        u = db.get(User, uid)                          # an admin's mark stays
        u.disabled_at, u.disabled_reason = userstatus.utcnow(), "admin"
        db.commit()
        assert userstatus.sweep(db) == []
        assert db.get(User, uid).disabled_at is not None


def test_sign_in_clears_the_sweeps_mark_not_an_admins(client):
    uid, _ = _owner_with_token(client)
    with session_factory()() as db:
        u = db.get(User, uid)
        userstatus.disable(db, u, "provider")
        userstatus.enable_after_sign_in(db, u)
        assert u.disabled_at is None
        userstatus.disable(db, u, "admin")
        userstatus.enable_after_sign_in(db, u)
        assert u.disabled_at is not None
