"""The Containers page of a host (plan C1, Planning_Container-Management.md): read-only.

`GET /api/hosts/{id}/containers` is what the 5-minute collector stored; `POST .../containers/refresh` reads now;
`POST .../containers/logs` and `.../stats` read one container on request and store nothing. Admin only, since a
log can hold secrets. `POST .../containers/action` (batch C2) start, stop, restart or recreate one container, only
as far as the host's action level allows; the Stack's own containers are never changed from here.
"""
from __future__ import annotations

import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine import containerupdates, hostadmin, hostcontainers
from ..errors import FieldError
from ..models import HostContainers, KvmHost, utcnow
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
    return {**hostcontainers.build(db.get(HostContainers, h.id), h), "alerts": h.container_alerts}


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
    return {**hostcontainers.build(row, h), "alerts": h.container_alerts}


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


@router.get("/{host_id}/containers/history")
def container_history(host_id: int, name: str, hours: int = 24, db: Session = Depends(get_db),
                      _: Principal = Depends(require_roles("admin"))):
    """CPU and memory of one container over the last 1 to 168 hours (plan A1)."""
    h = _host(db, host_id)
    if not 1 <= hours <= 168:
        raise FieldError("hours", "Choose 1 to 168 hours.")
    if not hostcontainers.NAME.match(name):
        raise FieldError("name", "Not a container name.")
    return {"name": name, "hours": hours, "points": hostcontainers.history(db, h.id, name, hours)}


class SwitchIn(BaseModel):
    enabled: bool


@router.post("/{host_id}/containers/updates/switch")
def updates_switch(host_id: int, body: SwitchIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    """The weekly check for newer images: off unless turned on, since the host then reaches out to registries."""
    h = _host(db, host_id)
    h.container_updates = body.enabled
    db.commit()
    audit(db, p.username, "host.container.updates_switch", object_type="kvm_host", object_id=h.id,
          detail={"enabled": body.enabled}, source_ip=client_ip(request))
    return {"enabled": h.container_updates}


@router.post("/{host_id}/containers/updates/check")
def updates_check(host_id: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """Ask the registries now (digests only; nothing is pulled)."""
    h = _host(db, host_id)
    try:
        res = containerupdates.check(db, h)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=502, detail=f"The check failed: {exc}") from exc
    audit(db, p.username, "host.container.updates_check", object_type="kvm_host", object_id=h.id,
          detail={"newer": sorted(r for r, v in res["images"].items() if v == "newer")}, source_ip=client_ip(request))
    return hostcontainers.build(db.get(HostContainers, h.id), h)


class ProjectIn(BaseModel):
    project: str = Field(max_length=128)


@router.post("/{host_id}/containers/project")
def project_update(host_id: int, body: ProjectIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    """Pull and recreate a whole compose project (every container of it must be one the host's level allows)."""
    h = _host(db, host_id)
    detail = {"project": body.project, "level": h.container_level}
    try:
        out = containerupdates.run_project(hostadmin.for_host(h), h, db.get(HostContainers, h.id), body.project)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        audit(db, p.username, "host.container.project", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={**detail, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("project", str(exc)) from exc
    audit(db, p.username, "host.container.project", object_type="kvm_host", object_id=h.id, detail=detail, source_ip=client_ip(request))
    try:
        hostcontainers.collect(db, h)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "output": out}


class ActionIn(BaseModel):
    name: str = Field(max_length=128)
    op: str = Field(max_length=10)


@router.post("/{host_id}/containers/action")
def container_action(host_id: int, body: ActionIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("admin"))):
    """Start, stop, restart or recreate one container. The host's level decides which; the facts are read again after."""
    h = _host(db, host_id)
    detail = {"container": body.name, "level": h.container_level}
    try:
        out = hostcontainers.run_action(hostadmin.for_host(h), h, db.get(HostContainers, h.id), body.name, body.op)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        audit(db, p.username, f"host.container.{body.op}", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={**detail, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("name", str(exc)) from exc
    audit(db, p.username, f"host.container.{body.op}", object_type="kvm_host", object_id=h.id, detail=detail, source_ip=client_ip(request))
    try:
        hostcontainers.collect(db, h)
    except Exception:  # noqa: BLE001 - the action worked; the next scheduled read will catch up
        pass
    return {"ok": True, "output": out}


class LevelIn(BaseModel):
    level: str = Field(pattern=r"^(off|known|new|all)$")


@router.post("/{host_id}/containers/level")
def container_level(host_id: int, body: LevelIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("admin"))):
    """What the page may act on for this host: off, known, new or all (plan question 203)."""
    h = _host(db, host_id)
    if body.level != h.container_level:
        h.container_level, h.container_level_at = body.level, utcnow()
        db.commit()
        audit(db, p.username, "host.container.level", object_type="kvm_host", object_id=h.id,
              detail={"level": body.level}, source_ip=client_ip(request))
    return {"level": h.container_level, "known": list(h.container_known or [])}


class KnownIn(BaseModel):
    name: str = Field(max_length=128)
    known: bool


@router.post("/{host_id}/containers/known")
def container_known(host_id: int, body: KnownIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("admin"))):
    """Mark a container as known (the level "known" and "new" may act on it) or take the mark off."""
    h = _host(db, host_id)
    row = db.get(HostContainers, h.id)
    if not any(c["name"] == body.name for c in ((row.data if row else {}) or {}).get("containers") or []):
        raise FieldError("name", "no such container on this host (refresh the page)")
    known = [n for n in (h.container_known or []) if n != body.name]
    if body.known:
        known.append(body.name)
    h.container_known = sorted(known)
    db.commit()
    audit(db, p.username, "host.container.known", object_type="kvm_host", object_id=h.id,
          detail={"container": body.name, "known": body.known}, source_ip=client_ip(request))
    return {"level": h.container_level, "known": h.container_known}


@router.get("/{host_id}/containers/cleanup")
def cleanup_preview(host_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    """What could be removed and what it would free: stopped containers the level allows, images nothing uses,
    volumes no container refers to. Computed from the last read; nothing is removed by asking."""
    h = _host(db, host_id)
    page = hostcontainers.build(db.get(HostContainers, h.id), h)
    return {"level": h.container_level, **hostcontainers.cleanup_candidates(page)}


class CleanupIn(BaseModel):
    kind: str = Field(pattern=r"^(containers|dangling_images|unused_images|volumes)$")
    keys: list[str] = Field(min_length=1, max_length=500)
    confirm: str = Field(default="", max_length=20)


@router.post("/{host_id}/containers/cleanup")
def cleanup(host_id: int, body: CleanupIn, request: Request, db: Session = Depends(get_db),
            p: Principal = Depends(require_roles("admin"))):
    """Remove the picked items, every one of which must still be a candidate. Removing volumes deletes their data
    and needs the word `delete`. The runtime itself refuses anything in use (no force flag)."""
    h = _host(db, host_id)
    detail = {"kind": body.kind, "count": len(body.keys)}
    try:
        out = hostcontainers.run_cleanup(hostadmin.for_host(h), h, db.get(HostContainers, h.id), body.kind, body.keys, body.confirm)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        audit(db, p.username, "host.container.cleanup", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={**detail, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("keys", str(exc)) from exc
    audit(db, p.username, "host.container.cleanup", object_type="kvm_host", object_id=h.id,
          detail={**detail, "removed": len(out["removed"]), "kept": len(out["kept"]), "items": body.keys[:50]}, source_ip=client_ip(request))
    try:
        hostcontainers.collect(db, h)
    except Exception:  # noqa: BLE001 - the clean-up worked; the next scheduled read will catch up
        pass
    return out
