"""External secret stores (phase 10d, P2): the catalog, routing by store, moves, policy links."""
import json

import httpx
import pytest

from labape import appsettings, stores
from labape.db import session_factory
from labape.engine import rotation, vmaccess
from labape.models import Secret, SecretPolicy
from labape.security import get_secret, put_secret
from labape.stores.base import StoreError
from labape.stores.openbao import OpenBaoSettings, OpenBaoStore

from test_api import _admin_session
from test_stores import FAKE, bao  # noqa: F401 — the fixture


def _use_bao(db):
    appsettings.put(db, "store_openbao", OpenBaoSettings(enabled=True, url="https://bao.example.test:8200",
                                                         role_id="role-1"))
    put_secret(db, "stores/openbao/secret_id", FAKE)
    stores.forget("openbao")


def test_move_there_and_back(client, bao):  # noqa: F811
    with session_factory()() as db:
        _use_bao(db)
        put_secret(db, "settings/smtp_password", FAKE)
        assert "moved from Built-in" in stores.move(db, "settings/smtp_password", "openbao", "t")
        row = db.get(Secret, "settings/smtp_password")
        assert (row.store, row.value) == ("openbao", "")
        assert bao.kv["settings/smtp_password"] == FAKE
        assert get_secret(db, "settings/smtp_password") == FAKE              # read through the row
        put_secret(db, "settings/smtp_password", "changed")                  # and written there
        assert bao.kv["settings/smtp_password"] == "changed" and row.value == ""
        stores.move(db, "settings/smtp_password", "builtin", "t")
        assert db.get(Secret, "settings/smtp_password").store == "builtin"
        assert "settings/smtp_password" not in bao.kv                        # old copy deleted
        assert get_secret(db, "settings/smtp_password") == "changed"


def test_move_failures_leave_it_in_place(client, bao):  # noqa: F811
    with session_factory()() as db:
        _use_bao(db)
        put_secret(db, "settings/smtp_password", FAKE)
        bao.denied.add("settings/smtp_password")
        with pytest.raises(StoreError, match="denied access to settings/smtp_password"):
            stores.move(db, "settings/smtp_password", "openbao", "t")
        assert db.get(Secret, "settings/smtp_password").store == "builtin"
        assert get_secret(db, "settings/smtp_password") == FAKE
        put_secret(db, "elevated/ak-admin", FAKE)
        with pytest.raises(StoreError, match="stays in the built-in store"):
            stores.move(db, "elevated/ak-admin", "openbao", "t")
        with pytest.raises(StoreError, match="isn't available"):
            stores.move(db, "settings/smtp_password", "conjur", "t")


def test_no_silent_fallback(client, bao, monkeypatch):  # noqa: F811
    with session_factory()() as db:
        _use_bao(db)
        put_secret(db, "settings/smtp_password", FAKE)
        stores.move(db, "settings/smtp_password", "openbao", "t")

        def down(req):
            raise httpx.ConnectError("Connection refused", request=req)
        monkeypatch.setattr(OpenBaoStore, "transport", httpx.MockTransport(down))
        stores.forget("openbao")
        with pytest.raises(StoreError, match="Couldn't reach"):
            get_secret(db, "settings/smtp_password")
        with pytest.raises(StoreError):
            put_secret(db, "settings/smtp_password", "new")


def test_default_store_for_new_environment_secrets(client, bao):  # noqa: F811
    _admin_session(client)
    r = client.put("/api/admin/settings/secret_stores", json={"default_environment_store": "openbao"})
    assert r.status_code == 422 and "isn't available" in r.text
    with session_factory()() as db:
        _use_bao(db)
    r = client.put("/api/admin/settings/secret_stores", json={"default_environment_store": "openbao"})
    assert r.status_code == 200
    with session_factory()() as db:
        put_secret(db, "environment/9/inventory", "all: {}")
        put_secret(db, "settings/other", "x")                 # not an environment secret
        put_secret(db, "job/5/cert_token", "y")               # pinned
        assert db.get(Secret, "environment/9/inventory").store == "openbao"
        assert "environment/9/inventory" in bao.kv
        assert db.get(Secret, "settings/other").store == "builtin"
        assert db.get(Secret, "job/5/cert_token").store == "builtin"


