"""External secret stores (phase 10d, P3): rotating the stores' own logins, externally rotated secrets."""
import pytest

from labape import appsettings, stores
from labape.db import session_factory
from labape.engine import rotation, rotation_stores
from labape.models import SecretPolicy
from labape.security import get_secret, put_secret
from labape.stores.conjur import ConjurSettings
from labape.stores.openbao import OpenBaoSettings

from test_api import _admin_session
from test_stores import FAKE, bao, conjur  # noqa: F401 — the fixtures


def _setup(db):
    appsettings.put(db, "store_openbao", OpenBaoSettings(enabled=True, url="https://bao.example.test:8200",
                                                         role_id="role-1"))
    put_secret(db, "stores/openbao/secret_id", FAKE)
    appsettings.put(db, "store_conjur", ConjurSettings(enabled=True, url="https://conjur.example.test:8443"))
    put_secret(db, "stores/conjur/api_key", FAKE)
    stores.forget("openbao")
    stores.forget("conjur")
    rotation.ensure_policies(db)


def _policy(db, kind):
    return db.query(SecretPolicy).filter(SecretPolicy.kind == kind).one()


def test_policies_only_once_a_store_is_set_up(client, bao, conjur):  # noqa: F811
    with session_factory()() as db:
        rotation.ensure_policies(db)
        assert not db.query(SecretPolicy).filter(SecretPolicy.kind.in_(("openbao_secret_id", "conjur_api_key"))).all()
        _setup(db)
        p = _policy(db, "openbao_secret_id")
        assert p.interval_hours == 24 and p.status == "pending"
        p.next_due_at = None
        db.commit()
        assert p in rotation.due(db)
        cfg = appsettings.get(db, "store_openbao")
        appsettings.put(db, "store_openbao", cfg.model_copy(update={"enabled": False}))
        assert p not in rotation.due(db)                      # store turned off: not scheduled
        rotation.link_secrets(db)
        from labape.models import Secret
        assert db.get(Secret, "stores/openbao/secret_id").policy_id == p.id


def test_openbao_secret_id_rotation(client, bao, conjur):  # noqa: F811
    with session_factory()() as db:
        _setup(db)
        summary = rotation_stores.rotate_openbao_secret_id(db, _policy(db, "openbao_secret_id"), None)
        new = get_secret(db, "stores/openbao/secret_id")
        assert summary == "new secret ID in use; the old one is destroyed"
        assert new != FAKE and bao.secret_ids == {new}
        stores.open_store(db, "openbao").put("after/rotation", "ok")      # the store works with the new one
        assert bao.kv["after/rotation"] == "ok"


def test_openbao_rotation_refused_keeps_old(client, bao, conjur):  # noqa: F811
    bao.may_rotate = False
    with session_factory()() as db:
        _setup(db)
        with pytest.raises(rotation.RotationError, match="doesn't let LABaPe change role labape's secret IDs"):
            rotation_stores.rotate_openbao_secret_id(db, _policy(db, "openbao_secret_id"), None)
        assert get_secret(db, "stores/openbao/secret_id") == FAKE and bao.secret_ids == {FAKE}


def test_conjur_api_key_rotation(client, bao, conjur):  # noqa: F811
    with session_factory()() as db:
        _setup(db)
        assert rotation_stores.rotate_conjur_api_key(db, _policy(db, "conjur_api_key"), None) == "new API key in use"
        new = get_secret(db, "stores/conjur/api_key")
        assert new == conjur.api_key and new != FAKE
        stores.open_store(db, "conjur").put("after/rotation", "ok")
        assert conjur.values["labape/secrets/after/rotation"] == "ok"


def test_managed_externally_needs_an_external_store(client, bao):  # noqa: F811
    _admin_session(client)
    with session_factory()() as db:
        _setup(db)
        put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, "dir-token")
        pid = _policy(db, "directory_token").id
    pol = client.get(f"/api/admin/secrets/{pid}").json()
    body = {k: pol[k] for k in ("interval_hours", "enabled", "elevated_ref", "params", "notes")}
    r = client.put(f"/api/admin/secrets/{pid}", json={**body, "store": "builtin", "managed_externally": True})
    assert r.status_code == 422 and "external store" in r.text
    r = client.put(f"/api/admin/secrets/{pid}", json={**body, "store": "openbao", "managed_externally": True})
    assert r.status_code == 200 and r.json()["managed_externally"]
    with session_factory()() as db:
        assert _policy(db, "directory_token") not in rotation.due(db)
        bao.kv[appsettings.DIRECTORY_TOKEN_SECRET] = "rotated-by-the-store"      # read fresh every time
        assert get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) == "rotated-by-the-store"
