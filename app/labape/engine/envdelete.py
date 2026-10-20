"""Deleting an environment's record (2026-10-20 round): once its VMs are gone (destroyed, or a new
environment that never deployed), the row and what hangs off it can go, and the name is free again.

What goes: its stored secrets (built-in rows, and external stores' copies, best effort), its rotation
policies, IP allocations, snapshot records and ownership grants; console tokens forget its id. Jobs
and remote sessions keep their rows with the environment cleared, and the audit log keeps everything.
Nothing is changed on any host: the destroy already deleted the VMs, their disks and the OpenTofu
workspace."""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import stores
from ..models import ApiToken, Environment, Grant, Job, RemoteSession, Secret, SecretPolicy, VmSnapshot
from ..netpolicy import release

log = logging.getLogger(__name__)

DELETABLE = ("destroyed", "new")


def blockers(db: Session, env: Environment) -> list[str]:
    """Why the record can't be deleted yet (empty: it can)."""
    out = []
    if env.status not in DELETABLE:
        out.append(f"it is {env.status}: destroy it first, so no VM is left behind")
    busy = db.scalar(select(Job.id).where(Job.environment_id == env.id, Job.state.in_(("queued", "running"))))
    if busy is not None:
        out.append(f"job {busy} is queued or running for it")
    return out


def delete(db: Session, env: Environment) -> dict:
    """Delete the record and what belongs to it; returns counts for the audit log. The caller commits."""
    eid = env.id
    prefix = f"environment/{eid}/"
    secrets = list(db.scalars(select(Secret).where(Secret.name.startswith(prefix))))
    for row in secrets:
        if row.store != "builtin":
            try:
                stores.ext_delete(db, row.store, row.name)
            except Exception as exc:  # noqa: BLE001 — the row goes either way; the store keeps an orphan
                log.warning("environment %s: couldn't delete %s from %s: %s", eid, row.name, row.store, exc)
        db.delete(row)
    policies = [p for p in db.scalars(select(SecretPolicy)) if (p.params or {}).get("environment_id") == eid]
    for p in policies:
        db.delete(p)
    addresses = release(db, eid)
    snapshots = list(db.scalars(select(VmSnapshot).where(VmSnapshot.environment_id == eid)))
    for s in snapshots:
        db.delete(s)
    grants = list(db.scalars(select(Grant).where(Grant.object_type == "environment", Grant.object_id == eid)))
    for g in grants:
        db.delete(g)
    for t in db.scalars(select(ApiToken)):
        if eid in (t.environments or []):
            t.environments = [x for x in t.environments if x != eid]
    db.execute(update(Job).where(Job.environment_id == eid).values(environment_id=None))
    db.execute(update(RemoteSession).where(RemoteSession.environment_id == eid).values(environment_id=None))
    db.flush()
    db.delete(env)
    return {"name": env.name, "secrets": len(secrets), "policies": len(policies), "addresses": addresses,
            "snapshots": len(snapshots), "grants": len(grants)}
