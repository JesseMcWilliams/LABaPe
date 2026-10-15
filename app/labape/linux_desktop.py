"""Desktop sessions on Linux VMs (E3, question 168; design §50.5).

A Linux host group can ask for `remote_desktop: [xrdp, vnc]`. xrdp signs anyone in through PAM (local
and domain accounts, over RDP like Windows); VNC gives each local account of the environment on that VM
a TigerVNC display of its own (:1, :2, ... in name order) whose password is the account's own
(A2: `environment/<id>/vm/<vm>/account/<name>`). LABaPe writes what each VM needs into its Ansible host
variables before a deploy (the `linux_remote_desktop` role) and tells the Connect menu what's there.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from .models import Environment
from .naming import instance_names

KINDS = ("xrdp", "vnc")
VNC_BASE = 5900


def _groups(env: Environment) -> dict[str, dict]:
    return {n: g for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}


def desktops(env: Environment, vm: str) -> list[str]:
    """What a Linux VM offers: xrdp and/or vnc."""
    g = _groups(env).get(vm) or {}
    if str(g.get("os", "")).startswith("windows"):
        return []
    return [k for k in KINDS if k in (g.get("remote_desktop") or [])]


def vnc_accounts(env: Environment, vm: str) -> list[str]:
    """The local accounts with a VNC display on `vm`, in display order (:1 first)."""
    from . import env_accounts, vm_passwords
    if "vnc" not in desktops(env, vm):
        return []
    return sorted(a["name"] for a in env_accounts.accounts(env)
                  if a["kind"] == "local" and vm in vm_passwords.local_account_vms(env, a))


def vnc_display(env: Environment, vm: str, user: str) -> int | None:
    names = vnc_accounts(env, vm)
    return names.index(user) + 1 if user in names else None


def host_vars(db: Session, env: Environment) -> dict[str, dict]:
    from . import vm_passwords
    from .security import get_secret
    out = {}
    for vm in _groups(env):
        kinds = desktops(env, vm)
        if not kinds:
            continue
        users = [{"name": n, "display": i + 1,
                  "password": get_secret(db, vm_passwords.name(env.id, vm, f"account/{n}")) or ""}
                 for i, n in enumerate(vnc_accounts(env, vm))]
        users = [u for u in users if u["password"]]
        ports = (["3389"] if "xrdp" in kinds else []) + [str(VNC_BASE + u["display"]) for u in users]
        out[vm] = {"labape_remote_desktop": kinds, "labape_vnc_users": users, "labape_desktop_ports": ports}
    return out


def write_host_vars(db: Session, env: Environment, work: Path) -> int:
    """Merge each VM's desktop settings into the workspace's host_vars (0600: VNC passwords)."""
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
