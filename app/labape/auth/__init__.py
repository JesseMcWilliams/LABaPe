"""Pluggable authentication providers (Claude_Docs/Planning_Web-Interface-Design.md §12).

A provider is a class registered under the `labape.auth_providers`
entry-point group (see app/pyproject.toml). Built in: `oidc` (Authentik or
any OIDC provider) and `breakglass`. LDAP and Active Directory arrive as
further providers in phase 10h without changes here.
"""
from __future__ import annotations

from importlib.metadata import entry_points

from sqlalchemy.orm import Session

from ..models import Setting
from .base import AuthProvider, Identity

__all__ = ["AuthProvider", "Identity", "load_providers", "provider_enabled", "set_provider_enabled"]


def load_providers() -> dict[str, AuthProvider]:
    providers: dict[str, AuthProvider] = {}
    for ep in entry_points(group="labape.auth_providers"):
        cls = ep.load()
        provider = cls()
        if provider.configured():
            providers[ep.name] = provider
    return providers


def provider_enabled(db: Session, name: str) -> bool:
    row = db.get(Setting, f"auth.{name}")
    return True if row is None else bool(row.value.get("enabled", True))


def set_provider_enabled(db: Session, name: str, enabled: bool) -> None:
    row = db.get(Setting, f"auth.{name}")
    if row is None:
        db.add(Setting(key=f"auth.{name}", value={"enabled": enabled}))
    else:
        row.value = {**row.value, "enabled": enabled}
    db.commit()
