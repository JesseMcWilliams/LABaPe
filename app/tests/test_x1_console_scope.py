"""Batch X1 (design §59): the console role, a token's environment list and limits."""
import pytest
from fastapi import HTTPException

from labape import console_access, tokens
from labape.db import session_factory
from labape.models import Environment, Grant, KvmHost
from labape.security import Principal

from test_a1_tokens import _admin_session, _no_session


def _env(db, name):
    h = KvmHost(name="k-" + name, libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t")
    db.add(h)
    db.flush()
    e = Environment(name=name, kvm_host_id=h.id, created_by="alice", spec={"host_groups": []})
    db.add(e)
    db.commit()
    return e.id


def _scope(**kw):
    s = {"token_id": 1, "name": "t", "environments": None, "host_consoles": False, "require_account": True,
         "rate_per_minute": 30, "max_session_minutes": 30, "max_output_kb": 256, "owner_admin": False}
    s.update(kw)
    return s


def test_an_admin_can_make_a_console_token_for_chosen_environments(client):
    _admin_session(client)
    with session_factory()() as db:
        a, b = _env(db, "x1a"), _env(db, "x1b")
    assert client.post("/api/tokens", json={"name": "ai", "roles": ["console"], "environments": [a, 9999]}).status_code == 400
    r = client.post("/api/tokens", json={"name": "ai", "roles": ["console"], "environments": [b, a, a],
                                         "rate_per_minute": 5, "max_output_kb": 64})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["environments"] == sorted([a, b]) and j["rate_per_minute"] == 5 and j["max_output_kb"] == 64
    assert j["require_account"] is True and j["host_consoles"] is False and j["max_session_minutes"] == 30
    assert client.post("/api/tokens", json={"name": "x", "rate_per_minute": 0}).status_code == 422
    # an admin's console-only token has just the console role, and carries its scope
    _no_session(client)
    with session_factory()() as db:
        t = tokens.find(db, j["secret"])
        assert tokens.effective_roles(t, {"admin"}) == {"console"}
        sc = tokens.scope(t, True)
        assert sc["environments"] == sorted([a, b]) and sc["owner_admin"] is True


def test_the_host_console_flag_is_for_admins_only(client):
    _admin_session(client)
    ok = client.post("/api/tokens", json={"name": "h", "roles": ["admin"], "host_consoles": True})
    assert ok.status_code == 200 and ok.json()["host_consoles"] is True


def test_console_calls_need_a_token_with_the_role():
    with pytest.raises(HTTPException) as e:
        console_access.console_principal(Principal(1, "u", "U", [], {"admin"}))        # a session
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        console_access.console_principal(Principal(1, "u", "U", [], {"viewer"}, via_token=True, scope=_scope()))
    p = Principal(1, "u", "U", [], {"console"}, via_token=True, scope=_scope())
    assert console_access.console_principal(p) is p


def test_the_environment_list_and_ownership_decide_reach(client):
    with session_factory()() as db:
        a, b = _env(db, "x1c"), _env(db, "x1d")
        db.add(Grant(object_type="environment", object_id=a, principal_type="user", principal="bob", level="owner"))
        db.add(Grant(object_type="environment", object_id=b, principal_type="user", principal="bob", level="viewer"))
        db.commit()
        bob = Principal(2, "bob", "Bob", [], {"console"}, via_token=True, scope=_scope())
        assert console_access.environment_allowed(db, bob, a)                       # owns it, no list
        assert not console_access.environment_allowed(db, bob, b)                   # only a viewer
        listed = Principal(2, "bob", "Bob", [], {"console"}, via_token=True, scope=_scope(environments=[b]))
        assert not console_access.environment_allowed(db, listed, a)                # owns it but not on the list
        svc = Principal(3, "svc:ai", "ai", [], {"console"}, via_token=True, scope=_scope(environments=[b], owner_admin=True))
        assert console_access.environment_allowed(db, svc, b) and not console_access.environment_allowed(db, svc, a)
        with pytest.raises(HTTPException) as e:
            console_access.check_environment(db, listed, a)
        assert e.value.status_code == 404


def test_the_rate_limit_account_and_host_rules():
    p = Principal(1, "u", "U", [], {"console"}, via_token=True, scope=_scope(token_id=991, rate_per_minute=2))
    console_access.rate_limit(p, now=100.0)
    console_access.rate_limit(p, now=101.0)
    with pytest.raises(HTTPException) as e:
        console_access.rate_limit(p, now=102.0)
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "58"
    console_access.rate_limit(p, now=161.0)                                          # the first call aged out
    with pytest.raises(HTTPException):
        console_access.check_account(p, "")
    assert console_access.check_account(p, "deploy") == "deploy"
    loose = Principal(1, "u", "U", [], {"console"}, via_token=True, scope=_scope(require_account=False))
    assert console_access.check_account(loose, "") == ""
    with pytest.raises(HTTPException):
        console_access.check_host_consoles(p)
    adm = Principal(1, "u", "U", [], {"admin"}, via_token=True, scope=_scope(host_consoles=True))
    console_access.check_host_consoles(adm)
    with pytest.raises(HTTPException):                                               # allowed, but no longer admin
        console_access.check_host_consoles(Principal(1, "u", "U", [], {"console"}, via_token=True, scope=_scope(host_consoles=True)))
    assert console_access.limits(p) == {"session_seconds": 1800, "output_bytes": 262144}
