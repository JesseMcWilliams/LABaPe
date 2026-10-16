"""OpenID Connect provider (Authentik by default; any OIDC provider works).

The client secret comes from the stack's secret file until LABaPe rotates it
(design §24); from then on the built-in store holds it and every sign-in
reads it from there, so a rotation needs no restart. The store also records
a hash of the file's value at that time: replacing the file by hand later
makes the file win again.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import ca
from ..config import get_settings
from ..db import session_factory
from ..security import get_secret, put_secret
from .base import AuthProvider, Identity

STORED_SECRET = "platform/oidc_client_secret"
FILE_HASH = "platform/oidc_client_secret.file_sha256"
_oauth: OAuth | None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def client_secret(db: Session) -> str:
    """The client secret in use: the rotated one, unless the file changed since."""
    from_file = get_settings().oidc_client_secret
    stored = get_secret(db, STORED_SECRET)
    if stored and get_secret(db, FILE_HASH) == _sha256(from_file):
        return stored
    return from_file


def store_client_secret(db: Session, value: str) -> None:
    put_secret(db, FILE_HASH, _sha256(get_settings().oidc_client_secret))
    put_secret(db, STORED_SECRET, value)


def check_client_secret(client_id: str, secret: str, timeout: float = 15) -> None:
    """Ask the token endpoint whether it accepts the secret, without signing in:
    a made-up code with the right secret gets invalid_grant, with a wrong one
    invalid_client. Raises RuntimeError when it isn't accepted."""
    s = get_settings()
    meta = httpx.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=timeout,
                     verify=ca.trust()).json()
    r = httpx.post(meta["token_endpoint"], timeout=timeout, verify=ca.trust(), data={
        "grant_type": "authorization_code", "code": "labape-secret-check",
        "redirect_uri": s.public_url.rstrip("/") + "/api/auth/oidc/callback",
        "client_id": client_id, "client_secret": secret})
    error = r.json().get("error", "") if r.headers.get("content-type", "").startswith("application/json") else ""
    if error != "invalid_grant":
        raise RuntimeError(f"the token endpoint answered HTTP {r.status_code} {error or '(no OAuth error)'}")


def stack_names() -> list[str]:
    """Every name the stack answers to, lower case: LABAPE_HOSTNAME (the public URL's) first, then LABAPE_ALT_NAMES."""
    s = get_settings()
    names = [(urlsplit(s.public_url).hostname or "").lower()]
    names += [n.strip().lower() for n in s.alt_names.split(",") if n.strip()]
    return [n for i, n in enumerate(names) if n and n not in names[:i]]


def request_name(request: Request) -> str:
    """The name the browser used to reach this stack when it is one of the stack's own, else the main name.
    Sign-in then stays on that name end to end (Kerberos needs the full host name, design §58)."""
    host = urlsplit("//" + (request.headers.get("host") or "")).hostname or ""
    names = stack_names()
    return host if host in names else (names[0] if names else "")


def public_base(request: Request) -> str:
    """https://<name the browser used>[:port] (the public URL's scheme and port)."""
    u = urlsplit(get_settings().public_url)
    name = request_name(request) or (u.hostname or "")
    return urlunsplit((u.scheme, f"{name}:{u.port}" if u.port else name, "", "", "")).rstrip("/")


def _on_name(url: str, name: str) -> str:
    """`url` with its host replaced by `name` (its port kept): Authentik answers on every name of the stack."""
    p = urlsplit(url)
    if not name or (p.hostname or "").lower() == name:
        return url
    host = f"[{name}]" if ":" in name else name
    return urlunsplit((p.scheme, f"{host}:{p.port}" if p.port else host, p.path, p.query, p.fragment))


def _client():
    global _oauth
    s = get_settings()
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="oidc",
            server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret,
            client_kwargs={"scope": s.oidc_scopes, "code_challenge_method": "S256", "verify": ca.trust()},
        )
    with session_factory()() as db:                     # a rotation may have changed it
        _oauth.oidc.client_secret = client_secret(db)
    return _oauth.oidc


class OidcProvider(AuthProvider):
    name = "oidc"
    kind = "redirect"

    @property
    def display_name(self) -> str:  # type: ignore[override]
        return get_settings().oidc_display_name

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.oidc_issuer and s.oidc_client_id)

    async def begin_login(self, request: Request, silent: bool = False):
        """silent: prompt=none, for Reload rights (re-reads groups without a login page)."""
        # The browser goes to Authentik and back on the name it used here (design §58); the server-side calls
        # (metadata, token, keys) keep using the configured issuer.
        redirect_uri = public_base(request) + "/api/auth/oidc/callback"
        extra = {"prompt": "none"} if silent else {}
        client = _client()
        rv = await client.create_authorization_url(redirect_uri, **extra)
        await client.save_authorize_data(request, redirect_uri=redirect_uri, **rv)
        return RedirectResponse(_on_name(rv["url"], request_name(request)), status_code=302)

    async def complete_login(self, request: Request) -> Identity:
        try:
            token = await _client().authorize_access_token(request)
        except Exception as exc:  # authlib raises several error types
            raise HTTPException(status_code=401, detail=f"OIDC sign-in failed: {exc}") from exc
        claims = token.get("userinfo") or {}
        groups_claim = get_settings().oidc_groups_claim
        groups = claims.get(groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        return Identity(
            subject=str(claims.get("sub")),
            username=claims.get("preferred_username") or claims.get("email") or str(claims.get("sub")),
            email=claims.get("email", ""),
            display_name=claims.get("name", ""),
            groups=list(groups),
        )

    async def test_connection(self) -> tuple[bool, str]:
        url = get_settings().oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=5, verify=ca.trust()) as c:
                r = await c.get(url)
            return r.status_code == 200, f"HTTP {r.status_code} from {url}"
        except httpx.HTTPError as exc:
            return False, f"{url}: {exc}"
