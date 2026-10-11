"""Updates and rollback from the web interface (design §23.2). Admins only.
These paths stay open during maintenance mode."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import appsettings, update, version
from ..db import get_db
from ..errors import FieldError
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/admin/update", tags=["update"])


def choices(db: Session) -> dict:
    """What Check now offers: the newest release, a newer pre-release, the previous version."""
    current = version.current()
    cfg = appsettings.get(db, "updates")
    out: dict = {"current": current, "release": None, "prerelease": None, "error": ""}
    try:
        rels = update.releases(cfg.repo) if cfg.check_enabled else []
    except Exception as exc:  # noqa: BLE001
        rels, out["error"] = [], f"Couldn't read the releases: {exc}"
    stable = next((r for r in rels if not r["prerelease"]), None)
    if stable and version.newer(stable["tag"], current):
        out["release"] = stable
    pre = next((r for r in rels if r["prerelease"]), None)
    floor = stable["tag"] if stable and version.newer(stable["tag"], current) else current
    if pre and version.newer(pre["tag"], floor):
        out["prerelease"] = pre
    return out


@router.get("")
def get_update(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    status = version.status(db)
    return {**choices(db), "db_revision": status.get("db_revision"), "code_revision": status.get("code_revision"),
            "previous": update.previous(), "agent": {"installed": update.agent_installed(), **update.status()}}


class UpdateIn(BaseModel):
    target: str = Field(pattern=r"^v\d+\.\d+\.\d+[A-Za-z0-9.+-]*$")


@router.post("")
def start_update(body: UpdateIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    if not update.agent_installed():
        raise FieldError("target", "The update agent isn't installed on this host; run the commands shown instead.",
                         status_code=409)
    if update.busy():
        raise FieldError("target", "An update or rollback is already under way.", status_code=409)
    offered = {c["tag"] for c in (choices(db)["release"], choices(db)["prerelease"]) if c}
    if body.target not in offered:
        raise FieldError("target", f"{body.target} isn't one of the versions on offer.")
    req = update.write_request("update", body.target, p.username)
    audit(db, p.username, "update.request", detail=req, source_ip=client_ip(request))
    return {"requested": req}


@router.post("/rollback")
def start_rollback(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    prev = update.previous()
    if not update.agent_installed() or not prev:
        raise FieldError("rollback", "There's no previous version on standby to go back to.", status_code=409)
    if update.busy():
        raise FieldError("rollback", "An update or rollback is already under way.", status_code=409)
    req = update.write_request("rollback", prev.get("version", ""), p.username)
    audit(db, p.username, "update.rollback.request", detail=req, source_ip=client_ip(request))
    return {"requested": req}
