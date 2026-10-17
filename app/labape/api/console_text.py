"""Interactive text sessions for console tokens (design §59.4, batch X3): open a shell on a VM, a VM's
serial console, or (tokens allowed host consoles) a KVM host's shell; send input; read output by polling,
long poll or server-sent events; close. The session itself lives in `console_sessions`.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import appsettings, console_access, console_sessions, console_transcripts, remote
from ..db import get_db, session_factory
from ..engine import hostio, vmexec
from ..models import KvmHost, RemoteSession, utcnow
from ..security import Principal, audit, client_ip
from .console import _env_vm

router = APIRouter(prefix="/api/console", tags=["console"])


class SessionIn(BaseModel):
    environment_id: int | None = None
    vm: str = Field(default="", max_length=255)
    kvm_host_id: int | None = None                 # a KVM host's shell: only on a token allowed host consoles
    kind: Literal["shell", "serial"] = "shell"     # shell = ssh; serial = the VM's serial console through its KVM host
    account: str = Field(default="", max_length=255)


class InputIn(BaseModel):
    data: str = Field(min_length=1, max_length=console_sessions.MAX_INPUT)


def _finalize(s: console_sessions.TextSession) -> None:
    """Write a session's end: its row and audit event."""
    with session_factory()() as db:
        row = db.get(RemoteSession, s.id)
        if row is not None:
            row.state, row.ended_at = "closed", utcnow()
            if s.closed_reason not in ("closed", "closed by the caller"):
                row.error = s.closed_reason[:500]
            db.commit()
            audit(db, row.username, "console.session.close", object_type="remote_session", object_id=s.id,
                  detail={"target": row.target, "reason": s.closed_reason, "input_bytes": s.input_bytes,
                          "output_bytes": s.end})
    console_sessions.forget(s.id)


def _mine(db: Session, p: Principal, session_id: int) -> console_sessions.TextSession:
    row = db.get(RemoteSession, session_id)
    s = console_sessions.get(session_id)
    if row is None or row.protocol != "text" or row.username != p.username or s is None \
            or s.token_id != p.scope["token_id"]:
        raise HTTPException(404, "No such session")
    return s


