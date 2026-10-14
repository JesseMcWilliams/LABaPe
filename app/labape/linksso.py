"""Sign-on between linked stacks (design §47.2, F1b; questions 146, 153, 156, 157).

Each side's admin decides whether the peer's users may sign in to this stack (per link, per side).
Turning it on for link L (this stack A, peer B):
1. B, asked over the link, has its update agent make an OAuth2 provider and application for A in
   B's Authentik: a public client with PKCE (so no client secret has to travel), redirecting only
   to A's source callback, with B's openid/email/profile mappings (the profile scope carries the
   groups);
2. A's update agent makes an OpenID Connect source "<B>" in A's Authentik with that client, shown
   on A's sign-in page, users named `<user>@<B>` (no clash with A's own), groups linked by name to
   A's groups (question 156 b: B's role groups carry over), and A's Authentik trusts B's platform CA.
Turning it off removes both. The agents run `tools/authentik-test/sso.py` with `ak shell`.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from . import links, update
from .config import get_settings
from .models import StackLink, utcnow
from .security import get_secret


class SsoError(RuntimeError):
    pass


def slug(name: str) -> str:
    return "labape-link-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "peer")


def auth_base() -> str:
    """This stack's Authentik, as browsers and linked stacks reach it (design §47.1)."""
    issuer = urlsplit(get_settings().oidc_issuer)
    if issuer.scheme != "https" or not issuer.netloc:
        raise SsoError("this stack's sign-in isn't Authentik over HTTPS (LABAPE_OIDC_ISSUER; admin guide §9)")
    return f"https://{issuer.netloc}"


def own_name() -> str:
    return urlsplit(links.own_url()).hostname or "labape"


def _agent(action_fields: dict, actor: str) -> dict:
    if not update.agent_installed():
        raise SsoError("this stack's update agent isn't installed; it makes the Authentik objects")
    if update.busy():
        raise SsoError("this stack's update agent is busy; try again in a minute")
    return update.write_request("authentik_sso", "", actor, **action_fields)


# --- the peer's side (B): a client for the stack that asks ------------------------------------------

def provide(link: StackLink, enabled: bool, redirect_uri: str, actor: str = "link") -> dict:
    """Called by the linked stack A: make (or remove) the client A's source signs in with."""
    client_id = slug(link.name)
    if enabled:
        r = urlsplit(redirect_uri)
        if r.scheme != "https" or r.hostname != urlsplit(link.url).hostname or "/source/oauth/callback/" not in r.path:
            raise SsoError("the redirect address isn't the linked stack's sign-in callback")
        req = _agent({"op": "provider_add", "client_id": client_id, "name": link.name, "redirect_uri": redirect_uri},
                     actor)
    else:
        req = _agent({"op": "provider_remove", "client_id": client_id}, actor)
    base = auth_base()
    return {"client_id": client_id, "issuer": f"{base}/application/o/{client_id}/", "auth_base": base,
            "request": req["id"]}


# --- this side (A): let the peer's users in --------------------------------------------------------

def _call_peer(db: Session, link: StackLink, body: dict) -> dict:
    token = get_secret(db, f"link.{link.id}.outbound")
    if not token:
        raise SsoError("no token for this link; link the stacks again")
    try:
        with links.client(link.ca_pem) as h:
            r = h.post(f"{link.url}/api/link/sso/provider", json=body, headers={"Authorization": f"Bearer {token}"},
                       timeout=30)
    except httpx.HTTPError as exc:
        raise SsoError(f"{link.name} didn't answer: {exc}") from exc
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise SsoError(f"{link.name} refused: {detail or r.status_code}")
    return r.json()


def set_sign_on(db: Session, link: StackLink, enabled: bool, actor: str) -> dict:
    source = slug(link.name)
    if enabled:
        base = auth_base()
        if update.busy():
            raise SsoError("this stack's update agent is busy; try again in a minute")
        peer = _call_peer(db, link, {"enabled": True, "redirect_uri": f"{base}/source/oauth/callback/{source}/"})
        req = _agent({"op": "source_add", "slug": source, "name": link.name, "client_id": peer["client_id"],
                      "issuer": peer["issuer"], "auth_base": peer["auth_base"], "peer_ca": link.ca_pem}, actor)
    else:
        try:
            _call_peer(db, link, {"enabled": False, "redirect_uri": ""})
        except SsoError:
            pass                  # the peer may be gone; this side's source goes anyway
        req = _agent({"op": "source_remove", "slug": source}, actor)
    link.sso_in = enabled
    link.sso_state = {"state": "applying", "request": req["id"], "at": utcnow().isoformat(),
                      "message": ("adding" if enabled else "removing") + f" the sign-in source {source}"}
    db.commit()
    return state(db, link)


def state(db: Session, link: StackLink) -> dict:
    """The setup's state, from the agent's status once it has finished this request."""
    st = dict(link.sso_state or {})
    if st.get("state") == "applying":
        s = update.status() or {}
        if s.get("id") == st.get("request") and s.get("state") in ("succeeded", "failed"):
            st.update(state="ok" if s["state"] == "succeeded" else "failed", message=s.get("message", ""))
            link.sso_state = st
            db.commit()
    return {"enabled": bool(link.sso_in), "source": slug(link.name), **st}
