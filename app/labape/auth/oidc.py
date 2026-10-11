"""OpenID Connect provider (Authentik by default; any OIDC provider works)."""
from __future__ import annotations

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request

from ..config import get_settings
from .base import AuthProvider, Identity

_oauth: OAuth | None = None


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
            client_kwargs={"scope": s.oidc_scopes, "code_challenge_method": "S256"},
        )
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

    async def begin_login(self, request: Request):
        redirect_uri = get_settings().public_url.rstrip("/") + "/api/auth/oidc/callback"
        return await _client().authorize_redirect(request, redirect_uri)

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
            async with httpx.AsyncClient(timeout=5, verify=True) as c:
                r = await c.get(url)
            return r.status_code == 200, f"HTTP {r.status_code} from {url}"
        except httpx.HTTPError as exc:
            return False, f"{url}: {exc}"
