"""Deleting a KVM host (design §55): marked deleted now, removed after a retention period.

Deleting only takes the host out of LABaPe; nothing is uninstalled on the host itself (the worker's key stays
authorized there until someone removes it). A deleted host is hidden everywhere (db.py filters it out of every
query), can be restored until its retention ends (Settings → History), and is then purged: its facts, metrics,
stats, alerts, templates and network attachments go, jobs keep their history, and the row is deleted, or, when
an environment (even a destroyed one) still points at it, reduced to a hidden tombstone with no address.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import (Environment, HostFacts, HostMetric, HostNetwork, HostStats, Job, KvmHost, MetricSample, MonitorAlert,
                      RemoteSession, StackStatus, Template, utcnow)

ALL = {"include_deleted_hosts": True}


def blockers(db: Session, h: KvmHost) -> list[str]:
    """What stops a host from being deleted now; empty when it can go."""
    from .. import spread
    from . import stackmon
    out = []
    if h.is_primary:
        out.append("it is the primary file host: make another host the primary first (it holds every file)")
    if stackmon.is_self(h):
        out.append("this stack runs on it")
    busy = db.scalars(select(Job.id).where(Job.kvm_host_id == h.id, Job.state.in_(("queued", "running")))).all()
    if busy:
        out.append(f"{len(busy)} job{'s are' if len(busy) != 1 else ' is'} queued or running on it")
    using = [e.name for e in db.scalars(select(Environment).where(Environment.status != "destroyed"))
             if h.id in {e.kvm_host_id, *spread.host_ids(e)}]
    if using:
        out.append(f"environments use it: {', '.join(sorted(using)[:6])}{' …' if len(using) > 6 else ''} (destroy or move them first)")
    return out


def retention_days(db: Session) -> int:
    return appsettings.get(db, "retention").deleted_hosts_days


def purge_on(db: Session, h: KvmHost) -> dt.datetime | None:
    if h.deleted_at is None:
        return None
    t = h.deleted_at if h.deleted_at.tzinfo else h.deleted_at.replace(tzinfo=dt.timezone.utc)
    return t + dt.timedelta(days=retention_days(db))


def soft_delete(db: Session, h: KvmHost, who: str) -> None:
    """Mark the host deleted. It is turned off first so nothing is placed on it if it is restored later."""
    h.enabled = False
    h.deleted_at = utcnow()
    h.deleted_by = who
    db.commit()


def deleted_hosts(db: Session) -> list[KvmHost]:
    return list(db.scalars(select(KvmHost).where(KvmHost.deleted_at.is_not(None), KvmHost.purged.is_(False))
                           .order_by(KvmHost.deleted_at.desc()), execution_options=ALL))


def get_deleted(db: Session, host_id: int) -> KvmHost | None:
    return db.scalar(select(KvmHost).where(KvmHost.id == host_id, KvmHost.deleted_at.is_not(None), KvmHost.purged.is_(False)),
                     execution_options=ALL)


def restore(db: Session, h: KvmHost) -> None:
    """Bring a deleted host back, turned off (turn it on in its settings once it is checked)."""
    h.deleted_at, h.deleted_by, h.enabled = None, "", False
    db.commit()


def purge(db: Session, h: KvmHost) -> str:
    """Remove a deleted host's data now; the row too unless an environment still points at it."""
    hid = h.id
    for model in (HostFacts, HostMetric, HostNetwork, HostStats, MetricSample, MonitorAlert, StackStatus, Template):
        db.execute(delete(model).where(model.kvm_host_id == hid), execution_options=ALL)
    db.execute(update(Job).where(Job.kvm_host_id == hid).values(kvm_host_id=None), execution_options=ALL)
    db.execute(update(RemoteSession).where(RemoteSession.kvm_host_id == hid).values(kvm_host_id=None), execution_options=ALL)
    if db.scalar(select(Environment.id).where(Environment.kvm_host_id == hid).limit(1)) is not None:
        h.name, h.libvirt_uri, h.ssh_address, h.stack_url, h.files_path = f"deleted-{hid}", "", "", "", ""
        h.access_status, h.access_error, h.log_forward, h.purged = "none", "", False, True
        db.commit()
        return "kept as a hidden record (environments still point at it)"
    db.delete(h)
    db.commit()
    return "removed"


def purge_due(db: Session, now: dt.datetime | None = None) -> list[str]:
    """The daily purge: every deleted host whose retention has ended."""
    now = now or utcnow()
    done = []
    for h in deleted_hosts(db):
        due = purge_on(db, h)
        if due is not None and due <= now:
            name = h.name
            done.append(f"{name}: {purge(db, h)}")
    return done
