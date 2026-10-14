"""A unique password per VM for its local accounts (A2, Planning_Lists-Secrets-Settings.md).

Each Windows member server's Administrator and labape-admin2, and each of the environment's own local
accounts on each VM, get their own password, kept per VM:

  environment/<id>/vm/<vm>/windows_admin            Administrator, once the VM has it
  environment/<id>/vm/<vm>/windows_admin.pending    the one a deploy is giving it
  environment/<id>/vm/<vm>/windows_admin2           labape-admin2 (A1's recovery pair)
  environment/<id>/vm/<vm>/account/<name>           a local account of the environment's own (E3)

A new VM starts with the environment's bootstrap password (the template's answer file); the deploy
then gives each its own: LABaPe writes the values into each VM's Ansible host variables, the
windows_common role changes Administrator's password and goes on signing in with it, and leaves a
marker (labape-applied/<vm>) once it works, after which the pending value becomes the VM's. A VM
without a marker keeps its old password, so nothing is stored that the VM doesn't have.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from .models import Environment
from .naming import instance_names
from .security import get_secret, put_secret

APPLIED_DIR = "labape-applied"            # in the engine workspace; one empty file per VM that took its password
SHEET_SECTION = "## Windows Administrator, per VM"


def name(env_id: int, vm: str, kind: str) -> str:
    return f"environment/{env_id}/vm/{vm}/{kind}"


def _groups(env: Environment) -> list[dict]:
    return (env.spec or {}).get("host_groups", [])


def member_vms(env: Environment) -> list[str]:
    """Windows VMs that aren't domain controllers: they have local accounts."""
    return [n for g in _groups(env) if str(g.get("os", "")).startswith("windows")
            and "domain_controller" not in (g.get("roles") or []) for n in instance_names(g)]


def local_account_vms(env: Environment, account: dict) -> list[str]:
    roles = set(account.get("hosts") or [])
    return [n for g in _groups(env) if roles & set(g.get("roles") or [])
            and "domain_controller" not in (g.get("roles") or []) for n in instance_names(g)]


def admin_password(db: Session, env: Environment, vm: str) -> str:
    """The Administrator password a member VM has: its own, or else the environment's (bootstrap or
    rotated before A2)."""
    from .engine.vmaccess import env_password_name
    return (get_secret(db, name(env.id, vm, "windows_admin")) or get_secret(db, env_password_name(env.id, "windows_admin"))
            or "")


def host_vars(db: Session, env: Environment) -> dict[str, dict]:
    """Per VM, the variables a deploy needs (made where missing): Administrator's own password (the one
    it has, or a pending new one), labape-admin2's, and the local accounts'."""
    from . import env_accounts
    from .engine.vmaccess import new_windows_password
    out: dict[str, dict] = {}
    for vm in member_vms(env):
        v = out.setdefault(vm, {})
        have = get_secret(db, name(env.id, vm, "windows_admin")) or ""
        if have:
            v["ansible_password"] = have                 # it has its own already: sign in with it
            v["labape_admin_password"] = have
        else:
            pending = get_secret(db, name(env.id, vm, "windows_admin.pending")) or ""
            if not pending:
                pending = new_windows_password()
                put_secret(db, name(env.id, vm, "windows_admin.pending"), pending)
            v["labape_admin_password"] = pending       # signs in with the bootstrap one, then changes it
        admin2 = get_secret(db, name(env.id, vm, "windows_admin2")) or ""
        if not admin2:
            admin2 = new_windows_password()
            put_secret(db, name(env.id, vm, "windows_admin2"), admin2)
        v["labape_recovery_local_password"] = admin2
    for a in env_accounts.accounts(env):
        if a["kind"] != "local":
            continue
        for vm in local_account_vms(env, a):
            pw = get_secret(db, name(env.id, vm, f"account/{a['name']}")) or ""
            if not pw:
                pw = new_windows_password()
                put_secret(db, name(env.id, vm, f"account/{a['name']}"), pw)
            out.setdefault(vm, {}).setdefault("labape_host_passwords", {})[a["name"]] = pw
    return out


def write_host_vars(db: Session, env: Environment, work: Path) -> int:
    """Merge the per-VM passwords into the workspace's inventory host_vars (0600); returns how many VMs."""
    hv = work / "ansible" / "inventory" / "host_vars"
    hv.mkdir(parents=True, exist_ok=True)
    values = host_vars(db, env)
    for vm, v in values.items():
        f = hv / f"{vm}.yml"
        data = (yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else None) or {}
        data.update(v)
        f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.chmod(f, 0o600)
    (work / APPLIED_DIR).mkdir(exist_ok=True)
    for old in (work / APPLIED_DIR).iterdir():
        old.unlink()
    return len(values)


def promote(db: Session, env: Environment, work: Path) -> list[str]:
    """The VMs whose deploy left a marker: their pending Administrator password is theirs now."""
    done = []
    d = work / APPLIED_DIR
    for marker in sorted(d.iterdir()) if d.is_dir() else []:
        vm = marker.name
        pending = get_secret(db, name(env.id, vm, "windows_admin.pending")) or ""
        if pending:
            put_secret(db, name(env.id, vm, "windows_admin"), pending)
            put_secret(db, name(env.id, vm, "windows_admin.pending"), "")
            done.append(vm)
        marker.unlink()
    return done


def apply_to_inventory(db: Session, env: Environment, inv_text: str) -> str:
    """The stored inventory with each member VM's own Administrator password."""
    inv = yaml.safe_load(inv_text) if inv_text.strip() else None
    if not inv:
        return inv_text
    hosts = (inv.get("all") or {}).get("hosts") or {}
    for vm in member_vms(env):
        own = get_secret(db, name(env.id, vm, "windows_admin")) or ""
        if own and vm in hosts:
            hosts[vm]["ansible_password"] = own
    return yaml.safe_dump(inv, sort_keys=False)


def apply_to_sheet(db: Session, env: Environment, text: str, vms: list[str] | None = None) -> str:
    """The credentials sheet's Windows section pointing at a per-VM list, with that list (`vms`: the
    member VMs, by default from the spec)."""
    vms = member_vms(env) if vms is None else vms
    if not vms or not text:
        return text
    lines, out, skip, section = text.splitlines(), [], False, ""
    for line in lines:
        if line.startswith("## "):
            section = line
            skip = line.startswith(SHEET_SECTION)
        if skip:
            continue
        if section.startswith("## Windows (") and line.startswith("Password: "):
            line = "Password: member servers have their own, below (domain controllers: the domain administrator's)"
        out.append(line)
    rows = [SHEET_SECTION, ""] + [f"{vm}: {admin_password(db, env, vm)}" for vm in vms] + [""]
    return "\n".join(out).rstrip("\n") + "\n\n" + "\n".join(rows)
