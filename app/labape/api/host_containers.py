"""The Containers page of a host (plan C1, Planning_Container-Management.md): read-only.

`GET /api/hosts/{id}/containers` is what the 5-minute collector stored; `POST .../containers/refresh` reads now;
`POST .../containers/logs` and `.../stats` read one container on request and store nothing. Admin only, since a
log can hold secrets. No route here changes a container (that is batch C2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine import hostadmin, hostcontainers
from ..errors import FieldError
from ..models import HostContainers, KvmHost
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _host(db: Session, host_id: int) -> KvmHost:
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    return h


@router.get("/{host_id}/containers")
def containers(host_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    h = _host(db, host_id)
    return {**hostcontainers.build(db.get(HostContainers, h.id)), "alerts": h.container_alerts}


@router.post("/{host_id}/containers/refresh")
def refresh(host_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    """Read the host's containers now, instead of waiting for the 5-minute collection."""
    h = _host(db, host_id)
    try:
        hostadmin.for_host(h)
    except hostadmin.AdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = hostcontainers.collect(db, h)
    if row.error:
        raise HTTPException(status_code=502, detail=f"Reading the host's containers failed: {row.error}")
    return {**hostcontainers.build(row), "alerts": h.container_alerts}


class LogsIn(BaseModel):
    name: str = Field(max_length=128)
    tail: int = Field(default=200, ge=1, le=hostcontainers.LOG_MAX_LINES)
    since: str = Field(default="", max_length=6)
    filter: str = Field(default="", max_length=200)


@router.post("/{host_id}/containers/logs")
def container_logs(host_id: int, body: LogsIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    """A container's recent log lines (capped, secrets masked). The audit entry says that they were read, not what they said."""
    h = _host(db, host_id)
    detail = {"container": body.name, "tail": body.tail, "since": body.since}
    try:
        out = hostcontainers.logs(hostadmin.for_host(h), db.get(HostContainers, h.id), body.name, body.tail, body.since, body.filter)
    except hostadmin.AdminError as exc:
        audit(db, p.username, "host.container.logs", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={**detail, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("name", str(exc)) from exc
    audit(db, p.username, "host.container.logs", object_type="kvm_host", object_id=h.id, detail=detail, source_ip=client_ip(request))
    return out


class NameIn(BaseModel):
    name: str = Field(max_length=128)


@router.post("/{host_id}/containers/stats")
def container_stats(host_id: int, body: NameIn, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    """One fresh CPU and memory reading for a container."""
    h = _host(db, host_id)
    try:
        return hostcontainers.live_stats(hostadmin.for_host(h), db.get(HostContainers, h.id), body.name)
    except hostadmin.AdminError as exc:
        raise FieldError("name", str(exc)) from exc


class AlertsIn(BaseModel):
    enabled: bool


@router.post("/{host_id}/containers/alerts")
def container_alerts(host_id: int, body: AlertsIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("admin"))):
    """Switch the Monitoring alerts for this host's containers on or off (plan question 207)."""
    h = _host(db, host_id)
    h.container_alerts = body.enabled
    db.commit()
    audit(db, p.username, "host.container.alerts", object_type="kvm_host", object_id=h.id,
          detail={"enabled": body.enabled}, source_ip=client_ip(request))
    return {"alerts": h.container_alerts}
