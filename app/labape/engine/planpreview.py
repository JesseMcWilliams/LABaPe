"""A redeploy's plan preview (design §56.1): which VMs a redeploy would replace, create or remove.

The `environment.plan` job runs deploy.sh with --plan-only on each of the environment's KVM hosts
(one part per host for a spread environment, design §45): OpenTofu plans and nothing is applied.
Each part writes scripts/lib/plan_summary.py's JSON; the merged result is kept on the job
(params["plan"]) and the newest one is shown in the Redeploy dialog.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Environment, Job, utcnow

JOB_TYPE = "environment.plan"
PLAN_FILE = "plan.summary.json"

# What each VM trigger means to a person (tofu/modules/vm/libvirt/main.tf).
TRIGGERS = {
    "name": "name", "libvirt_uri": "KVM host", "iso_host_path": "OS image", "template_path": "template",
    "bridge_device": "network", "cpu_count": "CPU", "memory_mb": "memory", "disk_gb": "disk size",
    "vm_storage_path": "storage", "answer_file_md5": "install answers", "seed_md5": "install answers",
}


def reasons(changed: list[str]) -> list[str]:
    """The changed triggers in words, each once, in the module's order."""
    out: list[str] = []
    for key in changed:
        word = TRIGGERS.get(key, key)
        if word not in out:
            out.append(word)
    return out


def merge(works: list[Path]) -> dict:
    """Every part's summary in one (a part that wrote none fails the merge)."""
    vms: list[dict] = []
    other = 0
    for work in works:
        f = work / PLAN_FILE
        if not f.is_file():
            raise RuntimeError(f"a part's plan is missing ({work.parent.name})")
        part = json.loads(f.read_text(encoding="utf-8"))
        for vm in part.get("vms") or []:
            vms.append({"name": str(vm.get("name", "")), "action": str(vm.get("action", "none")),
                        "reasons": reasons([str(k) for k in vm.get("changed") or []])})
        other += int(part.get("other_changes") or 0)
        f.unlink()
    vms.sort(key=lambda v: v["name"])
    counts = {a: sum(1 for v in vms if v["action"] == a) for a in ("replace", "create", "delete", "update", "none")}
    return {"vms": vms, "counts": counts, "other_changes": other, "planned_at": utcnow().isoformat()}


def store(db: Session, job: Job, works: list[Path]) -> None:
    """Keep the merged plan on the job (the caller commits)."""
    job.params = {**(job.params or {}), "plan": merge(works)}


def latest(db: Session, env: Environment) -> dict | None:
    """The newest finished plan for an environment, with its job, and whether a deploy ran since."""
    job = db.scalar(select(Job).where(Job.environment_id == env.id, Job.type == JOB_TYPE,
                                      Job.state.in_(("succeeded", "failed", "cancelled")))
                    .order_by(Job.id.desc()).limit(1))
    if job is None:
        return None
    deployed_since = db.scalar(select(Job.id).where(Job.environment_id == env.id, Job.id > job.id,
                                                    Job.type.in_(("environment.deploy", "environment.destroy")))
                               .limit(1)) is not None
    return {"job_id": job.id, "state": job.state, "finished_at": job.finished_at,
            "plan": (job.params or {}).get("plan"), "stale": deployed_since}
