"""Maintenance mode API (design §23.3)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import appsettings, maintenance_mode
from ..db import get_db
from ..errors import FieldError
from ..models import Job
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(tags=["maintenance"])


def next_window(db: Session, now: dt.datetime | None = None) -> dict | None:
    """The next weekly maintenance window, and whether its heads-up is due."""
    w = appsettings.get(db, "maintenance_window", cached=True)
    if not w.enabled:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    hh, mm = (int(x) for x in w.time_of_day.split(":"))
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + dt.timedelta(days=(w.weekday - now.weekday()) % 7)
    if start + dt.timedelta(minutes=w.duration_minutes) <= now:
        start += dt.timedelta(days=7)
    return {"start": start.isoformat(), "end": (start + dt.timedelta(minutes=w.duration_minutes)).isoformat(),
            "in_window": start <= now, "heads_up": start - dt.timedelta(hours=w.heads_up_hours) <= now}


def status(db: Session) -> dict:
    state = maintenance_mode.get(db)
    running = db.scalar(select(func.count()).select_from(Job).where(Job.state == "running")) or 0
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.state == "queued")) or 0
    return {**state, "running_jobs": running, "queued_jobs": queued, "window": next_window(db)}


@router.get("/api/maintenance")
def get_status(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    return status(db)


class MaintenanceIn(BaseModel):
    active: bool
    reason: str = Field(default="", max_length=500)
    expected_end: str | None = None          # ISO time
    scheduled_start: str | None = None       # ISO time: turn on then, instead of now


def _iso(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value)
    except ValueError:
        raise FieldError(field, "Use a date and time, e.g. 2026-10-12T02:00:00Z.") from None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()


@router.put("/api/admin/maintenance")
def set_status(body: MaintenanceIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    if body.active:
        if not body.reason.strip():
            raise FieldError("reason", "Say why, so everyone sees it in the banner.")
        end = _iso(body.expected_end, "expected_end")
        start = _iso(body.scheduled_start, "scheduled_start")
        if start and dt.datetime.fromisoformat(start) > dt.datetime.now(dt.timezone.utc):
            maintenance_mode.schedule(db, p.username, body.reason.strip(), start, end)
            action = "maintenance.schedule"
        else:
            maintenance_mode.turn_on(db, p.username, body.reason.strip(), "admin", end)
            action = "maintenance.on"
    else:
        maintenance_mode.turn_off(db)
        action = "maintenance.off"
    audit(db, p.username, action, detail={"reason": body.reason, "expected_end": body.expected_end,
                                          "scheduled_start": body.scheduled_start}, source_ip=client_ip(request))
    return status(db)