@router.post("/sessions")
def open_text_session(body: SessionIn, request: Request, db: Session = Depends(get_db),
                      p: Principal = Depends(console_access.console_principal)):
    """Open an interactive text session. One per token per target."""
    console_access.rate_limit(p)
    lim = console_access.limits(p)
    tmp = console_sessions.make_tmp()
    try:
        if body.kvm_host_id is not None:
            console_access.check_host_consoles(p)
            h = db.get(KvmHost, body.kvm_host_id)
            if h is None:
                raise HTTPException(404, "No such host")
            try:
                params = remote.host_target(h)["params"]
            except Exception as exc:  # noqa: BLE001 — no access set up, no key
                raise HTTPException(400, str(exc)) from exc
            row = RemoteSession(username=p.username, target_type="host", kvm_host_id=h.id, target=h.name,
                                address=h.ssh_address or "", protocol="text", client_ip=client_ip(request))
            via, key, env_id, account = None, f"host:{h.id}", None, ""
        else:
            if body.environment_id is None or not body.vm:
                raise HTTPException(422, "Name an environment and a vm, or a kvm_host_id")
            env = _env_vm(db, p, body.environment_id, body.vm)
            account = console_access.check_account(p, body.account) if body.kind == "shell" else ""
            try:
                if body.kind == "serial":
                    params, via = remote.console_target(env, body.vm, "serial")["params"], None
                else:
                    pl = vmexec.plan(db, env, body.vm, account)
                    if pl.windows:
                        raise HTTPException(400, "Windows VMs have no text session: use the exec call (PowerShell)")
                    params, via = pl.target["params"], pl.via
            except (vmexec.ExecError, remote.RemoteError) as exc:
                raise HTTPException(400, str(exc)) from exc
            row = RemoteSession(username=p.username, target_type="vm", environment_id=env.id, target=body.vm,
                                address=params.get("hostname", ""), protocol="text", client_ip=client_ip(request),
                                account=account)
            key, env_id = f"{env.id}:{body.vm}:{body.kind}", env.id
        argv, extra, secrets = vmexec._ssh_argv(params, params.get("command", ""), tmp, via, tty=True)
        row.recorded = console_transcripts.enabled(db)
        row.state, row.started_at = "open", utcnow()
        db.add(row)
        db.commit()
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    sid, record = row.id, row.recorded

    def on_output(text: str) -> None:
        if record:
            console_transcripts.append(sid, vmexec.mask(text, secrets))

    if record:
        console_transcripts.append(sid, f"# {row.target} ({body.kind}), opened by {p.username}\n")
    try:
        s = console_sessions.open_session(
            sid, p.scope["token_id"], key, argv, {**hostio.tool_env(), **extra}, tmp, buffer_cap=lim["output_bytes"],
            seconds=lim["session_seconds"], idle_seconds=appsettings.get(db, "remote").idle_minutes * 60,
            on_output=on_output)
    except (console_sessions.SessionError, OSError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        row.state, row.error, row.ended_at = "failed", str(exc)[:500], utcnow()
        db.commit()
        raise HTTPException(409 if isinstance(exc, console_sessions.SessionError) else 502, str(exc)) from exc
    console_sessions.ensure_sweeper(_finalize)
    audit(db, p.username, "console.session.open", object_type="remote_session", object_id=sid,
          detail={"token": p.scope["name"], "target": row.target, "kind": body.kind, "account": account,
                  "recorded": row.recorded, "environment_id": env_id}, source_ip=client_ip(request))
    return {"id": sid, "target": row.target, "kind": body.kind, "recorded": row.recorded,
            "session_seconds": lim["session_seconds"], "output_buffer_bytes": lim["output_bytes"], "next": s.end}


@router.post("/sessions/{session_id}/input")
def send_input(session_id: int, body: InputIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(console_access.console_principal)):
    """Type `data` into the session (end a line with a newline; \\u0003 is Ctrl-C). The audit event holds only the
    size, since what is typed may be a password; the transcript (recording on) holds what came back."""
    console_access.rate_limit(p)
    s = _mine(db, p, session_id)
    try:
        n = s.write(body.data)
    except console_sessions.SessionError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, p.username, "console.session.input", object_type="remote_session", object_id=session_id,
          detail={"bytes": n}, source_ip=client_ip(request))
    return {"bytes": n, "next": s.end}


@router.get("/sessions/{session_id}/output")
def read_output(session_id: int, after: int = Query(default=0, ge=0), wait: float = Query(default=0, ge=0, le=30),
                db: Session = Depends(get_db), p: Principal = Depends(console_access.console_principal)):
    """Everything after offset `after`. With `wait`, holds the request up to that many seconds for output.
    `next` is the offset to ask for next; `lost` counts bytes that left the output buffer before they were read."""
    return _mine(db, p, session_id).read(after, wait)


@router.get("/sessions/{session_id}/stream")
async def stream_output(session_id: int, after: int = Query(default=0, ge=0), db: Session = Depends(get_db),
                        p: Principal = Depends(console_access.console_principal)):
    """The same output as server-sent events: each `data:` is {data, next, lost, open, reason}."""
    s = _mine(db, p, session_id)

    async def events():
        pos = after
        while True:
            r = await asyncio.to_thread(s.read, pos, 15)
            pos = r["next"]
            if r["data"] or not r["open"]:
                yield f"data: {json.dumps(r)}\n\n"
            else:
                yield ": keepalive\n\n"
            if not r["open"]:
                return

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.delete("/sessions/{session_id}")
def close_text_session(session_id: int, db: Session = Depends(get_db),
                       p: Principal = Depends(console_access.console_principal)):
    s = _mine(db, p, session_id)
    s.close("closed by the caller")
    if not s.finalized:
        s.finalized = True
        _finalize(s)
    return {"closed": True}


@router.get("/sessions")
def my_sessions(p: Principal = Depends(console_access.console_principal)):
    """This token's open sessions."""
    tid = p.scope["token_id"]
    return [{"id": s.id, "target": s.target, "next": s.end, "input_bytes": s.input_bytes}
            for s in list(console_sessions._sessions.values()) if s.token_id == tid and s.open]
