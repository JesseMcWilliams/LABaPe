"""External secret stores (phase 10d, P1): OpenBao and Conjur providers against faked HTTP APIs."""
import json
from urllib.parse import unquote

import httpx
import pytest

from labape import appsettings, stores
from labape.db import session_factory
from labape.security import get_secret, put_secret
from labape.stores.base import StoreError, decode_name, encode_name
from labape.stores.conjur import ConjurSettings, ConjurStore
from labape.stores.openbao import OpenBaoSettings, OpenBaoStore

from test_api import _admin_session

FAKE = "ThisIsMy_FAKE_Password6!"


class FakeBao:
    """Enough of OpenBao's API: AppRole login, KV v2 data/metadata, lookup-self."""

    def __init__(self, secret_id=FAKE, denied=()):
        self.secret_ids, self.denied = {secret_id}, set(denied)
        self.may_rotate = True
        self.kv: dict[str, str] = {}
        self.tokens: set[str] = set()
        self.logins = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = unquote(req.url.raw_path.decode().split("?")[0])
        if path == "/v1/auth/approle/login":
            body = json.loads(req.content)
            if body.get("role_id") != "role-1" or body.get("secret_id") not in self.secret_ids:
                return httpx.Response(400, json={"errors": ["invalid role or secret ID"]})
            self.logins += 1
            tok = f"s.tok{self.logins}"
            self.tokens.add(tok)
            return httpx.Response(200, json={"auth": {"client_token": tok, "lease_duration": 3600}})
        if req.headers.get("X-Vault-Token") not in self.tokens:
            return httpx.Response(403, json={"errors": ["permission denied"]})
        if path.startswith("/v1/auth/approle/role/labape/secret-id"):
            if not self.may_rotate:
                return httpx.Response(403, json={"errors": ["permission denied"]})
            if path.endswith("/destroy"):
                self.secret_ids.discard(json.loads(req.content)["secret_id"])
                return httpx.Response(204)
            sid = f"new-secret-id-{len(self.secret_ids)}"
            self.secret_ids.add(sid)
            return httpx.Response(200, json={"data": {"secret_id": sid}})
        if path == "/v1/auth/token/lookup-self":
            return httpx.Response(200, json={"data": {"policies": ["default", "labape"]}})
        kind, _, key = path.removeprefix("/v1/labape/").partition("/")
        if key.rstrip("/") in self.denied:
            return httpx.Response(403, json={"errors": ["permission denied"]})
        if kind == "data" and req.method == "GET":
            return (httpx.Response(200, json={"data": {"data": {"value": self.kv[key]}}}) if key in self.kv
                    else httpx.Response(404, json={"errors": []}))
        if kind == "data" and req.method == "POST":
            self.kv[key] = json.loads(req.content)["data"]["value"]
            return httpx.Response(200, json={"data": {"version": 1}})
        if kind == "metadata" and req.method == "DELETE":
            self.kv.pop(key, None)
            return httpx.Response(204)
        if kind == "metadata" and req.method == "LIST":
            folder = key.rstrip("/")
            pre = f"{folder}/" if folder else ""
            keys = sorted({k[len(pre):].split("/")[0] + ("/" if "/" in k[len(pre):] else "")
                           for k in self.kv if k.startswith(pre)})
            return httpx.Response(200, json={"data": {"keys": keys}}) if keys else httpx.Response(404, json={})
        return httpx.Response(405)


