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
from ..models import Secret, SecretPolicy
from ..security import audit, decrypt_value, encrypt_value, get_secret, pinned
from . import layout
from .base import SecretStore, StoreError
from .builtin import BuiltinStore
from .conjur import ConjurStore
from .openbao import OpenBaoStore

__all__ = ["SecretStore", "StoreError", "available", "check", "configured", "credential_secret", "ext_delete", "ext_get",
           "ext_put", "move", "open_store", "providers"]

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


def ext_get(db: Session, store: str, name: str) -> str | None:
    """A secret from an external store, at its place in the store's layout (layout.py). One still under
    its name from before the layout is moved there on the way."""
    s, path = open_store(db, store), layout.store_name(db, store, name)
    value = s.get(path)
    if value is None and path != name:
        value = s.get(name)
        if value is not None:
            s.put(path, value)
            if s.get(path) == value:
                s.delete(name)
    return value


def ext_put(db: Session, store: str, name: str, value: str) -> None:
    s, path = open_store(db, store), layout.store_name(db, store, name)
    s.put(path, value)
    if path != name:
        try:
            s.delete(name)            # a copy from before the layout, if any
        except StoreError:
            pass


def ext_delete(db: Session, store: str, name: str) -> None:
    s, path = open_store(db, store), layout.store_name(db, store, name)
    s.delete(path)
    if path != name:
        s.delete(name)


def move(db: Session, name: str, target: str, actor: str = "system", *, source_ip: str = "") -> str:
    """Move one secret to another store (design §25): read it, write it there,
    read it back, point the catalog row at the new store, then delete the old
    copy. Until the row changes nothing else does, so a failure leaves the
    secret where it was. Used by Move on the Secrets page and by a policy's
    store change. Returns a summary; raises StoreError."""
    row = db.get(Secret, name)
    if row is None:
        raise StoreError(f"There's no stored secret {name}")
    source = row.store
    if source == target:
        return f"{name} is already in {_label(target)}"
    if pinned(name) and target != "builtin":
        raise StoreError(f"{name} stays in the built-in store: LABaPe needs it to reach the other stores")
    if target not in available(db):
        raise StoreError(f"{_label(target)} isn't available: set it up and pass its test in Settings → Secret stores")
    value = get_secret(db, name)
    if value is None:
        raise StoreError(f"{name} can't be read from {_label(source)}, so it wasn't moved")
    if target == "builtin":
        token = encrypt_value(db, value)
        if decrypt_value(db, token) != value:
            raise StoreError(f"{name} didn't read back after encrypting; it stays in {_label(source)}")
    else:
        ext_put(db, target, name, value)
        if ext_get(db, target, name) != value:
            raise StoreError(f"{name} didn't read back from {_label(target)}; it stays in {_label(source)}")
        token = ""
    row.store, row.value = target, token
    db.commit()
    note = ""
    if source != "builtin":
        try:
            ext_delete(db, source, name)
        except StoreError as exc:
            note = f"; the old copy is still in {_label(source)} ({exc})"
    if row.policy_id:
        pol = db.get(SecretPolicy, row.policy_id)
        if pol is not None and pol.store != target:
            pol.store = target
            db.commit()
    audit(db, actor, "secret.move", object_type="secret", object_id=name,
          detail={"name": name, "from": source, "to": target, "old_copy_left": bool(note)},
          outcome="ok", source_ip=source_ip)
    return f"{name} moved from {_label(source)} to {_label(target)}{note}"


def _label(name: str) -> str:
    cls = providers().get(name)
    return cls.display_name if cls else name
