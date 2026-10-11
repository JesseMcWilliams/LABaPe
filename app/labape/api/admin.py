"""Admin: audit log (and export), user errors, role bindings, auth provider status."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import load_providers, provider_enabled
from ..config import ROLES
from ..db import get_db
from .. import appsettings
from ..errors import FieldError
from ..models import AuditLog, RoleBinding, UserError
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/admin", tags=["admin"])


AUDIT_FIELDS = ("id", "at", "actor", "action", "object_type", "object_id", "outcome", "detail", "source_ip")


def _audit_query(actor: str | None, action: str | None, since: dt.datetime | None, until: dt.datetime | None):
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if since:
        q = q.where(AuditLog.at >= since)
    if until:
        q = q.where(AuditLog.at <= until)
    return q


@router.get("/audit")
def audit_log(limit: int = 200, actor: str | None = None, action: str | None = None,
              since: dt.datetime | None = None, until: dt.datetime | None = None,
              db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    rows = db.scalars(_audit_query(actor, action, since, until).limit(min(limit, 1000)))
    return [{c: getattr(r, c) for c in AUDIT_FIELDS} for r in rows]


@router.get("/audit/export")
def audit_export(request: Request, format: str = "json", actor: str | None = None, action: str | None = None,
                 since: dt.datetime | None = None, until: dt.datetime | None = None,
                 db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """Download the (filtered) audit log. JSON now; CSV is planned."""
    if format != "json":
        raise FieldError("format", "Only JSON export is available so far; CSV is planned.")
    rows = [{c: getattr(r, c) for c in AUDIT_FIELDS}
            for r in db.scalars(_audit_query(actor, action, since, until).limit(100000))]
    audit(db, p.username, "audit.export", detail={"format": format, "rows": len(rows), "actor": actor,
                                                  "action": action}, source_ip=client_ip(request))
    body = json.dumps(rows, default=str, indent=2)
    name = f"labape-audit-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- user errors (question 59) ---------------------------------------------

def error_retention_days(db: Session) -> int:
    return appsettings.get(db, "retention").errors_days  # Settings page, History retention


@router.get("/errors")
def user_errors(limit: int = 200, username: str | None = None, db: Session = Depends(get_db),
                _: Principal = Depends(require_roles("admin"))):
    q = select(UserError).order_by(UserError.id.desc()).limit(min(limit, 2000))
    if username:
        q = q.where(UserError.username == username)
    rows = [{c: getattr(r, c) for c in ("id", "at", "username", "method", "path", "status", "message", "fields",
                                        "origin")} for r in db.scalars(q)]
    return {"retention_days": error_retention_days(db), "errors": rows}


class RetentionIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.put("/errors/retention")
def set_error_retention(body: RetentionIn, request: Request, db: Session = Depends(get_db),
                        p: Principal = Depends(require_roles("admin"))):
    r = appsettings.get(db, "retention")
    appsettings.put(db, "retention", r.model_copy(update={"errors_days": body.days}))
    audit(db, p.username, "errors.retention", detail={"days": body.days}, source_ip=client_ip(request))
    return {"retention_days": body.days}


def purge_user_errors(db: Session) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=error_retention_days(db))
    n = db.execute(delete(UserError).where(UserError.at < cutoff)).rowcount or 0
    db.commit()
    return n


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
