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
    assert names == ["directory", "smtp", "syslog"]
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


# --- the CA and the stores: done by the update agent --------------------------------------------------------------

def _agent(monkeypatch, installed=True, busy=False):
    from labape import update
    sent = []
    monkeypatch.setattr(update, "agent_installed", lambda: installed)
    monkeypatch.setattr(update, "busy", lambda: busy)
    monkeypatch.setattr(update, "write_request", lambda action, target, actor, **kw: sent.append((action, target, actor)) or {})
    return sent


def test_the_restart_fixes_are_offered_only_when_the_agent_is_installed(client, monkeypatch):
    _admin_session(client)
    _agent(monkeypatch, installed=False)
    assert [f["name"] for f in client.get("/api/admin/fixes").json()] == ["directory", "smtp", "syslog"]
    _agent(monkeypatch)
    listed = {f["name"]: f for f in client.get("/api/admin/fixes").json()}
    assert {"platform_ca", "store.openbao", "store.conjur"} <= set(listed)
    assert listed["platform_ca"]["fields"] == []                                     # nothing to type: a confirmation only
    assert listed["store.openbao"]["fields"][0]["options"] == ["Give LABaPe a new login (runs the stores' setup)", "Restart the store"]


def test_restarting_the_ca_asks_the_agent_in_the_actors_name(client, monkeypatch):
    _admin_session(client)
    sent = _agent(monkeypatch)
    r = client.post("/api/admin/fixes/platform_ca", json={"values": {}})
    assert r.status_code == 200 and "restart the platform CA" in r.json()["message"]
    assert len(sent) == 1 and sent[0][:2] == ("restart_service", "labape-ca") and sent[0][2]
    assert "fix.platform_ca" in [a["action"] for a in client.get("/api/admin/audit").json()]


def test_a_store_fix_restarts_it_or_asks_for_a_new_login(client, monkeypatch):
    _admin_session(client)
    sent = _agent(monkeypatch)
    assert client.post("/api/admin/fixes/store.openbao", json={"values": {"what": "Restart the store"}}).status_code == 200
    assert client.post("/api/admin/fixes/store.conjur", json={"values": {"what": "Give LABaPe a new login (runs the stores' setup)"}}).status_code == 200
    assert client.post("/api/admin/fixes/store.conjur", json={"values": {}}).status_code == 200            # the default is a restart
    assert client.post("/api/admin/fixes/store.openbao", json={"values": {"what": "Something else"}}).status_code == 422
    assert [(a, t) for a, t, _ in sent] == [("restart_service", "labape-openbao"), ("stores_setup", ""),
                                            ("restart_service", "labape-conjur")]


def test_the_agent_must_be_there_and_free(client, monkeypatch):
    _admin_session(client)
    sent = _agent(monkeypatch, installed=False)
    assert client.post("/api/admin/fixes/platform_ca", json={"values": {}}).status_code == 409
    _agent(monkeypatch, busy=True)
    busy = client.post("/api/admin/fixes/platform_ca", json={"values": {}})
    assert busy.status_code == 409 and "busy" in busy.text and sent == []


def test_a_refused_login_defaults_to_a_new_login_and_anything_else_to_a_restart(client, monkeypatch):
    from labape import stores
    _admin_session(client)
    _agent(monkeypatch)
    for msg, want in (("OpenBao rejected the login (HTTP 403)", "Give LABaPe a new login (runs the stores' setup)"),
                      ("connection refused", "Restart the store"), ("sealed", "Restart the store")):
        monkeypatch.setattr(stores, "check", lambda db, name, cached=False, m=msg: (False, m))
        got = next(f for f in client.get("/api/admin/fixes").json() if f["name"] == "store.openbao")
        assert got["fields"][0]["default"] == want, msg


# --- syslog ------------------------------------------------------------------------------------------------------

def test_syslog_fix_sends_a_test_then_saves(client, monkeypatch):
    _admin_session(client)
    from labape import notify
    sent = []
    monkeypatch.setattr(notify, "send_syslog", lambda cfg, event, timeout=5.0: sent.append((cfg.host, cfg.port, cfg.protocol, event["actor"])))
    r = client.post("/api/admin/fixes/syslog", json={"values": {"host": "logs.example.test", "port": "6514", "protocol": "tls", "facility": "local3"}})
    assert r.status_code == 200, r.text
    assert "accepted the connection" in r.json()["message"] and sent and sent[0][:3] == ("logs.example.test", 6514, "tls")
    with session_factory()() as db:
        cfg = appsettings.get(db, "syslog")
        assert (cfg.enabled, cfg.host, cfg.port, cfg.protocol, cfg.facility) == (True, "logs.example.test", 6514, "tls", "local3")
    udp = client.post("/api/admin/fixes/syslog", json={"values": {"host": "udp.example.test", "protocol": "udp"}})
    assert udp.status_code == 200 and "UDP gives no answer" in udp.json()["message"]


def test_syslog_fix_that_cannot_connect_changes_nothing(client, monkeypatch):
    _admin_session(client)
    from labape import notify

    def refuse(cfg, event, timeout=5.0):
        raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(notify, "send_syslog", refuse)
    r = client.post("/api/admin/fixes/syslog", json={"values": {"host": "down.example.test", "port": "514", "protocol": "tcp"}})
    assert r.status_code == 422 and "connection refused" in r.text
    with session_factory()() as db:
        assert appsettings.get(db, "syslog").host == ""
    bad = client.post("/api/admin/fixes/syslog", json={"values": {"host": "x.example.test", "protocol": "carrier-pigeon"}})
    assert bad.status_code == 422
    assert client.post("/api/admin/fixes/syslog", json={"values": {"host": "x", "port": "many"}}).status_code == 422
