"""Browser remote access (design §28.5, questions 116-118): sessions, the Guacamole tunnel,
the session log, recordings and .rdp files."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, remote
from ..config import get_settings
from ..db import get_db, session_factory
from ..errors import FieldError
from ..models import Environment, KvmHost, RemoteSession, User, utcnow
from ..permissions import can_see, is_owner
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/remote", tags=["remote"])
RECORDINGS = Path("/var/lib/labape-recordings")           # guacd's recordings volume, read-only here


class SessionIn(BaseModel):
    target_type: Literal["vm", "host"]
    protocol: Literal["ssh", "rdp", "vnc", "serial"]
    environment_id: int | None = None
    vm: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,63}$")
    kvm_host_id: int | None = None
    account: str = Field(default="", max_length=255, pattern=r"^[A-Za-z0-9._/-]*$")   # E2: as this account


def _out(s: RemoteSession) -> dict:
    return {c: getattr(s, c) for c in ("id", "username", "target_type", "environment_id", "kvm_host_id", "target",
                                       "address", "protocol", "state", "recorded", "error", "client_ip", "account",
                                       "created_at", "started_at", "ended_at")}


@router.post("/sessions")
def create_session(body: SessionIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(current_principal)):
    """A one-time token for the tunnel, after checking who may open what (question 118):
    an environment's VMs for its owners and admins; KVM hosts for admins."""
    if body.target_type == "vm":
        env = db.get(Environment, body.environment_id or 0)
        if env is None or not can_see(db, p, "environment", env.id):
            raise HTTPException(status_code=404, detail="No such environment")
        if not is_owner(db, p, "environment", env.id):
            raise HTTPException(status_code=403, detail="Only the environment's owners and admins can open sessions")
        name = body.vm or ""
        s = RemoteSession(username=p.username, target_type="vm", environment_id=env.id, target=name,
                          protocol=body.protocol, client_ip=client_ip(request), account=body.account)
    else:
        if not p.is_admin:
            raise HTTPException(status_code=403, detail="Only admins can open sessions on KVM hosts")
        h = db.get(KvmHost, body.kvm_host_id or 0)
        if h is None:
            raise HTTPException(status_code=404, detail="No such host")
        s = RemoteSession(username=p.username, target_type="host", kvm_host_id=h.id, target=h.name,
                          protocol=body.protocol, client_ip=client_ip(request))
    db.add(s)
    db.flush()
    try:
        t = remote.resolve(db, s)                 # checks the target now, so errors show before the page opens
    except (remote.RemoteError, Exception) as exc:  # noqa: BLE001 — missing secrets, keys, addresses
        db.rollback()
        raise FieldError("target", str(exc)) from exc
    token, s.token_hash = remote.new_token()
    s.address, s.recorded = t["address"], "recording-path" in t["params"]
    db.commit()
    audit(db, p.username, "remote.session.create", object_type="remote_session", object_id=s.id,
          detail={"target": s.target, "type": s.target_type, "protocol": s.protocol, "recorded": s.recorded,
                  "account": s.account},
          source_ip=client_ip(request))
    return {"id": s.id, "token": token, "protocol": s.protocol, "target": s.target, "recorded": s.recorded}


def _ws_user(ws: WebSocket, db: Session) -> User | None:
    sess = ws.session if "session" in ws.scope else {}
    uid, exp = sess.get("uid"), sess.get("exp", 0)
    if not uid or time.time() > exp:
        return None
    return db.get(User, uid)


