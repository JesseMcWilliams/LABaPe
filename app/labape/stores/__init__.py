"""Pluggable secret stores (Claude_Docs/Planning_Web-Interface-Design.md §11, §25).

A store is a class registered under the `labape.secrets_providers` entry-point
group (app/pyproject.toml). Built in: `builtin` (the encrypted database
table), `openbao` (OpenBao or HashiCorp Vault) and `conjur`. Each external
store gets a settings section `store_<name>`; its own login credential is kept
in the built-in store as `stores/<name>/<field>`.
"""
from __future__ import annotations

import hashlib
import time
from importlib.metadata import entry_points

from sqlalchemy.orm import Session

from .. import appsettings
from ..security import get_secret
from .base import SecretStore, StoreError
from .builtin import BuiltinStore
from .conjur import ConjurStore
from .openbao import OpenBaoStore

__all__ = ["SecretStore", "StoreError", "available", "check", "configured", "credential_secret", "open_store",
           "providers"]

_BUNDLED: dict[str, type[SecretStore]] = {"builtin": BuiltinStore, "openbao": OpenBaoStore, "conjur": ConjurStore}
_providers: dict[str, type[SecretStore]] | None = None
_instances: dict[str, tuple[str, SecretStore]] = {}
_checks: dict[str, tuple[float, tuple[bool, str]]] = {}
CHECK_SECONDS = 60


def credential_secret(name: str) -> str:
    return f"stores/{name}/{providers()[name].credential_field}"


def providers() -> dict[str, type[SecretStore]]:
    """Every store class, bundled or from an entry point, with its settings
    section registered (so Settings shows it)."""
    global _providers
    if _providers is None:
        found = dict(_BUNDLED)
        for ep in entry_points(group="labape.secrets_providers"):
            found[ep.name] = ep.load()
        for name, cls in found.items():
            if cls.settings_model is not None:
                section = f"store_{name}"
                appsettings.SECTIONS[section] = cls.settings_model
                appsettings.SECRET_FIELDS[section] = (cls.credential_field, f"stores/{name}/{cls.credential_field}",
                                                      f"has_{cls.credential_field}")
        _providers = found
    return _providers


def configured(db: Session) -> list[str]:
    """Stores that are turned on and have their address and credential."""
    out = ["builtin"]
    for name, cls in providers().items():
        if cls.settings_model is None:
            continue
        cfg = appsettings.get(db, f"store_{name}", cached=True)
        if cfg.enabled and cfg.url and get_secret(db, credential_secret(name)):
            out.append(name)
    return out


def open_store(db: Session, name: str) -> SecretStore:
    cls = providers().get(name)
    if cls is None:
        raise StoreError(f"There's no secret store called {name!r}")
    if cls.settings_model is None:
        return cls(db)
    cfg = appsettings.get(db, f"store_{name}", cached=True)
    if not cfg.enabled or not cfg.url:
        raise StoreError(f"{cls.display_name} isn't set up (Settings → Secret stores)")
    cred = get_secret(db, credential_secret(name)) or ""
    if not cred:
        raise StoreError(f"{cls.display_name} has no {cls.credential_label} saved")
    # One instance per configuration, so its session token is reused until the settings change.
    key = hashlib.sha256(f"{cfg.model_dump_json()}\0{cred}".encode()).hexdigest()
    hit = _instances.get(name)
    if hit is None or hit[0] != key:
        hit = (key, cls(cfg, cred))
        _instances[name] = hit
    return hit[1]


def check(db: Session, name: str, cached: bool = False) -> tuple[bool, str]:
    hit = _checks.get(name)
    if cached and hit and time.monotonic() - hit[0] < CHECK_SECONDS:
        return hit[1]
    try:
        result = open_store(db, name).test_connection()
    except StoreError as exc:
        result = (False, str(exc))
    _checks[name] = (time.monotonic(), result)
    return result


def available(db: Session) -> list[str]:
    """Stores a secret may be placed in: configured, and their last check passed."""
    return [n for n in configured(db) if n == "builtin" or check(db, n, cached=True)[0]]


def forget(name: str) -> None:
    """Drop the cached instance and check, after its settings change."""
    _instances.pop(name, None)
    _checks.pop(name, None)
