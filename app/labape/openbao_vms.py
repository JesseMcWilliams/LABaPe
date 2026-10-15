"""VMs reading their environment's secrets themselves (V2, question 165 a; design §49.3).

Each VM signs in to OpenBao with the platform certificate it already has (its CN: `<vm>.<domain>`, or
`<vm>` without a domain) on OpenBao's VM listener (`vm_url`, port 8210), and reads:
  - its own folder, `env/<environment>/vm/<vm>/` (what owners store for that VM), and
  - exactly the secrets in `env/<environment>/user/` whose VM list names it (R21: a secret shared by
    app1 and app2 is readable by those two only; the list is kept in LABaPe's catalog, `Secret.vms`).
The policies and certificate roles are written by `stores.sh vm-policies` with the root token from a
fixed template when the update agent asks (LABaPe can't write either); LABaPe sends the whole set,
and asks again whenever it changes. On the VM, `labape-secret get <name>` (Linux, as root) or
`Get-LabapeSecret <name>` (Windows, as an administrator) reads one.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings, update
from .models import Environment, Secret, Setting
from .naming import instance_names
from .stores import layout

REQUESTED = "openbao_vms.requested"


def enabled(db: Session) -> bool:
    cfg = appsettings.get(db, "store_openbao", cached=True)
    return bool(cfg.enabled and cfg.vm_url)


def role_name(env: Environment, vm: str) -> str:
    return f"labape-vm-{env.name}-{vm.lower()}"


def _domain(db: Session, env: Environment) -> str:
    from .engine.vmaccess import _inventory
    return str(((_inventory(db, env).get("all") or {}).get("vars") or {}).get("domain_name") or "")


def cn(db: Session, env: Environment, vm: str) -> str:
    d = _domain(db, env)
    return f"{vm}.{d}" if d else vm


def _vms(env: Environment) -> list[str]:
    return [n for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)]


def desired(db: Session) -> list[dict]:
    """Every deployed OpenBao environment's VMs, each with the shared secrets it may read."""
    from .security import env_store
    out = []
    for env in db.scalars(select(Environment).where(Environment.status == "deployed").order_by(Environment.name)):
        if env_store(db, env.id) not in layout.LAYOUT_STORES:
            continue
        prefix = f"environment/{env.id}/{layout.USER}"
        shared = [(row.name[len(prefix):], set(row.vms or [])) for row in
                  db.scalars(select(Secret).where(Secret.name.startswith(prefix)))]
        for vm in _vms(env):
            out.append({"env": env.name, "vm": vm, "cn": cn(db, env, vm),
                        "user": sorted(name for name, vms in shared if vm in vms)})
    return out


def sync(db: Session) -> list[str]:
    """Ask the update agent for the VMs' policies when they differ from what was last asked for."""
    if not enabled(db):
        return []
    want = desired(db)
    digest = hashlib.sha256(json.dumps(want, sort_keys=True).encode()).hexdigest()
    row = db.get(Setting, REQUESTED)
    if row is not None and (row.value or {}).get("digest") == digest:
        return []
    if not update.agent_installed():
        return ["the update agent isn't installed: run stores/stores.sh vm-policies with the VMs' list"]
    if update.busy():
        return ["the update agent is busy; asking for the VM policies at the next sync"]
    update.write_request("openbao_vms", "", "system", vms=want)
    if row is None:
        db.add(Setting(key=REQUESTED, value={"digest": digest}))
    else:
        row.value = {"digest": digest}
    db.commit()
    return [f"asked the update agent for {len(want)} VM(s)' policies"]


def forget(db: Session) -> None:
    """Ask again at the next sync (after a failed agent run, or by an admin)."""
    row = db.get(Setting, REQUESTED)
    if row is not None:
        db.delete(row)
        db.commit()


def host_vars(db: Session, env: Environment) -> dict[str, dict]:
    from .security import env_store
    if not enabled(db) or env_store(db, env.id) not in layout.LAYOUT_STORES:
        return {}
    cfg = appsettings.get(db, "store_openbao", cached=True)
    return {vm: {"labape_secret_client": {"url": cfg.vm_url.rstrip("/"), "role": role_name(env, vm), "mount": cfg.mount,
                                          "env": env.name, "vm": vm}} for vm in _vms(env)}


def write_host_vars(db: Session, env: Environment, work: Path) -> int:
    hv = work / "ansible" / "inventory" / "host_vars"
    values = host_vars(db, env)
    if values:
        hv.mkdir(parents=True, exist_ok=True)
    for vm, v in values.items():
        f = hv / f"{vm}.yml"
        data = (yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else None) or {}
        data.update(v)
        f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.chmod(f, 0o600)
    return len(values)