class FakeConjur:
    """Enough of Conjur's API: host authn, variables, policy load/patch, resources, whoami."""

    def __init__(self, api_key=FAKE):
        self.api_key = api_key
        self.declared: set[str] = set()
        self.values: dict[str, str] = {}
        self.tokens: set[str] = set()
        self.logins = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        parts = [unquote(p) for p in req.url.raw_path.decode().split("?")[0].split("/")[1:]]
        if parts[0] == "authn" and parts[-1] == "authenticate":
            if parts[2] != "host/labape/app" or req.content.decode() != self.api_key:
                return httpx.Response(401)
            self.logins += 1
            tok = f"dG9r{self.logins}"
            self.tokens.add(tok)
            return httpx.Response(200, text=tok)
        if parts[:2] == ["authn", "labape"] and parts[2:] == ["api_key"] and req.method == "PUT":
            tok = req.headers.get("Authorization", "").removeprefix('Token token="').removesuffix('"')
            if tok not in self.tokens or req.url.params.get("role") != "host:labape/app":
                return httpx.Response(401)
            self.api_key = f"rotated-key-{self.logins}"
            return httpx.Response(200, text=self.api_key)
        auth = req.headers.get("Authorization", "")
        if auth.removeprefix('Token token="').removesuffix('"') not in self.tokens:
            return httpx.Response(401)
        if parts == ["whoami"]:
            return httpx.Response(200, json={"username": "host/labape/app"})
        if parts[:3] == ["secrets", "labape", "variable"]:
            vid = parts[3]
            if vid not in self.declared:
                return httpx.Response(404)
            if req.method == "POST":
                self.values[vid] = req.content.decode()
                return httpx.Response(201)
            return httpx.Response(200, text=self.values[vid]) if vid in self.values else httpx.Response(404)
        if parts[:3] == ["policies", "labape", "policy"] and parts[3] == "labape/secrets":
            text = req.content.decode()
            vid = "labape/secrets/" + text.split('"')[1]
            if req.method == "POST":
                self.declared.add(vid)
            elif req.method == "PATCH":
                self.declared.discard(vid)
                self.values.pop(vid, None)
            return httpx.Response(201, json={})
        if parts[:2] == ["resources", "labape"]:
            if len(parts) == 3:
                return httpx.Response(200, json=[{"id": f"labape:variable:{v}"} for v in sorted(self.declared)])
            if parts[2] == "policy":
                return httpx.Response(200, json={"id": f"labape:policy:{parts[3]}"})
            return httpx.Response(200, json={}) if parts[3] in self.declared else httpx.Response(404)
        return httpx.Response(405)


