"""API tokens (design §37, batch A1): for scripts and CI, instead of a browser sign-in.

A personal token (`lpat_`) acts as its owner, with the roles chosen for it but never more than the
owner has at the time it's used (losing a group in Authentik takes effect at the owner's next
sign-in, which re-reads them). A service token (`lpst_`) is an admin's: fixed roles, acting as
`svc:<name>`, not tied to any one person's groups. Both expire (at most a year); only the SHA-256
is stored, and the secret is shown once. Every write made with a token is audited; reads update
the token's last use (at most once a minute).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROLES
from .models import ApiToken, User, utcnow

PREFIX = {"personal": "lpat_", "service": "lpst_"}
MAX_DAYS = 365
TOUCH_EVERY = dt.timedelta(minutes=1)


class TokenError(Exception):
    pass


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def create(db: Session, owner: User, name: str, kind: str, roles: list[str], days: int) -> tuple[ApiToken, str]:
    if kind not in PREFIX:
        raise TokenError("kind is personal or service")
    if not 1 <= days <= MAX_DAYS:
        raise TokenError(f"a token lasts 1 to {MAX_DAYS} days")
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise TokenError(f"unknown role(s): {', '.join(bad)}")
    if kind == "service" and not roles:
        raise TokenError("a service token needs at least one role")
    secret = PREFIX[kind] + secrets.token_urlsafe(30)
    now = utcnow()
    t = ApiToken(name=name, kind=kind, user_id=owner.id, roles=sorted(set(roles)), token_hash=_hash(secret),
                 prefix=secret[:12], created_at=now, expires_at=now + dt.timedelta(days=days))
    db.add(t)
    db.commit()
    return t, secret


def find(db: Session, secret: str) -> ApiToken | None:
    """The live token for `secret` (not revoked, not expired)."""
    if not secret.startswith(tuple(PREFIX.values())):
        return None
    t = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(secret)))
    if t is None or t.revoked_at is not None or _aware(t.expires_at) <= utcnow():
        return None
    return t


def touch(db: Session, t: ApiToken, ip: str) -> None:
    now = utcnow()
    if t.last_used_at is None or now - _aware(t.last_used_at) >= TOUCH_EVERY or t.last_used_ip != ip:
        t.last_used_at, t.last_used_ip = now, ip[:64]
        db.commit()


def effective_roles(t: ApiToken, owner_roles: set[str]) -> set[str]:
    if t.kind == "service":
        return set(t.roles)
    return owner_roles & set(t.roles) if t.roles else set(owner_roles)


def revoke(db: Session, t: ApiToken, by: str) -> None:
    if t.revoked_at is None:
        t.revoked_at, t.revoked_by = utcnow(), by
        db.commit()


def out(t: ApiToken, owner: str = "") -> dict:
    now = utcnow()
    state = "revoked" if t.revoked_at else ("expired" if _aware(t.expires_at) <= now else "active")
    return {"id": t.id, "name": t.name, "kind": t.kind, "owner": owner, "roles": t.roles, "prefix": t.prefix,
            "created_at": t.created_at, "expires_at": t.expires_at, "last_used_at": t.last_used_at,
            "last_used_ip": t.last_used_ip, "revoked_at": t.revoked_at, "revoked_by": t.revoked_by, "state": state}
