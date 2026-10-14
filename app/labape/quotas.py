"""Quotas (design §41, batch E2; review item 5): limits on what each user's environments may use.

A quota limits vCPUs, memory, VM disk and the number of environments (each optional: empty is no
limit). It applies to a user, to a group, or to everyone (the default). A user's own quota wins;
else the most generous of their groups' quotas, limit by limit; else the default; else no limit.
Admins have none.

Usage is what the user's environments ask for (their host groups: count times CPUs, memory and
disk, as for the host capacity check), counting every environment they created that isn't destroyed.
It's checked when an environment is created and when a destroyed one is deployed again.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine.hoststats import demand_of
from .models import Environment, Quota
from .security import Principal

LIMITS = ("max_vcpus", "max_memory_mb", "max_disk_gb", "max_environments")
USE = {"max_vcpus": "vcpus", "max_memory_mb": "memory_mb", "max_disk_gb": "disk_gb", "max_environments": "environments"}
LABELS = {"max_vcpus": "vCPUs", "max_memory_mb": "MB of memory", "max_disk_gb": "GB of VM disk",
          "max_environments": "environments"}


class QuotaError(Exception):
    pass


def limits_for(db: Session, p: Principal) -> dict | None:
    """The limits that apply to `p` ({limit: number or None}), and where they come from; None: none."""
    if p.is_admin:
        return None
    rows = list(db.scalars(select(Quota)))
    mine = next((q for q in rows if q.principal_type == "user" and q.principal == p.username), None)
    if mine is not None:
        return {**{k: getattr(mine, k) for k in LIMITS}, "source": f"user {p.username}"}
    groups = [q for q in rows if q.principal_type == "group" and q.principal in (p.groups or [])]
    if groups:
        out = {}
        for k in LIMITS:
            vals = [getattr(q, k) for q in groups]
            out[k] = None if any(v is None for v in vals) else max(vals)     # the most generous
        return {**out, "source": "group " + ", ".join(sorted(q.principal for q in groups))}
    default = next((q for q in rows if q.principal_type == "default"), None)
    if default is not None:
        return {**{k: getattr(default, k) for k in LIMITS}, "source": "default"}
    return None


def usage(db: Session, username: str, exclude_env: int | None = None) -> dict:
    envs = [e for e in db.scalars(select(Environment).where(Environment.created_by == username,
                                                            Environment.status != "destroyed"))
            if e.id != exclude_env]
    groups = [g for e in envs for g in (e.spec or {}).get("host_groups", [])]
    return {**demand_of(groups), "environments": len(envs)}


def check(db: Session, p: Principal, groups: list[dict], exclude_env: int | None = None) -> None:
    """Refuse when one more environment asking for `groups` would go over the user's quota."""
    lim = limits_for(db, p)
    if lim is None:
        return
    have = usage(db, p.username, exclude_env)
    want = {**demand_of(groups), "environments": 1}
    over = []
    for k in LIMITS:
        if lim[k] is not None and have[USE[k]] + want[USE[k]] > lim[k]:
            over.append(f"{have[USE[k]] + want[USE[k]]} of {lim[k]} {LABELS[k]}")
    if over:
        raise QuotaError(f"Over your quota ({lim['source']}): this would use {', '.join(over)}. "
                         "Destroy an environment, make this one smaller, or ask an admin.")


def summary(db: Session, p: Principal) -> dict:
    lim = limits_for(db, p)
    return {"limits": lim, "usage": usage(db, p.username)}
