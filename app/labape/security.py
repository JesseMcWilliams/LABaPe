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
from .models import AuditLog, RoleBinding, Secret, User, utcnow


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
    db.add(AuditLog(actor=actor, action=action, object_type=object_type, object_id=str(object_id),
                    outcome=outcome, detail=detail or {}, source_ip=source_ip))
    db.commit()


# --- built-in secrets store -------------------------------------------------

def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError("LABAPE_SECRET_KEY is not set")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def put_secret(db: Session, name: str, value: str) -> None:
    token = _fernet().encrypt(value.encode()).decode()
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
    try:
        return _fernet().decrypt(row.value.encode()).decode()
    except InvalidToken:
        return None
