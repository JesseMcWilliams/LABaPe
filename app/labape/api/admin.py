"""Admin: audit log, role bindings, auth provider status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import load_providers, provider_enabled
from ..config import ROLES
from ..db import get_db
from ..models import AuditLog, RoleBinding
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)))
    return [{c: getattr(r, c) for c in ("id", "at", "actor", "action", "object_type", "object_id", "outcome",
                                        "detail", "source_ip")} for r in rows]


class BindingIn(BaseModel):
    principal_type: str
    principal: str
    role: str


@router.get("/role-bindings")
def list_bindings(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return [{"id": b.id, "principal_type": b.principal_type, "principal": b.principal, "role": b.role}
            for b in db.scalars(select(RoleBinding))]


@router.post("/role-bindings", status_code=201)
def add_binding(body: BindingIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    if body.role not in ROLES or body.principal_type not in ("user", "group"):
        raise HTTPException(status_code=422, detail="Unknown role or principal type")
    b = RoleBinding(**body.model_dump())
    db.add(b)
    db.commit()
    audit(db, p.username, "role_binding.add", object_type="role_binding", object_id=b.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return {"id": b.id}


@router.delete("/role-bindings/{binding_id}")
def delete_binding(binding_id: int, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    b = db.get(RoleBinding, binding_id)
    if b is None:
        raise HTTPException(status_code=404, detail="No such binding")
    db.delete(b)
    db.commit()
    audit(db, p.username, "role_binding.delete", object_type="role_binding", object_id=binding_id,
          source_ip=client_ip(request))
    return {"ok": True}


@router.get("/auth-providers")
async def auth_providers(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    out = []
    for name, prov in load_providers().items():
        ok, msg = await prov.test_connection()
        out.append({"name": name, "kind": prov.kind, "enabled": provider_enabled(db, name), "healthy": ok,
                    "detail": msg})
    return out
