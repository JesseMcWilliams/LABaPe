"""The Fix button on the admin dashboard's checks (app/labape/fixes.py). Admins only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import fixes
from ..db import get_db
from ..errors import FieldError
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/admin/fixes", tags=["admin"])


class FixIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.get("")
def list_fixes(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return fixes.listing(db)


@router.post("/{name}")
def run_fix(name: str, body: FixIn, request: Request, db: Session = Depends(get_db),
            p: Principal = Depends(require_roles("admin"))):
    fix = fixes.FIXES.get(name)
    if fix is None:
        raise FieldError("name", f"There is no fix called {name!r}.", status_code=404)
    values = {k: (v or "") for k, v in body.values.items() if k in {f["name"] for f in fix.fields}}
    for f in fix.fields:
        if f["required"] and not values.get(f["name"], "").strip():
            raise FieldError(f["name"], f"{f['label']} is needed.")
    detail = {k: values[k] for k in fix.detail_keys if k in values}
    try:
        message = fix.run(db, values, p.username)
    except FieldError as exc:
        audit(db, p.username, f"fix.{name}", outcome="failed", detail={**detail, "error": str(exc.detail)[:200]},
              source_ip=client_ip(request))
        raise
    audit(db, p.username, f"fix.{name}", detail=detail, source_ip=client_ip(request))
    return {"ok": True, "message": message}
