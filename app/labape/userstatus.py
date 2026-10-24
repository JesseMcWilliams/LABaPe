"""Whether a user is still active in the sign-in provider (question 196).

An API token acts for its owner, and the owner's groups are only re-read at a sign-in. So a person
who is deactivated or removed in Authentik but never signs in again would keep a working token for
up to a year. A sweep asks Authentik (through the Directory settings' read-only API token) about
every owner of a live personal token; the ones it can't find, or finds inactive, are marked
`disabled` with the reason `provider`: their personal tokens are revoked and neither a token nor a
session of theirs works. A later sign-in (which Authentik only allows once the account is active
again) clears the mark. A disabled mark with another reason (an admin's, question 202) is never
cleared by a sign-in or the sweep. When Authentik can't be asked (no token saved, network down) the
sweep changes nothing: unknown is not removed.
"""
from __future__ import annotations

import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings, ca
from .models import ApiToken, User, utcnow
from .security import audit, get_secret

log = logging.getLogger(__name__)

REASON_PROVIDER = "provider"
EVERY_SECONDS = 15 * 60
_last = 0.0


def lookup(db: Session, user: User) -> str | None:
    """'active', 'inactive' (deactivated), 'gone' (no such account), or None when Authentik can't be asked."""
    from .api.directory import api_base
    cfg = appsettings.get(db, "directory")
    token = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) or ""
    base = api_base(cfg)
    if not (cfg.enabled and token and base):
        return None
    try:
        r = httpx.get(f"{base}/api/v3/core/users/", params={"username": user.username, "page_size": 10},
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                      timeout=10, verify=ca.trust() if cfg.verify_tls else False)
        r.raise_for_status()
        found = [u for u in r.json().get("results", []) if str(u.get("username", "")).lower() == user.username.lower()]
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("user check for %s failed: %s", user.username, exc)
        return None
    if not found:
        return "gone"
    return "active" if any(u.get("is_active", True) for u in found) else "inactive"


def disable(db: Session, user: User, reason: str, detail: str = "") -> int:
    """Mark the user disabled and revoke their personal tokens. Returns how many tokens were revoked."""
    now = utcnow()
    user.disabled_at, user.disabled_reason = now, reason
    n = 0
    for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.kind == "personal",
                                               ApiToken.revoked_at.is_(None))):
        t.revoked_at = now
        n += 1
    db.commit()
    audit(db, "system", "user.disable", object_type="user", object_id=user.id,
          detail={"user": user.username, "reason": reason, "why": detail, "tokens_revoked": n})
    return n


def enable_after_sign_in(db: Session, user: User) -> None:
    """A sign-in proves the provider lets the account in: clear a mark the sweep made (not an admin's)."""
    if user.disabled_at is not None and user.disabled_reason == REASON_PROVIDER:
        user.disabled_at, user.disabled_reason = None, ""
        audit(db, user.username, "user.enable", object_type="user", object_id=user.id, detail={"by": "sign-in"})


def sweep(db: Session) -> list[str]:
    """Check the owners of live personal tokens (and users the sweep disabled, in case they are back)."""
    now = utcnow()
    owners = set(db.scalars(select(ApiToken.user_id).where(
        ApiToken.kind == "personal", ApiToken.revoked_at.is_(None), ApiToken.expires_at > now)))
    marked = set(db.scalars(select(User.id).where(User.disabled_reason == REASON_PROVIDER, User.disabled_at.is_not(None))))
    done: list[str] = []
    for user in db.scalars(select(User).where(User.id.in_(owners | marked), User.provider != "breakglass")):
        state = lookup(db, user)
        if state in ("gone", "inactive") and user.disabled_at is None:
            n = disable(db, user, REASON_PROVIDER, state)
            done.append(f"{user.username}: {state}, {n} token(s) revoked")
        elif state == "active" and user.disabled_at is not None and user.disabled_reason == REASON_PROVIDER:
            user.disabled_at, user.disabled_reason = None, ""
            db.commit()
            audit(db, "system", "user.enable", object_type="user", object_id=user.id,
                  detail={"user": user.username, "by": "sweep"})
            done.append(f"{user.username}: active again")
    return done


def sweep_due(db: Session) -> list[str]:
    """Run `sweep` at most every EVERY_SECONDS (the worker calls this on every pass)."""
    global _last
    if time.monotonic() - _last < EVERY_SECONDS:
        return []
    _last = time.monotonic()
    return sweep(db)
