"""The stack's own OpenBao and Conjur (design §26): point LABaPe's store
settings at them. container/stores/stores.sh runs `labape stores-configure`.

A credential is only replaced when LABaPe has none for that store, or its
settings name another address (a different store, e.g. the old test
stores). Once LABaPe has rotated a login (design §25.3) nobody else holds
it, so stores.sh asks first (`--needs`) and only issues the logins LABaPe
lacks: a new Conjur API key would end the one LABaPe has.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import appsettings
from ..security import get_secret, put_secret
from . import credential_secret, forget, providers

STACK_STORES = ("openbao", "conjur")


def needs(db: Session, wanted: dict[str, dict]) -> dict[str, bool]:
    """For each store in `wanted` ({name: {url, ...}}), whether LABaPe needs a new login."""
    providers()          # registers the store_* settings sections
    out = {}
    for name, want in wanted.items():
        cur = appsettings.get(db, f"store_{name}")
        same = cur.enabled and cur.url.rstrip("/") == str(want.get("url", "")).rstrip("/")
        out[name] = not (same and get_secret(db, credential_secret(name)))
    return out


def configure(db: Session, wanted: dict[str, dict], credentials: dict[str, str]) -> list[str]:
    """Turn each store on with the given settings; set its login when one is given."""
    changes = []
    for name, want in wanted.items():
        if name not in STACK_STORES or name not in providers():
            raise ValueError(f"not a stack store: {name}")
        section = f"store_{name}"
        cur = appsettings.get(db, section)
        model = appsettings.SECTIONS[section](**{**cur.model_dump(), **want, "enabled": True})
        if model != cur:
            appsettings.put(db, section, model)
            changes.append(f"{name}: settings for {model.url}")
        if credentials.get(name):
            put_secret(db, credential_secret(name), credentials[name])
            changes.append(f"{name}: login set")
        forget(name)
    return changes
