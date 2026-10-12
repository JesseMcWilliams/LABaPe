"""Sessions, the current user, roles, audit logging and secret encryption."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROLES, get_settings
from .db import get_db
from .models import SecretDataKey, AuditLog, RoleBinding, Secret, User, utcnow


@dataclass
class Principal:
    """The signed-in user as the API sees it."""

    user_id: int
    username: str
    display_name: str
    groups: list[str] = field(default_factory=list)
    roles: set[str] = field(default_factory=set)
    breakglass: bool = False

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def has_role(self, *roles: str) -> bool:
        return self.is_admin or any(r in self.roles for r in roles)


def resolve_roles(db: Session, username: str, groups: list[str]) -> set[str]:
    roles = {get_settings().role_map_dict[g] for g in groups if g in get_settings().role_map_dict}
    for rb in db.scalars(select(RoleBinding)):
        if (rb.principal_type == "group" and rb.principal in groups) or (
            rb.principal_type == "user" and rb.principal == username
        ):
            if rb.role in ROLES:
                roles.add(rb.role)
    return roles


def start_session(request: Request, user: User, *, breakglass: bool = False, minutes: int | None = None) -> None:
    s = get_settings()
    lifetime = dt.timedelta(minutes=minutes) if minutes else dt.timedelta(hours=s.session_hours)
    request.session.clear()
    request.session.update(
        {
            "uid": user.id,
            "bg": breakglass,
            "exp": (utcnow() + lifetime).timestamp(),
        }
    )


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    sess = request.session
    uid, exp = sess.get("uid"), sess.get("exp", 0)
    if not uid or utcnow().timestamp() > exp:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in")
    user = db.get(User, uid)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in")
    breakglass = bool(sess.get("bg"))
    roles = {"admin"} if breakglass else resolve_roles(db, user.username, user.groups or [])
    return Principal(user.id, user.username, user.display_name or user.username, user.groups or [], roles, breakglass)


def require_roles(*roles: str):
    """Dependency factory: the principal needs one of `roles` (admin always passes)."""

    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has_role(*roles):
            raise HTTPException(status_code=403, detail="Not permitted")
        return p

    return dep


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def audit(db: Session, actor: str, action: str, *, object_type: str = "", object_id: str | int = "",
          outcome: str = "ok", detail: dict | None = None, source_ip: str = "") -> None:
    row = AuditLog(actor=actor, action=action, object_type=object_type, object_id=str(object_id),
                   outcome=outcome, detail=detail or {}, source_ip=source_ip)
    db.add(row)
    db.commit()
    # Also to syslog, when configured (design §21.5); queued, never blocking.
    try:
        from . import appsettings, notify
        cfg = appsettings.get(db, "syslog", cached=True)
        if cfg.enabled:
            notify.audit_to_syslog(cfg, {"id": row.id, "at": row.at, "actor": actor, "action": action,
                                         "object_type": object_type, "object_id": str(object_id),
                                         "outcome": outcome, "detail": detail or {}, "source_ip": source_ip})
    except Exception:  # noqa: BLE001 - auditing itself must never fail on the syslog side
        pass


# --- built-in secrets store -------------------------------------------------

def master_fernet(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def rewrap_master(db: Session, old_key: str, new_key: str) -> int:
    """Re-wrap every data key from one master key to another, and move any
    value still encrypted with the master key itself onto a data key. Used by
    the master key rotation (design §24, phase R3); runs in one transaction."""
    old, new = master_fernet(old_key), master_fernet(new_key)
    deks = {}
    for k in db.scalars(select(SecretDataKey)):
        dek = old.decrypt(k.wrapped.encode())
        deks[k.id] = Fernet(dek)
        k.wrapped = new.encrypt(dek).decode()
    active = db.scalar(select(SecretDataKey).where(SecretDataKey.active.is_(True)).order_by(SecretDataKey.id.desc()))
    if active is None:
        dek = Fernet.generate_key()
        active = SecretDataKey(wrapped=new.encrypt(dek).decode(), active=True)
        db.add(active)
        db.flush()
        deks[active.id] = Fernet(dek)
    moved = 0
    for row in db.scalars(select(Secret).where(Secret.store == "builtin")):
        if not row.value.startswith("v2:"):
            row.value = f"v2:{active.id}:{deks[active.id].encrypt(old.decrypt(row.value.encode())).decode()}"
            moved += 1
    db.commit()
    _data_keys.clear()
    return len(deks)


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError("LABAPE_SECRET_KEY is not set")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


# Envelope encryption (design §24): values are "v2:<data key id>:<token>",
# encrypted with a data key that is stored wrapped by the master key, so the
# data key can rotate (re-encrypting the store) without a restart. Values
# from before data keys (plain master-key tokens) are still read, and are
# rewritten as v2 by the first rotation.
# Unwrapped data keys, cached by id *and* wrapped value: an id can come back
# (SQLite reuses the highest rowid after a delete; tests recreate tables),
# and a re-wrap changes the wrapped value, so a stale entry never matches.
_data_keys: dict[tuple[int, str], Fernet] = {}


def _data_key(db: Session, key_id: int) -> Fernet | None:
    row = db.get(SecretDataKey, key_id)
    if row is None:
        return None
    cache_key = (key_id, row.wrapped)
    if cache_key not in _data_keys:
        _data_keys[cache_key] = Fernet(_fernet().decrypt(row.wrapped.encode()))
    return _data_keys[cache_key]


def active_data_key(db: Session) -> tuple[int, Fernet]:
    """The active data key, made on first use. Read each time (not cached), so
    a rotation in another process is picked up by the next write."""
    row = db.scalar(select(SecretDataKey).where(SecretDataKey.active.is_(True)).order_by(SecretDataKey.id.desc()))
    if row is None:
        row = SecretDataKey(wrapped=_fernet().encrypt(Fernet.generate_key()).decode(), active=True)
        db.add(row)
        db.flush()
    return row.id, _data_key(db, row.id)


def encrypt_value(db: Session, value: str) -> str:
    key_id, f = active_data_key(db)
    return f"v2:{key_id}:{f.encrypt(value.encode()).decode()}"


def decrypt_value(db: Session, token: str) -> str | None:
    try:
        if token.startswith("v2:"):
            _, key_id, rest = token.split(":", 2)
            f = _data_key(db, int(key_id))
            return f.decrypt(rest.encode()).decode() if f else None
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# Secrets LABaPe needs before it can reach an external store, or that are
# too short-lived to send there, always stay built-in (design §25).
PINNED_PREFIXES = ("stores/", "elevated/", "job/")
DEFAULT_STORE_PREFIX = "environment/"     # new secrets here go to Settings → Secret stores' default


def pinned(name: str) -> bool:
    return name.startswith(PINNED_PREFIXES)


def default_store(db: Session, name: str) -> str:
    if pinned(name) or not name.startswith(DEFAULT_STORE_PREFIX):
        return "builtin"
    from . import appsettings
    return appsettings.get(db, "secret_stores", cached=True).default_environment_store


def put_secret(db: Session, name: str, value: str) -> None:
    """Write a secret where its catalog row says it lives (a new one goes to
    its default store). An external write happens first, so a store that
    fails leaves everything as it was; StoreError reaches the caller."""
    row = db.get(Secret, name)
    store = row.store if row else default_store(db, name)
    if store != "builtin":
        from .stores import open_store
        open_store(db, store).put(name, value)
        token = ""
    else:
        token = encrypt_value(db, value)
    if row:
        row.value = token
    else:
        db.add(Secret(name=name, value=token, store=store))
    db.commit()


def get_secret(db: Session, name: str) -> str | None:
    row = db.get(Secret, name)
    if not row:
        return None
    if row.store != "builtin":
        from .stores import open_store
        return open_store(db, row.store).get(name)
    return decrypt_value(db, row.value)
