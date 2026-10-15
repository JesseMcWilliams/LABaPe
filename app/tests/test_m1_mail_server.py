"""M1 (design §50.6, question 169): Mailpit as the environment's mail server, and the lab mail relay."""
import base64
import hashlib
import re

from labape import appsettings, catalog, mail_server
from labape.db import session_factory
from labape.models import Environment
from labape.security import get_secret, put_secret

from test_api import _admin_session, _host

SPEC = {"host_groups": [{"name": "mail", "os": "rocky9", "roles": ["linux_server"], "services": ["mail_server"]},
                        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"], "services": ["mail_server"]},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"]}]}


def test_the_catalog_has_it():
    kind, name, _display, _desc, definition = catalog.MAIL_SERVER
    assert (kind, name, definition) == ("service", "mail_server", {"linux": {"role": "mailpit"}})
    assert catalog.MAIL_SERVER in catalog.SEED and "mailpit" in catalog.SERVICE_ROLES


def test_helpers():
    line = mail_server.htpasswd_sha("mailpit", "ThisIsMy_FAKE_Password6!")
    assert line == "mailpit:{SHA}" + base64.b64encode(hashlib.sha1(b"ThisIsMy_FAKE_Password6!").digest()).decode()
    rx = mail_server.relay_matching(["@example.com", "ops@lab.test"])
    assert re.match(rx, "anyone@example.com") and re.match(rx, "ops@lab.test")
    assert not re.match(rx, "ops@lab.test.evil.com") and not re.match(rx, "x@exampleXcom")
    assert mail_server.relay_matching([]) == ""


def test_host_vars_and_relay(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        e = Environment(name="mail1", status="deployed", kvm_host_id=1, created_by="admin", spec=SPEC)
        db.add(e)
        db.commit()
        assert mail_server.vms(db, e) == ["mail1"]                      # Linux groups only
        hv = mail_server.host_vars(db, e)
        pw = get_secret(db, mail_server.ui_secret(e.id, "mail1"))
        assert len(pw) >= 20 and hv["mail1"]["labape_mailpit_ui_auth"] == mail_server.htpasswd_sha("mailpit", pw)
        assert hv["mail1"]["labape_mailpit_relay"] == {}                # off by default
        assert mail_server.host_vars(db, e)["mail1"]["labape_mailpit_ui_auth"].endswith(
            hv["mail1"]["labape_mailpit_ui_auth"].split(":", 1)[1])         # the same password next time
        env_id = e.id
    # The relay, through Settings: its password write-only, only allowed recipients.
    r = client.put("/api/admin/settings/lab_mail_relay", json={"enabled": True, "host": "smtp.example.com", "port": 587,
                                                                "starttls": True, "username": "lab",
                                                                "password": "ThisIsMy_FAKE_Password6!",
                                                                "allow": ["@Example.com", "ops@lab.test"]})
    assert r.status_code == 200, r.text
    got = client.get("/api/admin/settings").json()["lab_mail_relay"]
    assert got["has_password"] is True and "password" not in got and got["allow"] == ["@example.com", "ops@lab.test"]
    assert client.put("/api/admin/settings/lab_mail_relay", json={"allow": ["not-an-address"]}).status_code == 422
    with session_factory()() as db:
        appsettings.forget() if hasattr(appsettings, "forget") else None
        relay = mail_server.host_vars(db, db.get(Environment, env_id))["mail1"]["labape_mailpit_relay"]
        assert relay["host"] == "smtp.example.com" and relay["password"] == "ThisIsMy_FAKE_Password6!"
        assert re.match(relay["matching"], "x@example.com") and not re.match(relay["matching"], "x@other.com")
        put_secret(db, f"environment/{env_id}/inventory", "all:\n  hosts:\n    mail1: {ansible_host: 172.21.50.7}\n")
    info = client.get(f"/api/environments/{env_id}/secrets").json()["mail"]
    assert info == [{"vm": "mail1", "address": "172.21.50.7", "smtp": "172.21.50.7:25", "ui": "https://172.21.50.7:8025/",
                     "user": "mailpit", "relay": ["@example.com", "ops@lab.test"]}]
