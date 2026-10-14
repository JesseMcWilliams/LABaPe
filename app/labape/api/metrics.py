"""Prometheus metrics (design §38, batch A2): `GET /api/metrics` in the text exposition format.

Read with an API token (§37), normally an admin's service token with the auditor role; auditors
and admins only. Gauges only, read from what LABaPe already collects (no extra probes), so a
scrape is cheap: host load and capacity (§22.1, §28.4), environments, jobs, open alerts (§30),
linked stacks (§34) and backup ages (§32).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import appsettings, maintenance_mode, version
from ..db import get_db
from ..models import Environment, HostStats, Job, KvmHost, MonitorAlert, StackLink, utcnow
from ..security import Principal, require_roles

router = APIRouter(tags=["metrics"])
HOST_UP_WITHIN = dt.timedelta(minutes=10)


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.seen: set[str] = set()

    def g(self, name: str, help_: str, value, **labels) -> None:
        if value is None:
            return
        if name not in self.seen:
            self.seen.add(name)
            self.lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        v = float(value)
        num = str(int(v)) if v.is_integer() and abs(v) < 2**63 else repr(v)      # exact, never 8.05306e+08
        self.lines.append(f"{name}{{{lab}}} {num}" if lab else f"{name} {num}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _ts(t: dt.datetime | None) -> float | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


def collect(db: Session) -> str:
    now = utcnow()
    o = _Out()
    o.g("labape_info", "LABaPe's version (always 1)", 1, version=version.current())
    o.g("labape_maintenance", "1 while maintenance mode is on", 1 if maintenance_mode.get(db)["active"] else 0)

    stats = {r.kvm_host_id: r for r in db.scalars(select(HostStats))}
    for h in db.scalars(select(KvmHost).order_by(KvmHost.name)):
        row = stats.get(h.id)
        s = (row.stats if row else None) or {}
        vm = (s.get("storage") or {}).get("vm") or {}
        fresh = bool(row and not row.error and row.collected_at and now - dt.datetime.fromtimestamp(_ts(row.collected_at),
                                                                                                dt.timezone.utc) < HOST_UP_WITHIN)
        lab = {"host": h.name, "role": h.role}
        o.g("labape_host_enabled", "1 when the KVM host is enabled", 1 if h.enabled else 0, **lab)
        o.g("labape_host_up", "1 when the host's load was read in the last 10 minutes without an error", 1 if fresh else 0, **lab)
        o.g("labape_host_cpus", "CPUs", s.get("cpus"), **lab)
        o.g("labape_host_cpu_percent", "CPU use, percent", s.get("cpu_pct"), **lab)
        if s.get("memory_total_mb"):
            o.g("labape_host_memory_bytes", "Memory, bytes", s["memory_total_mb"] * 2**20, **lab)
            o.g("labape_host_memory_used_bytes", "Memory in use, bytes",
                (s["memory_total_mb"] - s.get("memory_available_mb", 0)) * 2**20, **lab)
        if vm.get("total_gb") is not None:
            o.g("labape_host_vm_disk_bytes", "VM storage, bytes", round(vm["total_gb"] * 2**30), **lab)
            o.g("labape_host_vm_disk_free_bytes", "VM storage free, bytes", round(vm.get("free_gb", 0) * 2**30), **lab)
        o.g("labape_host_vms_running", "VMs running", s.get("vms_running"), **lab)
        o.g("labape_host_vms_defined", "VMs defined", s.get("vms_defined"), **lab)
        o.g("labape_host_collected_timestamp_seconds", "When the host's load was last read", _ts(row.collected_at) if row else None, **lab)

    for status, n in db.execute(select(Environment.status, func.count()).group_by(Environment.status)):
        o.g("labape_environments", "Environments by status", n, status=status)
    for state in ("queued", "running"):
        o.g("labape_jobs", "Jobs queued or running", db.scalar(select(func.count()).select_from(Job).where(Job.state == state)) or 0,
            state=state)
    o.g("labape_jobs_failed_24h", "Jobs that failed in the last 24 hours",
        db.scalar(select(func.count()).select_from(Job).where(Job.state == "failed", Job.created_at >= now - dt.timedelta(days=1))) or 0)

    for level in ("warning", "error"):
        o.g("labape_alerts_open", "Open monitoring alerts", db.scalar(select(func.count()).select_from(MonitorAlert).where(
            MonitorAlert.cleared_at.is_(None), MonitorAlert.level == level)) or 0, level=level)

    from .. import links
    for link in db.scalars(select(StackLink).order_by(StackLink.name)):
        o.g("labape_link_up", "1 when the linked stack answered within 3 minutes", 0 if links.stale(link, now) or link.failures else 1,
            stack=link.name)
        o.g("labape_link_last_ok_timestamp_seconds", "When the linked stack last answered", _ts(link.last_ok_at), stack=link.name)

    from ..engine import stackbackup
    sb = stackbackup.newest(db)
    if sb and sb.get("created_at"):
        o.g("labape_backup_last_timestamp_seconds", "When the newest backup was taken",
            _ts(dt.datetime.fromisoformat(sb["created_at"])), kind="stack")
        o.g("labape_backup_last_check_ok", "1 when the newest full backup passed its check",
            1 if (sb.get("check") or {}).get("ok") else 0, kind="stack")
    from pathlib import Path
    d = Path(appsettings.get(db, "backup").directory)
    newest_db = max((p.stat().st_mtime for p in d.glob("labape-*.dump")), default=None) if d.is_dir() else None
    o.g("labape_backup_last_timestamp_seconds", "When the newest backup was taken", newest_db, kind="database")
    return o.text()


@router.get("/api/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    return PlainTextResponse(collect(db), media_type="text/plain; version=0.0.4; charset=utf-8")
