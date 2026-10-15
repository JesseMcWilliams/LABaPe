"""V1a (design §49): an environment's secrets in a folder of their own in OpenBao, and on its page."""
from sqlalchemy import select

from labape import stores
from labape.db import session_factory
from labape.models import AuditLog, Environment, Grant, Secret
from labape.security import Principal, current_principal, get_secret, put_secret
from labape.stores import layout

from test_api import _admin_session, _host

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"], "count": 2}]}
VALUE = "ThisIsMy_FAKE_Password6!"


class MemStore:
    """An external store in memory, by store path."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, name):
        return self.data.get(name)

    def put(self, name, value):
        self.data[name] = value

    def delete(self, name):
        self.data.pop(name, None)


def _env(db, name="lab5"):
    e = Environment(name=name, kvm_host_id=1, created_by="admin", status="deployed", spec=dict(SPEC))
    db.add(e)
    db.commit()
    return e


def test_the_layout():
    assert layout.env_path("lab2", "windows_admin") == "env/lab2/accounts/windows_admin"
    assert layout.env_path("lab2", "vm/app1/windows_admin") == "env/lab2/accounts/vm/app1/windows_admin"
    assert layout.env_path("lab2", "account/jdoe") == "env/lab2/accounts/account/jdoe"
    assert layout.env_path("lab2", "user/db-pass") == "env/lab2/user/db-pass"
    assert layout.env_path("lab2", "uservm/app1/api-key") == "env/lab2/vm/app1/api-key"
    assert layout.user_name(3, "db-pass") == "environment/3/user/db-pass"
    assert layout.user_name(3, "api-key", "app1") == "environment/3/uservm/app1/api-key"


def test_openbao_gets_the_layout_and_old_names_move(db_reset, monkeypatch):
    mem = MemStore()
    monkeypatch.setattr(stores, "open_store", lambda db, name: mem)
    with session_factory()() as db:
        e = _env(db)
        assert layout.store_name(db, "openbao", f"environment/{e.id}/windows_admin") == "env/lab5/accounts/windows_admin"
        assert layout.store_name(db, "conjur", f"environment/{e.id}/windows_admin") == f"environment/{e.id}/windows_admin"
        assert layout.store_name(db, "openbao", "platform/engine/ssh_key") == "platform/engine/ssh_key"
        # A secret written before the layout, under LABaPe's name: read moves it.
        old = f"environment/{e.id}/domain_admin"
        mem.data[old] = VALUE
        db.add(Secret(name=old, value="", store="openbao"))
        db.commit()
        assert get_secret(db, old) == VALUE
        assert mem.data == {"env/lab5/accounts/domain_admin": VALUE}
        # A write goes to the new place and leaves no old copy.
        mem.data[f"environment/{e.id}/windows_admin"] = "old"
        db.add(Secret(name=f"environment/{e.id}/windows_admin", value="", store="openbao"))
        db.commit()
        put_secret(db, f"environment/{e.id}/windows_admin", VALUE)
        assert f"environment/{e.id}/windows_admin" not in mem.data
        assert mem.data["env/lab5/accounts/windows_admin"] == VALUE
        # Moving between stores keeps the layout on the OpenBao side.
        stores.ext_delete(db, "openbao", old)
        assert "env/lab5/accounts/domain_admin" not in mem.data


def _as(client, username, *roles):
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username=username, display_name="", roles=set(roles))


def test_the_environment_page_secrets(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = _env(db)
        env_id = e.id
        put_secret(db, f"environment/{env_id}/windows_admin", VALUE)
        put_secret(db, f"environment/{env_id}/vm/app1/windows_admin", VALUE)
        put_secret(db, f"environment/{env_id}/vm/app2/windows_admin.pending", VALUE)     # an attempt: not listed
        db.add_all([Grant(object_type="environment", object_id=env_id, principal_type="user", principal="olive",
                          level="owner"),
                    Grant(object_type="environment", object_id=env_id, principal_type="user", principal="uma",
                          level="user")])
        db.commit()
    try:
        # A user of the environment, not an owner: nothing.
        _as(client, "uma", "deployer")
        assert client.get(f"/api/environments/{env_id}/secrets").status_code == 403
        assert client.post(f"/api/environments/{env_id}/secrets/reveal", json={"name": "windows_admin"}).status_code == 403
        assert client.put(f"/api/environments/{env_id}/secrets", json={"name": "x", "value": "y"}).status_code == 403
        _as(client, "nobody", "deployer")
        assert client.get(f"/api/environments/{env_id}/secrets").status_code == 404

        _as(client, "olive", "deployer")
        r = client.put(f"/api/environments/{env_id}/secrets", json={"name": "db-pass", "value": VALUE})
        assert r.status_code == 200 and r.json() == {"name": "user/db-pass", "replaced": False}
        r = client.put(f"/api/environments/{env_id}/secrets", json={"name": "api-key", "vm": "app1", "value": VALUE})
        assert r.json() == {"name": "uservm/app1/api-key", "replaced": False}
        assert client.put(f"/api/environments/{env_id}/secrets",
                          json={"name": "db-pass", "value": "ThisIsMy_FAKE_Password7!"}).json()["replaced"] is True
        assert client.put(f"/api/environments/{env_id}/secrets",
                          json={"name": "x", "vm": "web9", "value": "y"}).status_code == 422
        assert client.put(f"/api/environments/{env_id}/secrets",
                          json={"name": "../escape", "value": "y"}).status_code == 422

        listed = client.get(f"/api/environments/{env_id}/secrets").json()
        rows = {s["name"]: s for s in listed["secrets"]}
        assert set(rows) == {"windows_admin", "vm/app1/windows_admin", "user/db-pass", "uservm/app1/api-key"}
        assert rows["vm/app1/windows_admin"]["area"] == "accounts" and rows["vm/app1/windows_admin"]["vm"] == "app1"
        assert rows["uservm/app1/api-key"] == {**rows["uservm/app1/api-key"], "area": "vm", "vm": "app1",
                                               "short": "api-key", "editable": True}
        assert rows["windows_admin"]["editable"] is False and listed["vms"] == ["dc1", "app1", "app2"]
        assert all("value" not in s for s in listed["secrets"])

        r = client.post(f"/api/environments/{env_id}/secrets/reveal", json={"name": "user/db-pass"})
        assert r.json()["value"] == "ThisIsMy_FAKE_Password7!"
        assert client.post(f"/api/environments/{env_id}/secrets/reveal", json={"name": "nope"}).status_code == 404
        assert client.delete(f"/api/environments/{env_id}/secrets", params={"name": "windows_admin"}).status_code == 409
        assert client.delete(f"/api/environments/{env_id}/secrets", params={"name": "user/db-pass"}).status_code == 200
        with session_factory()() as db:
            assert db.get(Secret, f"environment/{env_id}/user/db-pass") is None
            log = list(db.scalars(select(AuditLog).where(AuditLog.action.like("environment.secret.%"))
                                  .order_by(AuditLog.id)))
            assert [a.action for a in log] == ["environment.secret.store", "environment.secret.store",
                                               "environment.secret.replace", "environment.secret.show",
                                               "environment.secret.delete"]
            assert all(VALUE not in str(a.detail) and "Password7" not in str(a.detail) for a in log)
    finally:
        client.app.dependency_overrides.clear()


def test_a_destroy_clears_what_owners_stored(db_reset):
    from labape.engine.vmaccess import clear_env_passwords
    with session_factory()() as db:
        put_secret(db, "environment/7/user/db-pass", VALUE)
        put_secret(db, "environment/7/uservm/app1/api-key", VALUE)
        clear_env_passwords(db, 7)
        assert get_secret(db, "environment/7/user/db-pass") == ""
        assert get_secret(db, "environment/7/uservm/app1/api-key") == ""


def test_a_new_secret_joins_its_environments_store(db_reset, monkeypatch):
    from labape.security import default_store
    mem = MemStore()
    monkeypatch.setattr(stores, "open_store", lambda db, name: mem)
    with session_factory()() as db:
        e = _env(db)
        assert default_store(db, f"environment/{e.id}/user/db-pass") == "builtin"        # none yet: the default
        db.add_all([Secret(name=f"environment/{e.id}/windows_admin", value="", store="openbao"),
                    Secret(name=f"environment/{e.id}/domain_admin", value="", store="openbao")])
        db.commit()
        assert default_store(db, f"environment/{e.id}/user/db-pass") == "openbao"
        put_secret(db, f"environment/{e.id}/user/db-pass", VALUE)
        assert mem.data == {"env/lab5/user/db-pass": VALUE}
        assert default_store(db, "environment/99/windows_admin") == "builtin"           # another environment's
        assert default_store(db, "settings/smtp_password") == "builtin"
