"""Break-glass access (Claude_Docs/Planning_Web-Interface-Options.md decision 25).

There is no standing local admin password. `labape breakglass enable`, run
inside the container, issues a one-time credential valid for a few
minutes; using it opens a time-limited admin session. Running the command
needs control of the host or container runtime, which is the proof of
identity. Everything is audited.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import BreakglassGrant, User, utcnow
from .base import AuthProvider

USERNAME = "breakglass"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db: Session, created_by: str, minutes: int | None = None, local_only: bool = False) -> tuple[str, dt.datetime]:
    token = secrets.token_urlsafe(24)
    expires = utcnow() + dt.timedelta(minutes=minutes or get_settings().breakglass_minutes)
    db.add(BreakglassGrant(token_hash=_hash(token), created_by=created_by, expires_at=expires, local_only=local_only))
    db.commit()
    return token, expires


def revoke_all(db: Session) -> int:
    """Revoke every credential not yet revoked, used ones too: an open break-glass session carries
    its grant's id and stops working at its next request. Returns how many unused credentials were
    revoked (`revoke_all_counts` gives both numbers)."""
    return revoke_all_counts(db)[0]


def revoke_all_counts(db: Session) -> tuple[int, int]:
    """(unused credentials revoked, break-glass sessions ended)."""
    unused = sessions = 0
    for g in db.scalars(select(BreakglassGrant).where(BreakglassGrant.revoked.is_(False))):
        g.revoked = True
        if g.used_at is None:
            unused += 1
        else:
            sessions += 1
    db.commit()
    return unused, sessions


def grant_id(db: Session, token: str) -> int | None:
    g = db.scalar(select(BreakglassGrant).where(BreakglassGrant.token_hash == _hash(token)))
    return g.id if g else None


def session_valid(db: Session, gid: object) -> bool:
    """Whether the grant a break-glass session was opened with is still unrevoked."""
    g = db.get(BreakglassGrant, gid) if isinstance(gid, int) else None
    return g is not None and not g.revoked


def redeem(db: Session, token: str, local: bool) -> tuple[User | None, str]:
    """Validate and consume a one-time credential. Returns (user, reason).

    `local` is true only for requests that came in through Caddy's
    host-loopback listener (container/caddy/Caddyfile), which sets
    X-LABaPe-Local; the public listener strips that header. A client IP
    check can't do this job: behind the container runtime's port
    publishing, host-local browsers don't arrive from 127.0.0.1.
    """
    grant = db.scalar(select(BreakglassGrant).where(BreakglassGrant.token_hash == _hash(token)))
    if grant is None:
        return None, "unknown credential"
    if grant.revoked:
        return None, "credential revoked"
    if grant.used_at is not None:
        return None, "credential already used"
    expires = grant.expires_at if grant.expires_at.tzinfo else grant.expires_at.replace(tzinfo=dt.timezone.utc)
    if utcnow() > expires:
        return None, "credential expired"
    if grant.local_only and not local:
        return None, "credential is host-only (use the host's loopback address)"
    grant.used_at = utcnow()
    user = db.scalar(select(User).where(User.provider == "breakglass", User.subject == USERNAME))
    if user is None:
        user = User(provider="breakglass", subject=USERNAME, username=USERNAME, display_name="Break-glass admin")
        db.add(user)
    user.last_login = utcnow()
    db.commit()
    return user, "ok"


class BreakglassProvider(AuthProvider):
    """Always available; redeemed through /api/auth/breakglass, not a login button."""

    name = "breakglass"
    kind = "token"
    display_name = "Break-glass"

    def configured(self) -> bool:
        return True
