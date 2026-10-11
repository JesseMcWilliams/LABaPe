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
_data_keys: dict[int, Fernet] = {}


def _data_key(db: Session, key_id: int) -> Fernet | None:
    if key_id not in _data_keys:
        row = db.get(SecretDataKey, key_id)
        if row is None:
            return None
        _data_keys[key_id] = Fernet(_fernet().decrypt(row.wrapped.encode()))
    return _data_keys[key_id]


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


def put_secret(db: Session, name: str, value: str) -> None:
    token = encrypt_value(db, value)
    row = db.get(Secret, name)
    if row:
        row.value = token
    else:
        db.add(Secret(name=name, value=token))
    db.commit()


def get_secret(db: Session, name: str) -> str | None:
    row = db.get(Secret, name)
    if not row:
        return None
    return decrypt_value(db, row.value)
