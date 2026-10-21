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
from ..models import ApiToken, Environment, User, utcnow
from ..permissions import is_owner
from ..security import Principal, audit, client_ip, current_principal

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="personal", pattern="^(personal|service)$")
    roles: list[str] = Field(default_factory=list)
    days: int = Field(default=90, ge=1, le=tokens.MAX_DAYS)
    # Console scope and limits (design §59); environments null = every one the owner can use.
    environments: list[int] | None = None
    host_consoles: bool = False
    require_account: bool = True
    rate_per_minute: int = Field(default=30, ge=1, le=600)
    max_session_minutes: int = Field(default=30, ge=1, le=480)
    max_output_kb: int = Field(default=256, ge=1, le=4096)


@router.get("")
def list_tokens(all: bool = False, inactive: bool = False, db: Session = Depends(get_db),
                p: Principal = Depends(current_principal)):
    """`inactive` adds the revoked and expired tokens, which are otherwise hidden (and removed after the retention
    period in Settings → History)."""
    q = select(ApiToken, User.username).join(User, User.id == ApiToken.user_id).order_by(ApiToken.created_at.desc())
    if not inactive:
        q = q.where(ApiToken.revoked_at.is_(None), ApiToken.expires_at > utcnow())
    if not (all and p.is_admin):
        q = q.where(ApiToken.user_id == p.user_id, ApiToken.kind == "personal")
    return {"roles": list(ROLES), "my_roles": sorted(tokens.held_roles(p.roles)),
            "tokens": [tokens.out(t, owner) for t, owner in db.execute(q).all()]}


@router.post("")
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    if p.breakglass or p.via_token:
        raise HTTPException(403, "Make API tokens from a normal sign-in")
    if body.kind == "service" and not p.is_admin:
        raise HTTPException(403, "Only admins make service tokens")
    if body.kind == "personal" and not set(body.roles) <= tokens.held_roles(p.roles):
        raise HTTPException(400, "A personal token can only have roles you have")
    if body.host_consoles and not p.is_admin:
        raise HTTPException(403, "Only admins make a token that can open KVM host consoles")
    if body.environments is not None:
        envs = sorted(set(body.environments))
        found = {e.id for e in db.scalars(select(Environment).where(Environment.id.in_(envs)))} if envs else set()
        if found != set(envs):
            raise HTTPException(400, f"No such environment: {sorted(set(envs) - found)}")
        if not p.is_admin and not all(is_owner(db, p, "environment", i) for i in envs):
            raise HTTPException(403, "A token can only reach environments you own")
        body.environments = envs
    scope = body.model_dump(include={"environments", "host_consoles", "require_account", "rate_per_minute",
                                     "max_session_minutes", "max_output_kb"})
    try:
        t, secret = tokens.create(db, db.get(User, p.user_id), body.name, body.kind, body.roles, body.days, scope)
    except tokens.TokenError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(db, p.username, "token.create", object_type="api_token", object_id=t.id,
          detail={"name": t.name, "kind": t.kind, "roles": t.roles, "expires_at": t.expires_at.isoformat(),
                  **{k: v for k, v in tokens.scope(t).items() if k not in ("token_id", "name", "owner_admin")}},
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
