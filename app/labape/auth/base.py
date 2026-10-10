"""Authentication provider interface."""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request


@dataclass
class Identity:
    """What a provider returns for a successful sign-in."""

    subject: str
    username: str
    email: str = ""
    display_name: str = ""
    groups: list[str] = field(default_factory=list)


class AuthProvider:
    """Base class. `kind` is "redirect" (OIDC, SAML) or "password" (LDAP, AD)."""

    name = "base"
    kind = "redirect"
    display_name = "Provider"

    def configured(self) -> bool:
        """Whether settings for this provider exist (unconfigured providers aren't offered)."""
        return False

    # redirect providers
    async def begin_login(self, request: Request):  # pragma: no cover - interface
        raise NotImplementedError

    async def complete_login(self, request: Request) -> Identity:  # pragma: no cover - interface
        raise NotImplementedError

    # password providers
    async def authenticate(self, username: str, password: str) -> Identity | None:  # pragma: no cover
        raise NotImplementedError

    async def test_connection(self) -> tuple[bool, str]:
        return True, "ok"
