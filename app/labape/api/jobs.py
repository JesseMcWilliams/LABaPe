"""Jobs: list, detail, cancel, retry, live log (Server-Sent Events)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db, session_factory
from ..models import Environment, Job
from ..permissions import can_see, is_owner, visible_ids
from ..security import Principal, audit, client_ip, current_principal

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

FINISHED = ("succeeded", "failed", "cancelled")


def _out(j: Job, env: Environment | None = None) -> dict:
    out = {c: getattr(j, c) for c in ("id", "type", "params", "state", "kvm_host_id", "environment_id",
                                      "requested_by", "cancel_requested", "retry_of", "exit_code",
                                      "created_at", "started_at", "finished_at")}
    # Environments are shown by name (design §28, O1); a destroyed one keeps its row and name.
    out["environment_name"] = env.name if env else None
    out["environment_status"] = env.status if env else None
    return out


def _visible(db: Session, j: Job, p: Principal) -> bool:
    if p.is_admin or j.requested_by == p.username:
        return True
    return j.environment_id is not None and can_see(db, p, "environment", j.environment_id)


def _get(db: Session, job_id: int, p: Principal) -> Job:
    j = db.get(Job, job_id)
    if j is None or not _visible(db, j, p):
        raise HTTPException(status_code=404, detail="No such job")
    return j


def _visible_query(db: Session, p: Principal):
    """Jobs this user may see: all for admins; otherwise their own and those of the
    environments they can see."""
    q = select(Job, Environment).outerjoin(Environment, Job.environment_id == Environment.id)
    ids = visible_ids(db, p, "environment")
    if ids is not None:
        q = q.where(or_(Job.requested_by == p.username, Job.environment_id.in_(ids or {-1})))
    return q


@router.get("")
def list_jobs(limit: int = 100, environment: str | None = None, type: str | None = None,
              state: str | None = None, requested_by: str | None = None, since: dt.datetime | None = None,
              until: dt.datetime | None = None, db: Session = Depends(get_db),
              p: Principal = Depends(current_principal)):
    """Filters (O1): environment name (contains, case-insensitive), type (prefix), state,
    requested_by (exact), created since/until. Visibility is applied first, so a filter
    can never reveal another user's jobs."""
    q = _visible_query(db, p)
    if environment:
        q = q.where(Environment.name.ilike(f"%{environment}%"))
    if type:
        q = q.where(Job.type.startswith(type))
    if state:
        q = q.where(Job.state == state)
    if requested_by:
        q = q.where(Job.requested_by == requested_by)
    if since:
        q = q.where(Job.created_at >= since)
    if until:
        q = q.where(Job.created_at <= until)
    q = q.order_by(Job.id.desc()).limit(min(max(limit, 1), 1000))
    return [_out(j, env) for j, env in db.execute(q)]


@router.get("/facets")
def job_facets(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """Values for the Jobs page's filter lists, from the jobs this user can see."""
    rows = db.execute(_visible_query(db, p).order_by(Job.id.desc()).limit(5000)).all()
    return {"types": sorted({j.type for j, _ in rows}),
            "states": sorted({j.state for j, _ in rows}),
            "requested_by": sorted({j.requested_by for j, _ in rows if j.requested_by}),
            "environments": sorted({e.name for _, e in rows if e is not None})}


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    j = _get(db, job_id, p)
    return _out(j, db.get(Environment, j.environment_id) if j.environment_id else None)


def _may_control(db: Session, j: Job, p: Principal) -> bool:
    if p.is_admin or j.requested_by == p.username:
        return True
    return j.environment_id is not None and is_owner(db, p, "environment", j.environment_id)


@router.post("/{job_id}/cancel")
def cancel(job_id: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    j = _get(db, job_id, p)
    if not _may_control(db, j, p):
        raise HTTPException(status_code=403, detail="Not permitted")
    if j.state in FINISHED:
        raise HTTPException(status_code=409, detail=f"Job already {j.state}")
    if j.state == "queued":
        j.state = "cancelled"
    j.cancel_requested = True
    db.commit()
    audit(db, p.username, "job.cancel", object_type="job", object_id=j.id, source_ip=client_ip(request))
    return _out(j, db.get(Environment, j.environment_id) if j.environment_id else None)


@router.post("/{job_id}/retry")
def retry(job_id: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    j = _get(db, job_id, p)
    if not _may_control(db, j, p):
        raise HTTPException(status_code=403, detail="Not permitted")
    if j.state not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
    busy = db.scalar(select(Job).where(Job.environment_id == j.environment_id, Job.state.in_(("queued", "running"))))
    if j.environment_id is not None and busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    new = Job(type=j.type, params=j.params, kvm_host_id=j.kvm_host_id, environment_id=j.environment_id,
              requested_by=p.username, retry_of=j.id)
    db.add(new)
    db.commit()
    audit(db, p.username, "job.retry", object_type="job", object_id=new.id, detail={"retry_of": j.id},
          source_ip=client_ip(request))
    return _out(new, db.get(Environment, new.environment_id) if new.environment_id else None)


@router.get("/{job_id}/log")
async def log_stream(job_id: int, request: Request, offset: int = 0, db: Session = Depends(get_db),
                     p: Principal = Depends(current_principal)):
    """Server-Sent Events: `log` events carry {offset, text}; `end` carries the final state."""
    j = _get(db, job_id, p)
    log_path = Path(j.log_path) if j.log_path else None

    async def events():
        pos = max(offset, 0)
        while True:
            if await request.is_disconnected():
                return
            path = log_path
            state = None
            with session_factory()() as s:
                row = s.get(Job, job_id)
                if row is not None:
                    state = row.state
                    path = Path(row.log_path) if row.log_path else path
            if path and path.exists():
                with path.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read(256 * 1024)
                if chunk:
                    pos += len(chunk)
                    payload = json.dumps({"offset": pos, "text": chunk.decode("utf-8", "replace")})
                    yield f"event: log\ndata: {payload}\n\n"
                    continue
            if state in FINISHED:
                yield f"event: end\ndata: {json.dumps({'state': state, 'offset': pos})}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
