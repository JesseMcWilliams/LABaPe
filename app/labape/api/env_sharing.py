"""Sharing an environment and handing it over (question 201).

An owner (or an admin) shares an environment with a user or a group: they see it and its credentials-free
details, like any grant at level `user`. An owner can hand ownership to another account, and an admin can
do it for an environment whose owner has left. Sharing changes who sees it; it doesn't widen anyone's roles,
so what a person can do in it is still limited by their roles.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Environment, Grant, User
from ..permissions import account_subject, is_owner
from ..security import Principal, audit, client_ip, current_principal

router = APIRouter(prefix="/api/environments", tags=["environments"])


class ShareIn(BaseModel):
    principal_type: Literal["user", "group"]
    principal: str = Field(min_length=1, max_length=255)


class TransferIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    keep_access: bool = True       # the previous owners stay as users of it


def _env(db: Session, env_id: int, p: Principal) -> Environment:
    e = db.get(Environment, env_id)
    if e is None or e.status == "destroyed" or not is_owner(db, p, "environment", env_id):
        raise HTTPException(status_code=404 if e is None else 403,
                            detail="No such environment" if e is None else "Only the environment's owners and admins can share it")
    return e


def _rows(db: Session, env_id: int) -> list[dict]:
    out = []
    for g in db.scalars(select(Grant).where(Grant.object_type == "environment", Grant.object_id == env_id)
                        .order_by(Grant.level, Grant.principal_type, Grant.principal)):
        out.append({"id": g.id, "principal_type": g.principal_type, "principal": g.principal, "level": g.level})
    return out


@router.get("/{env_id}/sharing")
def sharing(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    _env(db, env_id, p)
    return {"grants": _rows(db, env_id)}


@router.get("/{env_id}/sharing/candidates")
def candidates(env_id: int, q: str = Query("", max_length=100), db: Session = Depends(get_db),
               p: Principal = Depends(current_principal)):
    """Accounts that have signed in, to pick from (owners only; the list of users is otherwise an admin's)."""
    _env(db, env_id, p)
    like = f"%{q.strip().lower()}%"
    rows = db.scalars(select(User).where(User.provider != "breakglass", User.disabled_at.is_(None),
                                         or_(func.lower(User.username).like(like), func.lower(User.display_name).like(like)))
                      .order_by(User.username).limit(20))
    return {"users": [{"username": u.username, "display_name": u.display_name} for u in rows]}


@router.post("/{env_id}/sharing", status_code=201)
def share(env_id: int, body: ShareIn, request: Request, db: Session = Depends(get_db),
          p: Principal = Depends(current_principal)):
    e = _env(db, env_id, p)
    name = body.principal.strip()
    if body.principal_type == "user" and not db.scalar(select(User.id).where(User.username == name, User.provider != "breakglass")):
        raise HTTPException(status_code=422, detail=f"No account named {name} has signed in to LABaPe yet.")
    if db.scalar(select(Grant.id).where(Grant.object_type == "environment", Grant.object_id == env_id,
                                        Grant.principal_type == body.principal_type, Grant.principal == name)):
        raise HTTPException(status_code=409, detail=f"{name} already has access.")
    g = Grant(object_type="environment", object_id=env_id, principal_type=body.principal_type, principal=name,
              level="user", subject=account_subject(db, name) if body.principal_type == "user" else None)
    db.add(g)
    db.commit()
    audit(db, p.username, "environment.share", object_type="environment", object_id=e.id,
          detail=body.model_dump(), source_ip=client_ip(request))
    return {"id": g.id}


@router.delete("/{env_id}/sharing/{grant_id}")
def unshare(env_id: int, grant_id: int, request: Request, db: Session = Depends(get_db),
            p: Principal = Depends(current_principal)):
    e = _env(db, env_id, p)
    g = db.get(Grant, grant_id)
    if g is None or g.object_type != "environment" or g.object_id != env_id:
        raise HTTPException(status_code=404, detail="No such grant")
    if g.level == "owner":
        raise HTTPException(status_code=409, detail="Owners change through Hand over, so the environment is never left without one.")
    db.delete(g)
    db.commit()
    audit(db, p.username, "environment.unshare", object_type="environment", object_id=e.id,
          detail={"principal_type": g.principal_type, "principal": g.principal}, source_ip=client_ip(request))
    return {"ok": True}


@router.post("/{env_id}/transfer")
def transfer(env_id: int, body: TransferIn, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(current_principal)):
    """Make `username` the owner. The previous owners keep access as users unless `keep_access` is false."""
    e = _env(db, env_id, p)
    name = body.username.strip()
    target = db.scalar(select(User).where(User.username == name, User.provider != "breakglass"))
    if target is None:
        raise HTTPException(status_code=422, detail=f"No account named {name} has signed in to LABaPe yet.")
    if target.disabled_at is not None:
        raise HTTPException(status_code=422, detail=f"{name}'s account is disabled.")
    grants = list(db.scalars(select(Grant).where(Grant.object_type == "environment", Grant.object_id == env_id)))
    previous = [g.principal for g in grants if g.level == "owner" and g.principal_type == "user"]
    has_user_grant = {(g.principal_type, g.principal) for g in grants if g.level == "user"}
    for g in grants:
        if g.level == "owner":
            keep = body.keep_access and not (g.principal_type == "user" and g.principal == name)                 and (g.principal_type, g.principal) not in has_user_grant
            if keep:
                g.level = "user"
            else:
                db.delete(g)
    db.flush()
    mine = next((g for g in grants if g.principal_type == "user" and g.principal == name and g in db), None)
    if mine is not None:
        mine.level = "owner"
    else:
        db.add(Grant(object_type="environment", object_id=env_id, principal_type="user", principal=name, level="owner",
                     subject=account_subject(db, name)))
    db.commit()
    audit(db, p.username, "environment.transfer", object_type="environment", object_id=e.id,
          detail={"to": name, "from": previous, "keep_access": body.keep_access}, source_ip=client_ip(request))
    return {"ok": True}
