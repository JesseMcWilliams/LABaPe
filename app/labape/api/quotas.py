"""Quotas (design §41): admins set them; everyone sees their own limits and usage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import quotas
from ..db import get_db
from ..models import Quota
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(tags=["quotas"])


class QuotaIn(BaseModel):
    principal_type: str = Field(pattern="^(user|group|default)$")
    principal: str = Field(default="", max_length=255)
    max_vcpus: int | None = Field(default=None, ge=0, le=100000)
    max_memory_mb: int | None = Field(default=None, ge=0, le=100_000_000)
    max_disk_gb: int | None = Field(default=None, ge=0, le=10_000_000)
    max_environments: int | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def _who(self):
        if self.principal_type == "default":
            self.principal = ""
        elif not self.principal.strip():
            raise ValueError("name the user or group")
        return self


def _out(q: Quota) -> dict:
    return {"id": q.id, "principal_type": q.principal_type, "principal": q.principal,
            **{k: getattr(q, k) for k in quotas.LIMITS}}


@router.get("/api/quotas/me")
def my_quota(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    return quotas.summary(db, p)


@router.get("/api/admin/quotas")
def list_quotas(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return [_out(q) for q in db.scalars(select(Quota).order_by(Quota.principal_type, Quota.principal))]


@router.post("/api/admin/quotas")
def save_quota(body: QuotaIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    """Create or replace the quota for that user, group or the default."""
    q = db.scalar(select(Quota).where(Quota.principal_type == body.principal_type, Quota.principal == body.principal))
    if q is None:
        q = Quota(principal_type=body.principal_type, principal=body.principal)
        db.add(q)
    for k in quotas.LIMITS:
        setattr(q, k, getattr(body, k))
    db.commit()
    audit(db, p.username, "quota.save", object_type="quota", object_id=q.id, detail=_out(q), source_ip=client_ip(request))
    return _out(q)


@router.delete("/api/admin/quotas/{quota_id}")
def delete_quota(quota_id: int, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    q = db.get(Quota, quota_id)
    if q is None:
        raise HTTPException(404, "No such quota")
    detail = _out(q)
    db.delete(q)
    db.commit()
    audit(db, p.username, "quota.delete", object_type="quota", object_id=quota_id, detail=detail,
          source_ip=client_ip(request))
    return {"ok": True}
