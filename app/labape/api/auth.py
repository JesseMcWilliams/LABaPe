"""Sign-in, sign-out and the current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import load_providers, provider_enabled
from ..auth import breakglass
from ..config import get_settings
from ..db import get_db
from ..models import User, utcnow
from ..security import Principal, audit, client_ip, current_principal, start_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/providers")
def providers(db: Session = Depends(get_db)):
    """Sign-in options for the login page (break-glass is never listed)."""
    out = []
    for name, p in load_providers().items():
        if p.kind == "token" or not provider_enabled(db, name):
            continue
        out.append({"name": name, "kind": p.kind, "display_name": p.display_name})
    return out


@router.get("/{provider}/login")
async def login(provider: str, request: Request, db: Session = Depends(get_db)):
    p = load_providers().get(provider)
    if p is None or p.kind != "redirect" or not provider_enabled(db, provider):
        raise HTTPException(status_code=404, detail="Unknown sign-in provider")
    return await p.begin_login(request)


@router.get("/{provider}/callback")
async def callback(provider: str, request: Request, db: Session = Depends(get_db)):
    p = load_providers().get(provider)
    if p is None or p.kind != "redirect" or not provider_enabled(db, provider):
        raise HTTPException(status_code=404, detail="Unknown sign-in provider")
    ident = await p.complete_login(request)
    user = db.scalar(select(User).where(User.provider == provider, User.subject == ident.subject))
    if user is None:
        user = User(provider=provider, subject=ident.subject, username=ident.username)
        db.add(user)
    user.username = ident.username
    user.email = ident.email
    user.display_name = ident.display_name
    user.groups = ident.groups
    user.last_login = utcnow()
    db.commit()
    start_session(request, user)
    audit(db, user.username, "auth.login", detail={"provider": provider, "groups": ident.groups},
          source_ip=client_ip(request))
    return RedirectResponse(url="/", status_code=303)


class BreakglassIn(BaseModel):
    token: str


@router.post("/breakglass")
def breakglass_login(body: BreakglassIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    local = request.headers.get("x-labape-local") == "1"
    user, reason = breakglass.redeem(db, body.token.strip(), local)
    if user is None:
        audit(db, "breakglass", "auth.breakglass", outcome="denied", detail={"reason": reason}, source_ip=ip)
        raise HTTPException(status_code=401, detail=f"Break-glass sign-in refused: {reason}")
    start_session(request, user, breakglass=True, minutes=get_settings().breakglass_session_minutes)
    audit(db, "breakglass", "auth.breakglass", source_ip=ip)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    return {
        "username": p.username,
        "display_name": p.display_name,
        "groups": p.groups,
        "roles": sorted(p.roles),
        "breakglass": p.breakglass,
    }


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    request.session.clear()
    if uid:
        user = db.get(User, uid)
        audit(db, user.username if user else str(uid), "auth.logout", source_ip=client_ip(request))
    return {"ok": True}