def test_data_key_rotation_skips_external_rows(client, bao):  # noqa: F811
    with session_factory()() as db:
        _use_bao(db)
        put_secret(db, "settings/smtp_password", FAKE)
        stores.move(db, "settings/smtp_password", "openbao", "t")
        rotation.ensure_policies(db)
        p = db.query(SecretPolicy).filter(SecretPolicy.kind == "builtin_data_key").one()
        rotation.rotate_data_key(db, p, None)
        assert get_secret(db, "settings/smtp_password") == FAKE
        assert get_secret(db, "stores/openbao/secret_id") == FAKE


def test_policy_link_and_store_change(client, bao):  # noqa: F811
    _admin_session(client)
    with session_factory()() as db:
        _use_bao(db)
        rotation.ensure_policies(db)
        pid = db.query(SecretPolicy).filter(SecretPolicy.kind == "directory_token").one().id
    pol = client.get(f"/api/admin/secrets/{pid}").json()
    assert pol["secret_name"] == appsettings.DIRECTORY_TOKEN_SECRET and "openbao" in pol["stores"]
    body = {k: pol[k] for k in ("interval_hours", "enabled", "managed_externally", "elevated_ref", "params", "notes")}
    r = client.put(f"/api/admin/secrets/{pid}", json={**body, "store": "openbao"})
    assert r.status_code == 422 and "Nothing is stored" in r.text            # no token saved yet
    with session_factory()() as db:
        put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, "dir-token")
    stored = client.get("/api/admin/stored-secrets").json()
    row = next(s for s in stored["secrets"] if s["name"] == appsettings.DIRECTORY_TOKEN_SECRET)
    assert row["rotation"]["policy_id"] == pid and row["store"] == "builtin"
    assert [s["name"] for s in stored["stores"]] == ["builtin", "openbao"]
    r = client.put(f"/api/admin/secrets/{pid}", json={**body, "store": "openbao", "interval_hours": 48})
    assert r.status_code == 200 and r.json()["store"] == "openbao" and r.json()["interval_hours"] == 48
    with session_factory()() as db:
        assert db.get(Secret, appsettings.DIRECTORY_TOKEN_SECRET).store == "openbao"
    assert bao.kv[appsettings.DIRECTORY_TOKEN_SECRET] == "dir-token"


def test_move_endpoint(client, bao):  # noqa: F811
    _admin_session(client)
    with session_factory()() as db:
        _use_bao(db)
        put_secret(db, "settings/a", "1")
        put_secret(db, "elevated/b", "2")
    r = client.post("/api/admin/stored-secrets/move",
                    json={"names": ["settings/a", "elevated/b", "nope"], "store": "openbao"})
    res = {x["name"]: x for x in r.json()["results"]}
    assert r.json()["moved"] == 1 and res["settings/a"]["ok"]
    assert not res["elevated/b"]["ok"] and not res["nope"]["ok"]
    rows = {s["name"]: s for s in client.get("/api/admin/stored-secrets").json()["secrets"]}
    assert rows["settings/a"]["store"] == "openbao" and rows["elevated/b"]["pinned"]
    assert "value" not in rows["settings/a"]


def test_legacy_passwords_split(client):
    with session_factory()() as db:
        put_secret(db, "environment/4/passwords", json.dumps({"windows_bootstrap_admin_password": "w",
                                                              "domain_admin_password": "d"}))
        assert vmaccess.split_legacy_passwords(db) == 1
        assert get_secret(db, "environment/4/windows_admin") == "w"
        assert get_secret(db, "environment/4/domain_admin") == "d"
        assert db.get(Secret, "environment/4/passwords") is None
        assert vmaccess.env_password_overrides(db, 4) == {"windows_bootstrap_admin_password": "w",
                                                          "domain_admin_password": "d"}
        assert vmaccess.split_legacy_passwords(db) == 0