@router.websocket("/tunnel")
async def tunnel(ws: WebSocket, token: str = "", width: int = 1280, height: int = 800, dpi: int = 96):
    """The Guacamole WebSocket tunnel. The session cookie must belong to the user the token was
    issued to, the page must come from LABaPe's own origin, and the token works once, within
    two minutes."""
    public = urlsplit(get_settings().public_url)
    if ws.headers.get("origin") != f"{public.scheme}://{public.netloc}":
        await ws.close(code=4403)
        return
    with session_factory()() as db:
        s = db.scalar(select(RemoteSession).where(RemoteSession.token_hash == remote.token_hash(token),
                                                  RemoteSession.state == "pending")) if token else None
        user = _ws_user(ws, db)
        age = (utcnow() - (s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=dt.timezone.utc))
               ).total_seconds() if s else None
        if s is None or user is None or user.username != s.username or age is None or age > remote.TOKEN_SECONDS:
            await ws.close(code=4401)
            return
        s.token_hash, s.state, s.started_at = "", "open", utcnow()
        db.commit()
        try:
            target = remote.resolve(db, s)
        except Exception as exc:  # noqa: BLE001
            s.state, s.error, s.ended_at = "failed", str(exc)[:500], utcnow()
            db.commit()
            await ws.accept(subprotocol="guacamole")
            await ws.send_text(remote.encode("error", str(exc), "519"))
            await ws.close()
            return
        idle = appsettings.get(db, "remote").idle_minutes * 60
        sid, uname, ip = s.id, s.username, s.client_ip
    await ws.accept(subprotocol="guacamole")
    with session_factory()() as db:
        audit(db, uname, "remote.session.start", object_type="remote_session", object_id=sid,
              detail={"target": target["address"], "protocol": target["protocol"]}, source_ip=ip)
    why, forward = "ended", None
    try:
        if target.get("forward"):                   # a VM's VNC console: through the API to its host
            forward, port = await remote.start_forward(target["forward"])
            target["params"].update({"hostname": remote.API_HOST, "port": str(port)})
        reader, writer, leftover = await remote.open_guacd(target, max(width, 320), max(height, 240), dpi)
        why = await remote.relay(ws, reader, writer, leftover, idle)
    except remote.RemoteError as exc:
        why = f"failed: {exc}"
        try:
            await ws.send_text(remote.encode("error", str(exc), "519"))
        except Exception:  # noqa: BLE001
            pass
    except (WebSocketDisconnect, OSError, TimeoutError) as exc:
        why = f"ended: {type(exc).__name__}"
    finally:
        if forward is not None:
            forward.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        with session_factory()() as db:
            s = db.get(RemoteSession, sid)
            s.state = "failed" if why.startswith("failed") else "closed"
            s.ended_at, s.error = utcnow(), why[:500]
            db.commit()
            audit(db, uname, "remote.session.end", object_type="remote_session", object_id=sid,
                  detail={"why": why[:200]}, source_ip=ip)


@router.get("/sessions")
def list_sessions(limit: int = 200, username: str | None = None, target: str | None = None,
                  since: dt.datetime | None = None, until: dt.datetime | None = None,
                  db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    """The session log (auditors and admins; question 118)."""
    q = select(RemoteSession).order_by(RemoteSession.id.desc())
    if username:
        q = q.where(RemoteSession.username == username)
    if target:
        q = q.where(RemoteSession.target.ilike(f"%{target}%"))
    if since:
        q = q.where(RemoteSession.created_at >= since)
    if until:
        q = q.where(RemoteSession.created_at <= until)
    rows = [_out(s) for s in db.scalars(q.limit(min(limit, 2000)))]
    for r in rows:
        r["has_recording"] = (RECORDINGS / f"session-{r['id']}").is_file()
    return rows


@router.get("/sessions/{session_id}/recording")
def recording(session_id: int, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("auditor"))):
    s = db.get(RemoteSession, session_id)
    path = RECORDINGS / f"session-{session_id}"
    if s is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No recording for that session")
    audit(db, p.username, "remote.recording.view", object_type="remote_session", object_id=s.id,
          detail={"target": s.target, "user": s.username}, source_ip=client_ip(request))
    return FileResponse(path, media_type="application/octet-stream", filename=f"labape-session-{session_id}.guac")


@router.get("/rdp")
def rdp(environment_id: int, vm: str, request: Request, db: Session = Depends(get_db),
        p: Principal = Depends(current_principal)):
    """A .rdp file for a native RDP client on the lab network (question 116). It names the
    address and user, never the password."""
    env = db.get(Environment, environment_id)
    if env is None or not can_see(db, p, "environment", env.id):
        raise HTTPException(status_code=404, detail="No such environment")
    if not is_owner(db, p, "environment", env.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners and admins")
    try:
        t = remote.vm_target(db, env, vm)
    except remote.RemoteError as exc:
        raise FieldError("vm", str(exc)) from exc
    if t["protocol"] != "rdp":
        raise FieldError("vm", f"{vm} is a Linux VM: use SSH")
    audit(db, p.username, "remote.rdp_file", object_type="environment", object_id=env.id, detail={"vm": vm},
          source_ip=client_ip(request))
    body = remote.rdp_file(t["address"], t["params"]["username"], t["params"]["domain"])
    return Response(body, media_type="application/x-rdp",
                    headers={"Content-Disposition": f'attachment; filename="{vm}.rdp"'})
