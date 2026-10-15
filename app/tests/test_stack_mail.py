"""The stack's own mail server, Mailpit (design §52): its options, the first-start Email defaults, the Mail page's API."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from labape import appsettings, notify, stack_mail
from labape.db import session_factory
from labape.security import Principal, current_principal, get_secret

from test_api import _admin_session

ROOT = Path(__file__).resolve().parents[2]
PW = "ThisIsMy_FAKE_Password6!"


@pytest.fixture()
def mailpit(tmp_path, monkeypatch):
    monkeypatch.setenv("LABAPE_MAIL_HOST", "labape-mailpit")
    monkeypatch.setenv("LABAPE_MAIL_PORT", "1025")
    monkeypatch.setenv("LABAPE_MAILPIT_CONFIG", str(tmp_path))
    monkeypatch.setenv("LABAPE_PUBLIC_URL", "https://lab.example.test")
    return tmp_path


def _cfg(**kw):
    return appsettings.StackMailSettings(**kw)


def test_the_options_written_for_mailpit():
    env, relay = stack_mail.render(_cfg(), "")
    assert relay is None and "MP_SMTP_RELAY" not in env and "MP_MAX_MESSAGES=5000" in env and "MP_MAX_AGE=30d" in env
    assert re.search(r"^LABAPE_REV=[0-9a-f]{12}$", env, re.M)
    env, relay = stack_mail.render(_cfg(relay=True, host="smtp.example.com", port=587, username="lab", auth="login",
                                        allow=["Ops@Example.com", "@example.org"], override_from="noreply@example.com"), PW)
    assert "MP_SMTP_RELAY_CONFIG=/config/relay.yaml" in env and "MP_SMTP_RELAY_ALL" not in env
    assert "host: smtp.example.com" in relay and "starttls: true" in relay and "auth: login" in relay
    assert f"password: {PW}" in relay and "override-from: noreply@example.com" in relay and "allow-insecure" not in relay
    env, relay = stack_mail.render(_cfg(relay=True, host="mx.example.com", port=465, security="ssl", verify_tls=False,
                                        forward="all"), "")
    assert "MP_SMTP_RELAY_ALL=true" in env and "MP_SMTP_RELAY_MATCHING" not in env
    assert "tls: true" in relay and "starttls" not in relay and "allow-insecure: true" in relay and "auth" not in relay
    # Forwarding on with an empty list: the relay exists (manual release) but nothing is forwarded by itself.
    env, relay = stack_mail.render(_cfg(relay=True, host="mx.example.com"), "")
    assert "MP_SMTP_RELAY_CONFIG" in env and "MATCHING" not in env and "ALL" not in env
    # Forwarding off, even with a host saved: nothing.
    assert stack_mail.render(_cfg(relay=False, host="mx.example.com"), "")[1] is None


def test_the_recipient_pattern():
    rx = stack_mail.relay_regex(["ops@example.com", "@example.org"])
    for yes in ("ops@example.com", "OPS@Example.COM", "anyone@example.org", "a.b@example.org"):
        assert re.search(rx, yes), yes
    for no in ("ops@example.com.evil.net", "xops@example.com", "me@notexample.org", "me@example.orgx"):
        assert not re.search(rx, no), no
    assert stack_mail.relay_regex([]) == ""


def test_apply_writes_only_what_changed(db_reset, mailpit):
    with session_factory()() as db:
        assert stack_mail.apply(db) is True
        first = (mailpit / "mailpit.env").read_text()
        assert not (mailpit / "relay.yaml").exists() and stack_mail.apply(db) is False       # same again: untouched
        appsettings.put(db, "stack_mail", _cfg(relay=True, host="smtp.example.com", username="lab"))
        from labape.security import put_secret
        put_secret(db, stack_mail.PASSWORD_SECRET, PW)
        assert stack_mail.apply(db) is True
        assert (mailpit / "mailpit.env").read_text() != first and f"password: {PW}" in (mailpit / "relay.yaml").read_text()
        if os.name != "nt":
            assert (mailpit / "relay.yaml").stat().st_mode & 0o077 == 0                       # the password: owner only
        appsettings.put(db, "stack_mail", _cfg(relay=False))
        assert stack_mail.apply(db) is True and not (mailpit / "relay.yaml").exists()         # forwarding off: the file goes
        assert stack_mail.wanted_revision() and stack_mail.applied_revision() == ""
        (mailpit / "applied").write_text(stack_mail.wanted_revision() + "\n")
        assert stack_mail.applied_revision() == stack_mail.wanted_revision()


def test_first_start_points_email_at_mailpit_once(db_reset, mailpit):
    with session_factory()() as db:
        appsettings.put(db, "smtp", appsettings.SmtpSettings())               # a saved row of defaults is still "never set up"
        done = stack_mail.startup(db)
        smtp = appsettings.get(db, "smtp")
        assert (smtp.enabled, smtp.host, smtp.port, smtp.security, smtp.username) == (True, "labape-mailpit", 1025, "none", "")
        assert smtp.from_address == "labape@lab.example.test" and smtp.notify_jobs is False and len(done) == 3
        assert appsettings.get(db, "monitoring").email_to == ["alerts@lab.example.test"]
        appsettings.put(db, "smtp", smtp.model_copy(update={"enabled": False, "host": "mail.corp.test"}))   # an admin's choice
        stack_mail.startup(db)
        assert appsettings.get(db, "smtp").host == "mail.corp.test"
        assert stack_mail.uses_mailpit(smtp) and not stack_mail.uses_mailpit(appsettings.get(db, "smtp"))


def test_no_mailpit_without_the_container_stack(db_reset, monkeypatch):
    monkeypatch.delenv("LABAPE_MAIL_HOST", raising=False)
    with session_factory()() as db:
        assert stack_mail.startup(db) == [] and appsettings.get(db, "smtp").host == ""
        assert stack_mail.status(db)["available"] is False


def test_status_reads_mailpit(db_reset, mailpit, monkeypatch):
    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, json={"Version": "v1.31.5", "Messages": 7, "Unread": 2, "DatabaseSize": 2097152},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(stack_mail.httpx, "get", fake_get)
    with session_factory()() as db:
        stack_mail.startup(db)
        s = stack_mail.status(db)
        assert seen == ["http://labape-mailpit:8025/mail/api/v1/info"]
        assert (s["reachable"], s["version"], s["messages"], s["unread"]) == (True, "v1.31.5", 7, 2)
        assert s["applied"] is False and s["email_uses_mailpit"] is True
        (mailpit / "applied").write_text(stack_mail.wanted_revision())
        assert stack_mail.status(db)["applied"] is True
        monkeypatch.setattr(stack_mail.httpx, "get", lambda url, timeout: (_ for _ in ()).throw(httpx.ConnectError("refused")))
        down = stack_mail.status(db)
        assert down["reachable"] is False and "refused" in down["error"]


def _as(client, username, *roles):
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username=username, display_name="", roles=set(roles))


def test_the_api(client, mailpit, monkeypatch):
    _admin_session(client)
    assert client.get("/api/mail/auth").status_code == 200
    # Forwarding needs a server, and a user name needs its password.
    base = client.get("/api/admin/settings").json()["stack_mail"]
    assert base["relay"] is False and base["has_password"] is False and "password" not in base
    assert client.put("/api/admin/settings/stack_mail", json={**base, "relay": True}).status_code == 422
    r = client.put("/api/admin/settings/stack_mail", json={**base, "relay": True, "host": "smtp.example.com", "username": "lab"})
    assert r.status_code == 422 and "password" in r.text
    assert client.put("/api/admin/settings/stack_mail", json={**base, "allow": ["not-an-address"]}).status_code == 422
    r = client.put("/api/admin/settings/stack_mail", json={**base, "relay": True, "host": "smtp.example.com", "username": "lab",
                                                           "password": PW, "allow": ["@Example.org", "ops@example.com"]})
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is True and "password" not in r.json() and r.json()["allow"] == ["@example.org", "ops@example.com"]
    assert f"password: {PW}" in (mailpit / "relay.yaml").read_text()                          # applied at once
    with session_factory()() as db:
        assert get_secret(db, stack_mail.PASSWORD_SECRET) == PW
    # The test email goes the way LABaPe's own do, through Settings -> Email.
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda cfg, password, to, subject, body: sent.append((cfg.host, to, subject)))
    assert client.post("/api/admin/mail/test", json={"to": "x@example.com"}).status_code == 422          # Email isn't set up yet
    assert client.post("/api/admin/mail/use-for-email").status_code == 200
    r = client.post("/api/admin/mail/test", json={"to": "x@example.com"})
    assert r.status_code == 200 and sent == [("labape-mailpit", ["x@example.com"], "LABaPe test email")] and "Mailpit" in r.json()["message"]
    monkeypatch.setattr(stack_mail.httpx, "get", lambda url, timeout: httpx.Response(
        200, json={"Version": "v1", "Messages": 1}, request=httpx.Request("GET", url)))
    assert client.get("/api/admin/mail").json()["email_uses_mailpit"] is True
    # Only admins: not the Mail API, and not Mailpit's web page (Caddy asks /api/mail/auth first).
    _as(client, "dev", "deployer")
    assert client.get("/api/mail/auth").status_code == 403 and client.get("/api/admin/mail").status_code == 403
    client.app.dependency_overrides.clear()


def test_the_stack_files():
    compose = (ROOT / "container" / "compose.yaml").read_text(encoding="utf-8")
    assert "image: docker.io/axllent/mailpit:v" in compose and "profiles" not in compose.split("labape-mailpit:")[1].split("\n  labape-ca:")[0]
    for needle in ("LABAPE_MAIL_HOST: labape-mailpit", "LABAPE_MAILPIT_CONFIG: /var/lib/labape-mailpit",
                   "labape-mailpit-config:/var/lib/labape-mailpit", "labape-mailpit-config:/config",
                   "./mailpit/supervise.sh:/usr/local/bin/labape-mailpit.sh:ro", "MP_WEBROOT: /mail"):
        assert needle in compose, needle
    mailpit_block = compose.split("labape-mailpit:\n")[1].split("\n  labape-ca:")[0]
    assert "ports:" not in mailpit_block                                                        # inside the stack only
    for name in ("Caddyfile", "Caddyfile.labape-ca", "Caddyfile.names"):
        text = (ROOT / "container" / "caddy" / name).read_text(encoding="utf-8")
        assert "forward_auth labape-api:8000" in text and "uri /api/mail/auth" in text and "reverse_proxy labape-mailpit:8025" in text
        assert text.index("handle /mail/*") < text.index("reverse_proxy labape-api:8000")       # before the catch-all


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="a POSIX shell script")
def test_the_supervisor_restarts_mailpit_when_the_options_change(tmp_path):
    conf = tmp_path / "conf"
    conf.mkdir()
    fake = tmp_path / "mailpit"
    fake.write_text('#!/bin/sh\necho "start ${MP_SMTP_RELAY_MATCHING:-none} ${MP_MAX_AGE:-} ${BOGUS:-}" >> "$LOG"\nexec sleep 300\n')
    fake.chmod(0o755)
    log = tmp_path / "log"
    (conf / "mailpit.env").write_text("LABAPE_REV=aaa\nMP_MAX_AGE=30d\nBOGUS=1\nMP_SMTP_RELAY_MATCHING=(?i)(x@y\\.z)\n")
    env = {**os.environ, "LABAPE_MAILPIT_CONF": str(conf), "LABAPE_MAILPIT_BIN": str(fake), "LOG": str(log)}
    proc = subprocess.Popen(["sh", str(ROOT / "container" / "mailpit" / "supervise.sh")], env=env)
    try:
        def wait(cond, seconds=15):
            end = time.time() + seconds
            while time.time() < end:
                if cond():
                    return True
                time.sleep(0.3)
            return False
        assert wait(lambda: (conf / "applied").exists() and (conf / "applied").read_text().strip() == "aaa")
        assert log.read_text().splitlines() == ["start (?i)(x@y\\.z) 30d "]                    # only the known keys, as text
        (conf / "mailpit.env").write_text("LABAPE_REV=bbb\nMP_MAX_AGE=7d\n")
        assert wait(lambda: (conf / "applied").read_text().strip() == "bbb", 20)
        assert log.read_text().splitlines()[-1] == "start none 7d "
    finally:
        proc.terminate()
        proc.wait(timeout=15)
