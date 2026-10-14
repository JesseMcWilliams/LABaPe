"""API tokens (design §37): every user manages their own personal tokens; admins make service
tokens and see and revoke everyone's."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import tokens
from ..config import ROLES
from ..db import get_db
from ..models import ApiToken, User
from ..security import Principal, audit, client_ip, current_principal

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="personal", pattern="^(personal|service)$")
    roles: list[str] = Field(default_factory=list)
    days: int = Field(default=90, ge=1, le=tokens.MAX_DAYS)


@router.get("")
def list_tokens(all: bool = False, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    q = select(ApiToken, User.username).join(User, User.id == ApiToken.user_id).order_by(ApiToken.created_at.desc())
    if not (all and p.is_admin):
        q = q.where(ApiToken.user_id == p.user_id, ApiToken.kind == "personal")
    return {"roles": list(ROLES), "my_roles": sorted(p.roles),
            "tokens": [tokens.out(t, owner) for t, owner in db.execute(q).all()]}


@router.post("")
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    if p.breakglass or p.via_token:
        raise HTTPException(403, "Make API tokens from a normal sign-in")
    if body.kind == "service" and not p.is_admin:
        raise HTTPException(403, "Only admins make service tokens")
    if body.kind == "personal" and not set(body.roles) <= p.roles:
        raise HTTPException(400, "A personal token can only have roles you have")
    try:
        t, secret = tokens.create(db, db.get(User, p.user_id), body.name, body.kind, body.roles, body.days)
    except tokens.TokenError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(db, p.username, "token.create", object_type="api_token", object_id=t.id,
          detail={"name": t.name, "kind": t.kind, "roles": t.roles, "expires_at": t.expires_at.isoformat()},
          source_ip=client_ip(request))
    return {**tokens.out(t, p.username), "secret": secret}


@router.delete("/{token_id}")
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    t = db.get(ApiToken, token_id)
    if t is None or not (p.is_admin or (t.user_id == p.user_id and t.kind == "personal")):
        raise HTTPException(404, "No such token")
    tokens.revoke(db, t, p.username)
    audit(db, p.username, "token.revoke", object_type="api_token", object_id=t.id, detail={"name": t.name},
          source_ip=client_ip(request))
    return tokens.out(t)
