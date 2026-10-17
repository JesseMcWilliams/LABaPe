"""Console access for other AIs and scripts (design §59, batches X2 and X3): run a command on a VM and
get the result back. Bearer token with the console role only; the token's scope and limits (X1) apply.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import console_access, console_transcripts
from ..db import get_db
from ..engine import vmexec
from ..models import Environment, KvmHost, RemoteSession, utcnow
from ..naming import instance_names
from ..security import Principal, audit, client_ip

router = APIRouter(prefix="/api", tags=["console"])

COMMAND_AUDIT_CHARS = 2000


class ExecIn(BaseModel):
    command: str = Field(min_length=1, max_length=20000)
    timeout: int = Field(default=60, ge=1, le=3600)
    account: str = Field(default="", max_length=255)


def _env_vm(db: Session, p: Principal, env_id: int, vm: str) -> Environment:
    console_access.rate_limit(p)
    console_access.check_environment(db, p, env_id)
    env = db.get(Environment, env_id)
    if env is None:
        raise HTTPException(404, "No such environment")
    names = {n for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}
    if vm not in names:
        raise HTTPException(404, f"{vm} isn't a VM of {env.name}")
    return env


@router.post("/environments/{env_id}/vms/{vm}/exec")
def exec_command(env_id: int, vm: str, body: ExecIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(console_access.console_principal)):
    """Run one command (a shell command on Linux, PowerShell on Windows) and return its exit code and output.
    Output past the token's limit stops the command (`truncated`); a timeout kills it (`timed_out`)."""
    env = _env_vm(db, p, env_id, vm)
    account = console_access.check_account(p, body.account)
    lim = console_access.limits(p)
    s = RemoteSession(username=p.username, target_type="vm", environment_id=env.id, target=vm, protocol="exec",
                      client_ip=client_ip(request), account=account, state="open", started_at=utcnow())
    db.add(s)
    db.commit()
    detail = {"token": p.scope["name"], "vm": vm, "account": account, "session": s.id,
              "command": body.command[:COMMAND_AUDIT_CHARS]}
    try:
        r = vmexec.run(db, env, vm, body.command, account=account, timeout=min(body.timeout, lim["session_seconds"]),
                       cap=lim["output_bytes"])
    except vmexec.ExecError as exc:
        s.state, s.error, s.ended_at = "failed", str(exc)[:500], utcnow()
        db.commit()
        audit(db, p.username, "console.exec", object_type="environment", object_id=env.id, outcome="failed",
              detail={**detail, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise HTTPException(400, str(exc)) from exc
    s.state, s.ended_at = "closed", utcnow()
    if r.timed_out:
        s.error = "timed out"
    elif r.truncated:
        s.error = "output limit reached"
    if console_transcripts.enabled(db):
        s.recorded = console_transcripts.append(s.id, f"$ {body.command}\n{r.stdout}"
                                                f"{'' if r.stdout.endswith(chr(10)) or not r.stdout else chr(10)}"
                                                f"{r.stderr}[exit {r.exit_code}{', timed out' if r.timed_out else ''}"
                                                f"{', output limit reached' if r.truncated else ''}]\n")
    db.commit()
    audit(db, p.username, "console.exec", object_type="environment", object_id=env.id,
          detail={**detail, "exit_code": r.exit_code, "stdout_bytes": len(r.stdout.encode()),
                  "stderr_bytes": len(r.stderr.encode()), "truncated": r.truncated, "timed_out": r.timed_out,
                  "duration": round(r.duration, 3)}, source_ip=client_ip(request))
    return {**r.as_dict(), "session": s.id}


@router.get("/console/environments")
def console_targets(db: Session = Depends(get_db), p: Principal = Depends(console_access.console_principal)):
    """The environments and VMs this token can open consoles on, and whether it may open KVM host consoles."""
    out = []
    for e in db.scalars(select(Environment).order_by(Environment.name)):
        if console_access.environment_allowed(db, p, e.id):
            vms = [{"name": n, "os": str(g.get("os", ""))} for g in (e.spec or {}).get("host_groups", [])
                   for n in instance_names(g)]
            out.append({"id": e.id, "name": e.name, "status": e.status, "vms": vms})
    hosts = []
    if p.scope["host_consoles"] and p.is_admin:
        hosts = [{"id": h.id, "name": h.name, "ready": h.access_status == "ready"}
                 for h in db.scalars(select(KvmHost).order_by(KvmHost.name)) if not h.deleted_at]
    return {"environments": out, "kvm_hosts": hosts, "account_required": p.scope["require_account"]}
