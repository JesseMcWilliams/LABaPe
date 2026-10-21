"""One physical drive of a KVM or stack host: what it is, how it is doing and how it has changed (design §64, batch H3).

`GET /api/hosts/{id}/disks/{disk}` where `disk` is the drive's serial number or its device (`sdc` or `/dev/sdc`). The
current reading comes from the host's facts (the daily-to-6-hourly SMART read); the history from `disk_samples`.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..db import get_db
from ..engine import hostadmin
from ..models import DiskSample, HostFacts, KvmHost, MonitorAlert, utcnow
from ..security import Principal, require_roles

router = APIRouter(prefix="/api/hosts", tags=["hosts"])

TRACKED = ("reallocated", "pending", "uncorrectable")          # a rise in these is an event worth listing


def _find(smart: dict, disk: str) -> dict | None:
    want = disk if disk.startswith("/dev/") else f"/dev/{disk}"
    for d in (smart or {}).get("disks", []):
        if disk and disk == d.get("serial"):
            return d
    for d in (smart or {}).get("disks", []):
        if want == d.get("device"):
            return d
    return None


def _block(nodes: list[dict], serial: str, device: str) -> dict | None:
    for n in nodes or []:
        if (serial and n.get("serial") == serial) or (device and n.get("path") == device):
            return n
        found = _block(n.get("children") or [], serial, device)
        if found:
            return found
    return None


def _tree(n: dict) -> dict:
    return {"name": n.get("name"), "path": n.get("path"), "type": n.get("type"), "size": n.get("size"),
            "fstype": n.get("fstype"), "mountpoints": [m for m in (n.get("mountpoints") or []) if m],
            "children": [_tree(c) for c in n.get("children") or []]}


def changes(samples: list[dict]) -> list[dict]:
    """Where a tracked number moved between two readings, newest first."""
    out = []
    for prev, cur in zip(samples, samples[1:]):
        for k in TRACKED:
            a, b = prev["data"].get(k), cur["data"].get(k)
            if a is not None and b is not None and a != b:
                out.append({"at": cur["at"], "attribute": k, "from": a, "to": b})
    return out[::-1]


@router.get("/{host_id}/disks/{disk:path}")
def disk_detail(host_id: int, disk: str, days: int = Query(default=30, ge=1, le=3650), db: Session = Depends(get_db),
                _: Principal = Depends(require_roles("admin"))):
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    f = db.get(HostFacts, host_id)
    smart = (f.smart if f else {}) or {}
    cur = _find(smart, disk)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"{h.name} has no drive {disk!r} in its last SMART reading")
    key = cur.get("serial") or cur.get("device")
    since = utcnow() - dt.timedelta(days=days)
    rows = db.scalars(select(DiskSample).where(DiskSample.kvm_host_id == host_id, DiskSample.disk == key,
                                              DiskSample.at >= since).order_by(DiskSample.at)).all()
    samples = [{"at": r.at.isoformat() if r.at.tzinfo else r.at.replace(tzinfo=dt.timezone.utc).isoformat(), "data": r.data}
               for r in rows]
    block = _block((f.facts if f else {}).get("disks") or [], cur.get("serial", ""), cur.get("device", ""))
    alerts = [{"level": a.level, "message": a.message, "started_at": a.started_at, "acked": a.acked_at is not None}
              for a in db.scalars(select(MonitorAlert).where(MonitorAlert.kvm_host_id == host_id,
                                                              MonitorAlert.cleared_at.is_(None)))
              if hostadmin.disk_of(a.message) == cur.get("device")]
    return {"host": {"id": h.id, "name": h.name}, "disk": cur, "block": _tree(block) if block else None,
            "smart_at": f.smart_at if f else None, "days": days,
            "retention_days": appsettings.get(db, "retention").disk_history_days,
            "samples": samples, "changes": changes(samples), "alerts": alerts,
            "warnings": [w for w in hostadmin.warnings({}, {"disks": [cur]}, None) if w.get("disk") == cur.get("device")]}
