"""Catalog → Updates (U1, design §50.7): the update sources, for admins."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..db import get_db
from ..engine import package_updates as pu
from ..errors import FieldError
from ..models import Job, UpdateSource
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/package-updates", tags=["package updates"])


class SourceIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    display_name: str = Field(default="", max_length=255)
    kind: str
    params: dict[str, str] = Field(default={})
    targets: list[str] = Field(default=["windows"], max_length=20)
    keep: int = Field(default=3, ge=1, le=20)
    enabled: bool = True


@router.get("")
def list_sources(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    running = db.scalar(select(Job.id).where(Job.type == "files.update_check", Job.state.in_(("queued", "running"))))
    return {"sources": [pu.to_dict(s) for s in db.scalars(select(UpdateSource).order_by(UpdateSource.name))],
            "schedule": appsettings.get(db, "package_updates", cached=True).model_dump(), "kinds": list(pu.KINDS),
            "running_job": running}


def _apply(src: UpdateSource, body: SourceIn) -> None:
    try:
        params = pu.validate(body.kind, body.params)
    except pu.UpdateError as exc:
        raise FieldError("params", str(exc)) from exc
    src.name, src.display_name, src.kind, src.params = body.name, body.display_name, body.kind, params
    src.targets, src.keep, src.enabled = body.targets, body.keep, body.enabled


@router.post("", status_code=201)
def create_source(body: SourceIn, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("admin"))):
    if db.scalar(select(UpdateSource).where(UpdateSource.name == body.name)):
        raise FieldError("name", "There's a source of that name already.")
    src = UpdateSource(history=[])
    _apply(src, body)
    db.add(src)
    db.commit()
    audit(db, p.username, "package_update.create", object_type="update_source", object_id=src.id,
          detail=body.model_dump(), source_ip=client_ip(request))
    return pu.to_dict(src)


@router.put("/{source_id}")
def update_source(source_id: int, body: SourceIn, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("admin"))):
    src = db.get(UpdateSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="No such source")
    if body.name != src.name and db.scalar(select(UpdateSource).where(UpdateSource.name == body.name)):
        raise FieldError("name", "There's a source of that name already.")
    _apply(src, body)
    db.commit()
    audit(db, p.username, "package_update.update", object_type="update_source", object_id=src.id,
          detail=body.model_dump(), source_ip=client_ip(request))
    return pu.to_dict(src)


@router.delete("/{source_id}")
def delete_source(source_id: int, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("admin"))):
    """The source goes; the files it downloaded stay in Files → Installers."""
    src = db.get(UpdateSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="No such source")
    db.delete(src)
    db.commit()
    audit(db, p.username, "package_update.delete", object_type="update_source", object_id=source_id,
          detail={"name": src.name}, source_ip=client_ip(request))
    return {"deleted": source_id}


class CheckIn(BaseModel):
    source_id: int | None = None


@router.post("/check")
def check_now(body: CheckIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    """Check every enabled source now (or one), as a job."""
    if db.scalar(select(Job.id).where(Job.type == "files.update_check", Job.state.in_(("queued", "running")))):
        raise HTTPException(status_code=409, detail="A check is already running")
    job = Job(type="files.update_check", params={"source_id": body.source_id} if body.source_id else {},
              requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "package_update.check", object_type="job", object_id=job.id,
          detail={"source_id": body.source_id}, source_ip=client_ip(request))
    return {"job_id": job.id}
