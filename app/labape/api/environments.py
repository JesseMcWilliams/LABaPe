"""Environments: create (deploys), list, view, redeploy, destroy, credentials.

Phase 10a takes the host groups directly (the tfvars-level form); the
environment-template editor replaces this in phase 10c.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Environment, Job, KvmHost
from ..permissions import add_owner, can_see, is_owner, visible_ids
from ..security import Principal, audit, client_ip, current_principal, get_secret, require_roles

router = APIRouter(prefix="/api/environments", tags=["environments"])

ROLE_NAMES = Literal["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"]


class HostGroup(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9]{0,11}$")
    count: int = Field(default=1, ge=1, le=20)
    os: str = Field(pattern=r"^[a-z0-9_]+$")
    roles: list[ROLE_NAMES] = Field(min_length=1)
    image_source: Literal["iso_direct", "packer_template"] | None = None
    template: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+$")
    cpu_count: int = Field(default=2, ge=1, le=32)
    memory_mb: int = Field(default=4096, ge=512, le=262144)
    disk_gb: int = Field(default=40, ge=10, le=2048)
    windows_core: bool = False


class EnvironmentIn(BaseModel):
    # Doubles as the OpenTofu workspace and VM tag; "test-" names belong to the CLI's test mode.
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,39}$")
    kvm_host_id: int
    static_ip_offset_start: int = Field(ge=2, le=4000)
    host_groups: list[HostGroup] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def _not_test(cls, v: str) -> str:
        # pydantic's regex engine has no look-ahead, so this can't live in the pattern.
        if v.startswith("test-"):
            raise ValueError('names starting with "test-" are reserved for the CLI test mode')
        return v


def _out(e: Environment, owner: bool) -> dict:
    return {
        "id": e.id, "name": e.name, "status": e.status, "kvm_host": e.kvm_host.name if e.kvm_host else None,
        "spec": e.spec, "created_by": e.created_by, "created_at": e.created_at, "updated_at": e.updated_at,
        "is_owner": owner, "inventory": e.inventory if owner else "",
    }


def _queue(db: Session, e: Environment, job_type: str, p: Principal) -> Job:
    busy = db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    job = Job(type=job_type, params={"environment_id": e.id}, kvm_host_id=e.kvm_host_id, environment_id=e.id,
              requested_by=p.username)
    db.add(job)
    e.status = "deploying" if job_type == "environment.deploy" else "destroying"
    db.commit()
    return job


@router.get("")
def list_envs(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ids = visible_ids(db, p, "environment")
    q = select(Environment).order_by(Environment.name)
    if ids is not None:
        q = q.where(Environment.id.in_(ids or {-1}))
    return [_out(e, is_owner(db, p, "environment", e.id)) for e in db.scalars(q)]


@router.post("", status_code=201)
def create_env(body: EnvironmentIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("deployer", "template_editor"))):
    host = db.get(KvmHost, body.kvm_host_id)
    if host is None or not host.enabled:
        raise HTTPException(status_code=422, detail="Unknown or disabled KVM host")
    if db.scalar(select(Environment).where(Environment.name == body.name)):
        raise HTTPException(status_code=409, detail="An environment with that name exists")
    names = [g.name for g in body.host_groups]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="Host group names must be unique")
    for g in body.host_groups:
        if g.image_source == "packer_template" and not g.template:
            raise HTTPException(status_code=422, detail=f"Host group {g.name}: packer_template needs a template")
    spec = {"static_ip_offset_start": body.static_ip_offset_start,
            "host_groups": [g.model_dump(exclude_none=True) for g in body.host_groups]}
    e = Environment(name=body.name, kvm_host_id=host.id, spec=spec, created_by=p.username)
    db.add(e)
    db.commit()
    add_owner(db, "environment", e.id, p.username)
    job = _queue(db, e, "environment.deploy", p)
    audit(db, p.username, "environment.create", object_type="environment", object_id=e.id,
          detail={"spec": spec, "job": job.id}, source_ip=client_ip(request))
    return {"environment": _out(e, True), "job_id": job.id}


def _get(db: Session, env_id: int, p: Principal) -> Environment:
    e = db.get(Environment, env_id)
    if e is None or not can_see(db, p, "environment", env_id):
        raise HTTPException(status_code=404, detail="No such environment")
    return e


@router.get("/{env_id}")
def get_env(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _out(e, is_owner(db, p, "environment", e.id))


@router.post("/{env_id}/deploy")
def redeploy(env_id: int, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can deploy it")
    job = _queue(db, e, "environment.deploy", p)
    audit(db, p.username, "environment.deploy", object_type="environment", object_id=e.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}


@router.post("/{env_id}/destroy")
def destroy(env_id: int, request: Request, db: Session = Depends(get_db),
            p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can destroy it")
    job = _queue(db, e, "environment.destroy", p)
    audit(db, p.username, "environment.destroy", object_type="environment", object_id=e.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}


@router.get("/{env_id}/credentials")
def credentials(env_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Credentials are visible to owners and admins only")
    audit(db, p.username, "environment.credentials.view", object_type="environment", object_id=e.id,
          source_ip=client_ip(request))
    return {"credentials": get_secret(db, f"environment/{e.id}/credentials") or ""}
