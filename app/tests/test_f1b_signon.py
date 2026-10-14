"""F1b (design §47.2; questions 156-157): sign-on between linked stacks, per link and per side."""
import pytest

from labape import linksso, update
from labape.config import get_settings
from labape.db import session_factory
from labape.models import StackLink

from test_api import _admin_session

PEM = "-----BEGIN CERTIFICATE-----\nPEER\n-----END CERTIFICATE-----\n"


@pytest.fixture()
def agent(monkeypatch):
    sent = []
    monkeypatch.setattr(update, "agent_installed", lambda: True)
    monkeypatch.setattr(update, "busy", lambda: False)
    monkeypatch.setattr(update, "write_request", lambda action, target, actor, **kw: sent.append((action, kw)) or
                        {"id": f"r{len(sent)}"})
    monkeypatch.setattr(get_settings(), "oidc_issuer", "https://kvm1.lab:9445/application/o/labape/")
    monkeypatch.setattr(get_settings(), "public_url", "https://kvm1.lab")
    return sent


def _link(db, name="director.jlm.abq"):
    link = StackLink(name=name, url=f"https://{name}", ca_pem=PEM, ca_fingerprint="0" * 64, inbound_hash="h")
    db.add(link)
    db.commit()
    return link


def test_names_and_the_https_requirement(monkeypatch):
    assert linksso.slug("director.jlm.abq") == "labape-link-director-jlm-abq"
    monkeypatch.setattr(get_settings(), "oidc_issuer", "http://kvm1:9000/application/o/labape/")
    with pytest.raises(linksso.SsoError, match="HTTPS"):
        linksso.auth_base()


def test_the_peer_makes_a_client_for_the_callback_only(db_reset, agent):
    with session_factory()() as db:
        link = _link(db)
        out = linksso.provide(link, True, "https://director.jlm.abq:9445/source/oauth/callback/labape-link-kvm1-lab/")
        assert out["client_id"] == "labape-link-director-jlm-abq"
        assert out["issuer"] == "https://kvm1.lab:9445/application/o/labape-link-director-jlm-abq/"
        assert agent[-1] == ("authentik_sso", {"op": "provider_add", "client_id": "labape-link-director-jlm-abq",
                                               "name": "director.jlm.abq",
                                               "redirect_uri": "https://director.jlm.abq:9445/source/oauth/callback/labape-link-kvm1-lab/"})
        for bad in ("https://evil.example:9445/source/oauth/callback/x/", "http://director.jlm.abq:9445/source/oauth/callback/x/",
                    "https://director.jlm.abq/elsewhere"):
            with pytest.raises(linksso.SsoError, match="callback"):
                linksso.provide(link, True, bad)
        linksso.provide(link, False, "")
        assert agent[-1][1]["op"] == "provider_remove"


def test_turning_it_on_asks_the_peer_then_this_agent(db_reset, agent, monkeypatch):
    calls = []
    monkeypatch.setattr(linksso, "_call_peer", lambda db, link, body: calls.append(body) or {
        "client_id": "labape-link-kvm1-lab", "issuer": "https://director.jlm.abq:9445/application/o/labape-link-kvm1-lab/",
        "auth_base": "https://director.jlm.abq:9445"})
    with session_factory()() as db:
        link = _link(db)
        st = linksso.set_sign_on(db, link, True, "admin")
        assert calls == [{"enabled": True,
                          "redirect_uri": "https://kvm1.lab:9445/source/oauth/callback/labape-link-director-jlm-abq/"}]
        op = agent[-1][1]
        assert op["op"] == "source_add" and op["slug"] == "labape-link-director-jlm-abq" and op["peer_ca"] == PEM
        assert op["client_id"] == "labape-link-kvm1-lab" and op["auth_base"] == "https://director.jlm.abq:9445"
        assert st["enabled"] and st["state"] == "applying"
        # The agent's result is picked up.
        monkeypatch.setattr(update, "status", lambda: {"id": "r1", "state": "succeeded", "message": "sso: source added"})
        assert linksso.state(db, link)["state"] == "ok"
        # Off: the peer is told (even if it doesn't answer), the source goes.
        monkeypatch.setattr(linksso, "_call_peer", lambda db, link, body: (_ for _ in ()).throw(linksso.SsoError("gone")))
        st = linksso.set_sign_on(db, link, False, "admin")
        assert agent[-1][1] == {"op": "source_remove", "slug": "labape-link-director-jlm-abq"} and not st["enabled"]


def test_a_busy_agent_refuses(db_reset, agent, monkeypatch):
    monkeypatch.setattr(update, "busy", lambda: True)
    with session_factory()() as db:
        link = _link(db)
        with pytest.raises(linksso.SsoError, match="busy"):
            linksso.set_sign_on(db, link, True, "admin")
        assert link.sso_in is False


def test_the_admin_endpoint(client, agent, monkeypatch):
    from labape import ca
    from test_l1_links import OURS
    _admin_session(client)
    monkeypatch.setattr(ca, "root_pem", lambda: OURS)
    monkeypatch.setattr(linksso, "_call_peer", lambda db, link, body: {
        "client_id": "c", "issuer": "https://p:9445/application/o/c/", "auth_base": "https://p:9445"})
    with session_factory()() as db:
        link_id = _link(db).id
    r = client.put(f"/api/admin/links/{link_id}/sign-on", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    links = client.get("/api/admin/links").json()["links"]
    assert links[0]["sign_on"]["enabled"] is True