@pytest.fixture()
def bao(monkeypatch):
    fake = FakeBao()
    monkeypatch.setattr(OpenBaoStore, "transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture()
def conjur(monkeypatch):
    fake = FakeConjur()
    monkeypatch.setattr(ConjurStore, "transport", httpx.MockTransport(fake))
    return fake


def _bao_store(secret_id=FAKE):
    return OpenBaoStore(OpenBaoSettings(enabled=True, url="https://bao.example.test:8200", role_id="role-1"), secret_id)


def _conjur_store(api_key=FAKE):
    return ConjurStore(ConjurSettings(enabled=True, url="https://conjur.example.test:8443"), api_key)


def test_name_mapping():
    assert encode_name("environment/3/passwords") == "environment/3/passwords"
    assert encode_name("elevated/ak admin+1") == "elevated/ak%20admin%2B1"
    assert decode_name(encode_name("a b/ü%")) == "a b/ü%"
    for bad in ("", "/abs", "a//b", "a/../b", "a/.", "trailing/"):
        with pytest.raises(StoreError):
            encode_name(bad)


@pytest.mark.parametrize("make", [_bao_store, _conjur_store])
def test_crud_and_list(make, bao, conjur):
    s = make()
    assert s.get("settings/smtp_password") is None
    s.put("settings/smtp_password", FAKE)
    s.put("environment/3/passwords", '{"windows_admin": "x"}')
    s.put("environment/3/odd name", "v")
    assert s.get("settings/smtp_password") == FAKE
    s.put("settings/smtp_password", "second")                     # replace
    assert s.get("settings/smtp_password") == "second"
    assert s.list() == ["environment/3/odd name", "environment/3/passwords", "settings/smtp_password"]
    assert s.list("environment/") == ["environment/3/odd name", "environment/3/passwords"]
    s.delete("environment/3/passwords")
    s.delete("environment/3/passwords")                           # missing isn't an error
    assert s.get("environment/3/passwords") is None
    ok, msg = s.test_connection()
    assert ok, msg


def test_openbao_session_expiry_and_relogin(bao):
    s = _bao_store()
    s.put("a/b", "1")
    assert bao.logins == 1
    bao.tokens.clear()                        # the token expired or was revoked
    assert s.get("a/b") == "1"
    assert bao.logins == 2


def test_conjur_session_expiry_and_relogin(conjur):
    s = _conjur_store()
    s.put("a/b", "1")
    conjur.tokens.clear()
    assert s.get("a/b") == "1"
    assert conjur.logins == 2


def test_errors_never_carry_values(bao, conjur):
    with pytest.raises(StoreError) as e:
        _bao_store("wrong-secret-id-value").get("x/y")
    assert "refused the AppRole login" in str(e.value) and "wrong-secret-id-value" not in str(e.value)
    with pytest.raises(StoreError) as e:
        _conjur_store("wrong-api-key-value").put("x/y", FAKE)
    assert "refused the login" in str(e.value) and "wrong-api-key-value" not in str(e.value)
    bao.denied.add("other/area")
    with pytest.raises(StoreError) as e:
        _bao_store().put("other/area", FAKE)
    assert str(e.value) == "OpenBao / Vault denied access to other/area"
    ok, msg = _bao_store("wrong-secret-id-value").test_connection()
    assert not ok and "wrong-secret-id-value" not in msg


def test_timeout_and_unreachable(monkeypatch):
    def slow(req):
        raise httpx.ReadTimeout("timed out", request=req)
    monkeypatch.setattr(OpenBaoStore, "transport", httpx.MockTransport(slow))
    with pytest.raises(StoreError, match="didn't answer in time"):
        _bao_store().get("a")

    def refused(req):
        raise httpx.ConnectError("Connection refused", request=req)
    monkeypatch.setattr(ConjurStore, "transport", httpx.MockTransport(refused))
    ok, msg = _conjur_store().test_connection()
    assert not ok and "Couldn't reach CyberArk Conjur" in msg


def test_builtin_store(client):
    with session_factory()() as db:
        s = stores.open_store(db, "builtin")
        s.put("x/one", "1")
        assert s.get("x/one") == "1" and s.list("x/") == ["x/one"]
        s.delete("x/one")
        assert s.get("x/one") is None and s.test_connection()[0]


def test_settings_and_test_connection(client, bao):
    _admin_session(client)
    r = client.get("/api/admin/settings")
    assert r.json()["store_openbao"]["has_secret_id"] is False
    assert r.json()["store_conjur"]["account"] == "labape"
    r = client.put("/api/admin/settings/store_openbao", json={"enabled": True, "url": "http://plain.example.test"})
    assert r.status_code == 422                                     # HTTPS only
    r = client.put("/api/admin/settings/store_openbao", json={"enabled": True, "url": ""})
    assert r.status_code == 422 and "address" in r.text
    with session_factory()() as db:
        assert stores.configured(db) == ["builtin"]
    r = client.put("/api/admin/settings/store_openbao", json={"enabled": True, "url": "https://bao.example.test:8200",
                                                              "role_id": "role-1", "secret_id": FAKE})
    assert r.status_code == 200 and r.json()["has_secret_id"] is True and "secret_id" not in r.json()
    with session_factory()() as db:
        assert get_secret(db, "stores/openbao/secret_id") == FAKE
        assert stores.configured(db) == ["builtin", "openbao"]
    r = client.post("/api/admin/settings/stores/openbao/test")
    assert r.json()["ok"] is True and "policies default, labape" in r.json()["message"]
    with session_factory()() as db:
        assert stores.available(db) == ["builtin", "openbao"]
        put_secret(db, "stores/openbao/secret_id", "wrong")
    stores.forget("openbao")
    r = client.post("/api/admin/settings/stores/openbao/test")
    assert r.json() == {"ok": False, "message": "OpenBao / Vault refused the AppRole login"}
    r = client.get("/api/dashboard/admin")
    store = next(c for c in r.json()["secrets"] if c["name"] == "store.openbao")
    assert store["status"] == "error"
    assert client.post("/api/admin/settings/stores/nope/test").status_code == 404


def test_stack_stores_configure(db_reset):
    """design §26: stores.sh points the settings at the stack's stores, keeping a login LABaPe owns."""
    from labape.db import session_factory
    from labape.security import get_secret
    from labape.stores import credential_secret, stack
    want = {"openbao": {"url": "https://labape-openbao:8200", "role_id": "r1"},
            "conjur": {"url": "https://labape-conjur-tls:8443"}}
    with session_factory()() as db:
        assert stack.needs(db, want) == {"openbao": True, "conjur": True}
        stack.configure(db, want, {"openbao": "sid-1", "conjur": "key-1"})
        assert stack.needs(db, want) == {"openbao": False, "conjur": False}
        assert get_secret(db, credential_secret("openbao")) == "sid-1"
        from labape import appsettings
        assert appsettings.get(db, "secret_stores").default_environment_store == "openbao"   # newly connected
        appsettings.put(db, "secret_stores", appsettings.get(db, "secret_stores").model_copy(
            update={"default_environment_store": "builtin"}))                           # an admin's choice
        stack.configure(db, want, {})                      # a re-run keeps the rotated login
        assert get_secret(db, credential_secret("conjur")) == "key-1"
        assert appsettings.get(db, "secret_stores").default_environment_store == "builtin"   # and that choice
        moved = {**want, "openbao": {"url": "https://other:8200"}}
        assert stack.needs(db, moved)["openbao"] is True    # another address: a new login is needed
