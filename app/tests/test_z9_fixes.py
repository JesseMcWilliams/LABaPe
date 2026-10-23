"""The Fix button (app/labape/fixes.py): what each fix asks for, what it does, and that the credentials it is given are
neither kept nor logged."""
import json

import httpx
import pytest

from labape import appsettings, fixes
from labape.db import session_factory
from labape.models import AuditLog
from labape.security import get_secret, put_secret
from sqlalchemy import select
from test_api import _admin_session

SECRET = "ADMIN-TOKEN-SHOULD-NOT-BE-KEPT"


class FakeAuthentik:
    """Just enough of Authentik's API, remembering what it was asked to do."""

    def __init__(self, role=False, user=False, token=False, deny=False):
        self.calls, self.deny = [], deny
        self.state = {"role": role, "user": user, "token": token}

    def __call__(self, method, url, **kw):
        path = url.split("/api/v3", 1)[1]
        self.calls.append((method, path))
        assert kw["headers"]["Authorization"] == f"Bearer {SECRET}" or path.startswith("/core/groups/")
        if self.deny:
            return httpx.Response(403, json={"detail": "no"}, request=httpx.Request(method, url))
        body = None
        if path.startswith("/rbac/roles/") and method == "GET":
            body = {"results": [{"pk": "R1", "name": fixes.ROLE}] if self.state["role"] else []}
        elif path == "/rbac/roles/" and method == "POST":
            self.state["role"] = True
            body = {"pk": "R1", "name": fixes.ROLE}
        elif "assigned_by_roles" in path:
            body = [{"id": "1"}]
        elif path.startswith("/core/users/?") and method == "GET":
            body = {"results": [{"pk": 18, "username": fixes.SERVICE_USER, "roles_obj": []}] if self.state["user"] else []}
        elif path == "/core/users/" and method == "POST":
            self.state["user"] = True
            body = {"pk": 18, "username": fixes.SERVICE_USER, "roles_obj": []}
        elif path.startswith("/core/users/18/") and method == "PATCH":
            body = {}
        elif path.startswith("/core/tokens/?") and method == "GET":
            body = {"results": [{"identifier": fixes.SERVICE_USER}] if self.state["token"] else []}
        elif path == "/core/tokens/" and method == "POST":
            self.state["token"] = True
            body = {}
        elif path.endswith("/view_key/"):
            body = {"key": "dir-token-key"}
        assert body is not None, (method, path)
        return httpx.Response(200, json=body, request=httpx.Request(method, url))


def _run(monkeypatch, fake, values):
    monkeypatch.setattr(httpx, "request", fake)
    from labape.api import directory
    monkeypatch.setattr(directory, "authentik_groups", lambda cfg, token, q, timeout=5.0: ["a", "b", "c"])
    with session_factory()() as db:
        return fixes.fix_directory(db, values)


def test_directory_fix_creates_everything_and_keeps_only_the_read_only_token(client, monkeypatch):
    _admin_session(client)
    monkeypatch.setenv("LABAPE_OIDC_ISSUER", "https://ak.example.test/application/o/labape/")
    fake = FakeAuthentik()
    msg = _run(monkeypatch, fake, {"token": SECRET, "api_url": "https://ak.example.test"})
    assert "3 group(s)" in msg and SECRET not in msg
    made = [(m, p) for m, p in fake.calls if m in ("POST", "PATCH")]
    assert ("POST", "/rbac/roles/") in made and ("POST", "/core/users/") in made and ("POST", "/core/tokens/") in made
    with session_factory()() as db:
        assert get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) == "dir-token-key"
        assert appsettings.get(db, "directory").enabled is True


def test_directory_fix_reuses_what_exists(client, monkeypatch):
    _admin_session(client)
    fake = FakeAuthentik(role=True, user=True, token=True)
    _run(monkeypatch, fake, {"token": SECRET, "api_url": "https://ak.example.test"})
    posts = [(m, p) for m, p in fake.calls if m == "POST" and "assigned_by_roles" not in p]
    assert posts == []                                   # nothing made twice; the role got its permission again, harmlessly


def test_directory_fix_says_when_the_token_is_refused(client, monkeypatch):
    _admin_session(client)
    from labape.errors import FieldError
    with pytest.raises(FieldError) as exc:
        _run(monkeypatch, FakeAuthentik(deny=True), {"token": SECRET, "api_url": "https://ak.example.test"})
    assert exc.value.field == "token" and "admin's API token" in str(exc.value.detail) and SECRET not in str(exc.value.detail)


def test_listing_and_run_are_for_admins_and_never_log_a_secret(client, monkeypatch):
    r = client.get("/api/admin/fixes")
    assert r.status_code in (401, 403)
    _admin_session(client)
    names = [f["name"] for f in client.get("/api/admin/fixes").json()]
    assert names == ["directory", "smtp"]
    assert client.post("/api/admin/fixes/nothing", json={"values": {}}).status_code == 404
    assert client.post("/api/admin/fixes/directory", json={"values": {}}).status_code == 422       # the token is needed
    monkeypatch.setattr(httpx, "request", FakeAuthentik())
    from labape.api import directory
    monkeypatch.setattr(directory, "authentik_groups", lambda cfg, token, q, timeout=5.0: [])
    r = client.post("/api/admin/fixes/directory", json={"values": {"token": SECRET, "api_url": "https://ak.example.test"}})
    assert r.status_code == 200 and r.json()["ok"] and SECRET not in r.text
    with session_factory()() as db:
        events = [e for e in db.scalars(select(AuditLog)) if e.action == "fix.directory"]
        assert events and SECRET not in json.dumps([e.detail for e in events], default=str)


def test_smtp_fix_checks_then_saves(client, monkeypatch):
    _admin_session(client)
    seen = {}
    from labape import notify
    monkeypatch.setattr(notify, "smtp_check", lambda cfg, pw, timeout=10: seen.update(host=cfg.host, pw=pw))
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda cfg, pw, to, subj, body, timeout=20: sent.append(to))
    r = client.post("/api/admin/fixes/smtp", json={"values": {
        "host": "mail.example.test", "port": "2525", "security": "starttls", "username": "u", "password": "PW1",
        "from_address": "labape@example.test", "test_to": "me@example.test"}})
    assert r.status_code == 200, r.text
    assert seen == {"host": "mail.example.test", "pw": "PW1"} and sent == [["me@example.test"]]
    with session_factory()() as db:
        cfg = appsettings.get(db, "smtp")
        assert (cfg.enabled, cfg.host, cfg.port) == (True, "mail.example.test", 2525)
        assert get_secret(db, appsettings.SMTP_PASSWORD_SECRET) == "PW1"
    # a server that refuses changes nothing
    import smtplib

    def refuse(cfg, pw, timeout=10):
        raise smtplib.SMTPAuthenticationError(535, b"nope")
    monkeypatch.setattr(notify, "smtp_check", refuse)
    r = client.post("/api/admin/fixes/smtp", json={"values": {
        "host": "other.example.test", "port": "25", "security": "none", "username": "", "password": "",
        "from_address": "x@example.test"}})
    assert r.status_code == 422
    with session_factory()() as db:
        assert appsettings.get(db, "smtp").host == "mail.example.test"
