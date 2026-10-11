"""Template library (design §21): list for the deploy form, edit metadata."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import FieldError
from ..models import Template
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
