"""Users page for admins (question 202): who has signed in, when, what they hold, and a switch that turns an
account off in LABaPe (no sessions, no personal tokens) without touching the sign-in provider.

A disable here (reason `admin`) is never cleared by the provider check or by a sign-in; only Enable clears it.
A user the provider check disabled (reason `provider`, question 196) shows the same way and can be enabled too,
though the next provider check disables a deactivated account again.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import userstatus
from ..db import get_db
from ..models import ApiToken, RemoteSession, User, utcnow
from ..security import Principal, audit, client_ip, require_roles, resolve_roles, subject_key

router = APIRouter(prefix="/api/admin/users", tags=["users"])


def _row(db: Session, u: User, tokens: dict[int, int], sessions: dict[str, int]) -> dict:
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "provider": u.provider,
        "groups": u.groups or [], "roles": sorted(resolve_roles(db, u.username, u.groups or [], subject_key(u))),
        "last_login": u.last_login, "created_at": u.created_at,
        "disabled": u.disabled_at is not None, "disabled_at": u.disabled_at, "disabled_reason": u.disabled_reason,
        "active_tokens": tokens.get(u.id, 0), "open_sessions": sessions.get(u.username, 0),
    }


@router.get("")
def list_users(q: str = "", disabled: bool = False, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    stmt = select(User).where(User.provider != "breakglass")
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.username).like(like), func.lower(User.display_name).like(like),
                              func.lower(User.email).like(like)))
    if disabled:
        stmt = stmt.where(User.disabled_at.is_not(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = list(db.scalars(stmt.order_by(User.last_login.desc().nulls_last(), User.username).limit(limit).offset(offset)))
    now = utcnow()
    tokens = dict(db.execute(select(ApiToken.user_id, func.count()).where(
        ApiToken.kind == "personal", ApiToken.revoked_at.is_(None), ApiToken.expires_at > now).group_by(ApiToken.user_id)).all())
    sessions = dict(db.execute(select(RemoteSession.username, func.count()).where(
        RemoteSession.state == "open").group_by(RemoteSession.username)).all())
    return {"total": total, "users": [_row(db, u, tokens, sessions) for u in users]}


@router.post("/{user_id}/disable")
def disable_user(user_id: int, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if u is None or u.provider == "breakglass":
        raise HTTPException(status_code=404, detail="No such user")
    if u.id == p.user_id:
        raise HTTPException(status_code=409, detail="You can't disable your own account.")
    n = userstatus.disable(db, u, "admin", f"by {p.username}")
    audit(db, p.username, "user.disable.admin", object_type="user", object_id=u.id,
          detail={"user": u.username, "tokens_revoked": n}, source_ip=client_ip(request))
    return {"ok": True, "tokens_revoked": n}


@router.post("/{user_id}/enable")
def enable_user(user_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if u is None or u.provider == "breakglass":
        raise HTTPException(status_code=404, detail="No such user")
    u.disabled_at, u.disabled_reason = None, ""
    db.commit()
    audit(db, p.username, "user.enable.admin", object_type="user", object_id=u.id, detail={"user": u.username},
          source_ip=client_ip(request))
    return {"ok": True}
