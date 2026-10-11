"""Template library (design §21): list for the deploy form, edit metadata."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import FieldError
from ..models import Environment, Job, KvmHost, Template
from ..naming import instance_names
from ..permissions import is_owner
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/templates", tags=["templates"])

FIELDS = ("id", "kvm_host_id", "name", "os_key", "edition", "disk_gb", "min_cpu", "min_memory_mb",
          "description", "notes", "source", "present", "updated_at")


def _out(t: Template) -> dict:
    return {f: getattr(t, f) for f in FIELDS}


@router.get("")
def list_templates(kvm_host_id: int | None = None, os: str | None = None, db: Session = Depends(get_db),
                   _: Principal = Depends(current_principal)):
    q = select(Template).where(Template.present.is_(True)).order_by(Template.name)
    if kvm_host_id is not None:
        q = q.where(Template.kvm_host_id == kvm_host_id)
    if os:
        q = q.where(Template.os_key == os)
    return [_out(t) for t in db.scalars(q)]


class TemplateIn(BaseModel):
    description: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=8000)
    os_key: str = Field(default="", pattern=r"^[a-z0-9_]*$")
    edition: str = Field(default="", pattern=r"^(|core|desktop)$")
    min_cpu: int = Field(default=1, ge=1, le=32)
    min_memory_mb: int = Field(default=1024, ge=256, le=262144)


NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$"
OS_KEYS = ("rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest", "windows_server_2019", "windows_server_2022",
           "windows_server_2025", "windows_11", "windows_10")


def _name_free(db: Session, host_id: int, name: str, field: str) -> None:
    if db.scalar(select(Template).where(Template.kvm_host_id == host_id, Template.name == name,
                                        Template.present.is_(True))):
        raise FieldError(field, f"A template named {name} already exists on this host; templates are never "
                                "overwritten. Choose a new name, e.g. with a version suffix.", status_code=409)
    busy = db.scalars(select(Job).where(Job.type.startswith("template."), Job.state.in_(("queued", "running"))))
    for j in busy:
        if name in ((j.params or {}).get("name"), (j.params or {}).get("new_name")):
            raise FieldError(field, f"Job {j.id} is already making a template named {name}.", status_code=409)


def _job(db: Session, p: Principal, request: Request, host_id: int, job_type: str, params: dict,
         env_id: int | None = None) -> dict:
    job = Job(type=job_type, params=params, kvm_host_id=host_id, environment_id=env_id, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, job_type, object_type="job", object_id=job.id, detail=params, source_ip=client_ip(request))
    return {"job_id": job.id}


class BuildIn(BaseModel):
    kvm_host_id: int
    os_key: str
    name: str = Field(pattern=NAME_PATTERN)
    core: bool = False
    disk_gb: int | None = Field(default=None, ge=20, le=2048)


@router.post("/build")
def build_template(body: BuildIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("template_editor"))):
    """Build a template from its OS's ISO with Packer (scripts/build-template.sh)."""
    host = db.get(KvmHost, body.kvm_host_id)
    if host is None or not host.enabled:
        raise FieldError("kvm_host_id", "Choose an enabled KVM host.")
    if body.os_key not in OS_KEYS:
        raise FieldError("os_key", f"Choose an OS: {', '.join(OS_KEYS)}.")
    if body.core and not body.os_key.startswith("windows_server"):
        raise FieldError("core", "Server Core applies to Windows Server only.")
    _name_free(db, host.id, body.name, "name")
    return _job(db, p, request, host.id, "template.build", body.model_dump(exclude={"kvm_host_id"}))


class RefreshIn(BaseModel):
    new_name: str = Field(pattern=NAME_PATTERN)
    network: str | None = None
    addressing: str = Field(default="dhcp", pattern=r"^(static|dhcp)$")


@router.post("/{template_id}/refresh")
def refresh_template(template_id: int, body: RefreshIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("template_editor"))):
    """Clone, update everything, promote as a new template (scripts/refresh-template.sh)."""
    t = db.get(Template, template_id)
    if t is None or not t.present:
        raise FieldError("template", "No such template.", status_code=404)
    if not t.os_key:
        raise FieldError("os_key", "Set this template's OS first (Edit), so the refresh knows how to update it.")
    _name_free(db, t.kvm_host_id, body.new_name, "new_name")
    return _job(db, p, request, t.kvm_host_id, "template.refresh",
                {"template": t.name, "new_name": body.new_name, "os_key": t.os_key, "network": body.network,
                 "addressing": body.addressing})


class PromoteIn(BaseModel):
    environment_id: int
    vm: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
    name: str = Field(pattern=NAME_PATTERN)


@router.post("/promote")
def promote_template(body: PromoteIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("template_editor"))):
    """Turn a deployed VM into a template (scripts/promote-to-template.sh): it's
    finalized (sysprep / cloud-init clean) and copied; the VM is left shut down."""
    env = db.get(Environment, body.environment_id)
    if env is None or not is_owner(db, p, "environment", env.id):
        raise FieldError("environment_id", "Choose one of your environments.")
    if env.status != "deployed":
        raise FieldError("environment_id", "Only a deployed environment's VMs can be promoted.")
    group = next((g for g in (env.spec or {}).get("host_groups", []) if body.vm in instance_names(g)), None)
    if group is None:
        raise FieldError("vm", f"{body.vm} isn't a VM of environment {env.name}.")
    # Domain-joined VMs are refused by the finalize step itself (template_finalize).
    _name_free(db, env.kvm_host_id, body.name, "name")
    return _job(db, p, request, env.kvm_host_id, "template.promote",
                {"environment": env.name, "vm": body.vm, "name": body.name, "os_key": group.get("os", "")},
                env_id=env.id)


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("template_editor"))):
    t = db.get(Template, template_id)
    if t is None:
        raise FieldError("template", "No such template.", status_code=404)
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    t.dirty = True  # the worker writes it to <name>.json beside the image
    db.commit()
    audit(db, p.username, "template.update", object_type="template", object_id=t.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(t)


@router.get("/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    t = db.get(Template, template_id)
    if t is None or not t.present:
        raise FieldError("template", "No such template.", status_code=404)
    return _out(t)
