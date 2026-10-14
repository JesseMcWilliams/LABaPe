"""OpenID Connect provider (Authentik by default; any OIDC provider works).

The client secret comes from the stack's secret file until LABaPe rotates it
(design §24); from then on the built-in store holds it and every sign-in
reads it from there, so a rotation needs no restart. The store also records
a hash of the file's value at that time: replacing the file by hand later
makes the file win again.
"""
from __future__ import annotations

import hashlib

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
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
        redirect_uri = get_settings().public_url.rstrip("/") + "/api/auth/oidc/callback"
        extra = {"prompt": "none"} if silent else {}
        return await _client().authorize_redirect(request, redirect_uri, **extra)

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
