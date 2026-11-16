"""SMART self-test schedules (plan: Planning_SMART-Self-Test-Schedule.md, design §76). Admins only.

The stack default and each host's override are stored here; a change queues a `host.setup` job that runs only
`Prereq.SmartTests` on the host. What a host really has comes from its `smartd.conf` (the collector's report)."""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..appsettings import SelfTestSettings
from ..db import get_db
from ..engine import hostsetup, selftests
from ..models import HostFacts, KvmHost
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(prefix="/api/admin/selftests", tags=["selftests"])
DEVICE = re.compile(r"^/dev/[A-Za-z0-9/_.-]{1,60}$")


class OverrideIn(BaseModel):
    mode: Literal["default", "custom", "off"] = "default"
    short_on: bool = True
    short_weekday: int = Field(default=7, ge=1, le=7)
    short_hour: int = Field(default=2, ge=0, le=23)
    long_on: bool = True
    long_day: int = Field(default=1, ge=1, le=28)
    long_hour: int = Field(default=3, ge=0, le=23)
    skip: list[str] = Field(default=[], max_length=64)         # drives smartd leaves out of the tests
    warn: bool | None = None                                    # None: follow the stack default

    @field_validator("skip")
    @classmethod
    def _devices(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if not DEVICE.match(d)]
        if bad:
            raise ValueError(f"not device paths (like /dev/sdb): {', '.join(bad)}")
        return sorted(set(v))


def _host(db: Session, host_id: int) -> KvmHost:
    h = db.get(KvmHost, host_id)
    if h is None or h.deleted_at is not None:
        raise HTTPException(status_code=404, detail="No such host")
    return h


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="minutes") if t else None


def entry(db: Session, h: KvmHost) -> dict:
    """One host as the page shows it: the policy that applies, what its smartd.conf says, and each drive."""
    f = db.get(HostFacts, h.id)
    smart = (f.smart if f else None) or {}
    sd = smart.get("smartd")
    eff = selftests.effective(db, h.id)
    expr = selftests.expression(eff)
    now = selftests._local((sd or {}).get("now"))
    nxt = selftests.next_run(expr, now) if now and expr else {"short": None, "long": None}
    drives = []
    for d in smart.get("disks") or []:
        dev = d.get("device")
        applied, left_out = selftests.drive_schedule(sd, dev)
        drives.append({"device": dev, "model": d.get("model"), "serial": d.get("serial"), "power_on_hours": d.get("power_on_hours"),
                       "tests": d.get("tests"),           # None: the drive reports no self-test log (NVMe)
                       "left_out": left_out or dev in eff["skip"], "applied": describe_applied(applied),
                       "warnings": [w["message"] for w in selftests.disk_warnings(d, sd, eff)]})
    in_sync = selftests.in_sync(eff, sd)
    return {"id": h.id, "name": h.name, "ready": h.access_status == "ready", "policy": eff, "expression": expr,
            "described": selftests.describe(expr), "next": {k: _iso(v) for k, v in nxt.items()},
            "smartd": None if sd is None else {"conf": sd.get("conf"), "active": sd.get("active"), "tz": sd.get("tz"),
                                                "now": sd.get("now"), "described": selftests.describe(sd.get("scan"))},
            "in_sync": in_sync, "setup": hostsetup.state(db, h.id).get("st_attempt"), "drives": drives,
            "override": selftests.override(db, h.id)}


def describe_applied(expr: str | None) -> str:
    return selftests.describe(expr) if expr else ""


@router.get("")
def fleet(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    hosts = db.scalars(select(KvmHost).where(KvmHost.deleted_at.is_(None)).order_by(KvmHost.id)).all()
    return {"default": appsettings.get(db, "selftests").model_dump(), "hosts": [entry(db, h) for h in hosts]}


@router.get("/hosts/{host_id}")
def one(host_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    h = _host(db, host_id)
    return {"default": appsettings.get(db, "selftests").model_dump(), **entry(db, h)}


def _queue(db: Session, h: KvmHost, who: str) -> int | None:
    if h.access_status != "ready":
        return None
    job = hostsetup.queue(db, h, who, steps=["Prereq.SmartTests"])
    return job.id if job else None


@router.put("/default")
def put_default(body: SelfTestSettings, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    """Save the stack default and bring the hosts that follow it along."""
    appsettings.put(db, "selftests", body)
    queued = []
    for h in db.scalars(select(KvmHost).where(KvmHost.deleted_at.is_(None), KvmHost.access_status == "ready").order_by(KvmHost.id)):
        if selftests.effective(db, h.id)["mode"] == "default" and _queue(db, h, p.username):
            queued.append(h.name)
    audit(db, p.username, "selftests.default", detail={**body.model_dump(), "queued": queued}, source_ip=client_ip(request))
    return {"default": body.model_dump(), "queued": queued}


@router.put("/hosts/{host_id}")
def put_host(host_id: int, body: OverrideIn, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(require_roles("admin"))):
    """Save a host's override and apply it on the host (a setup job that runs only the self-test step)."""
    h = _host(db, host_id)
    if body.mode == "default" and not body.skip and body.warn is None:
        selftests.save_override(db, h.id, {})
    else:
        selftests.save_override(db, h.id, body.model_dump())
    job_id = _queue(db, h, p.username)
    audit(db, p.username, "selftests.host", object_type="kvm_host", object_id=h.id,
          detail={**body.model_dump(), "job": job_id}, source_ip=client_ip(request))
    return {"job_id": job_id, "queued": job_id is not None,
            "note": "" if job_id else "Saved; it applies on the host when SSH access is ready and no other setup job is running "
                                      "(the monitoring tick retries it)."}
