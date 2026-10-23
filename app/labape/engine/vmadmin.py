"""An environment's VM, looked at and managed from its details page (design §28.6,
questions 119-120).

- What the guest agent reports (OS, users logged in, filesystems, disks,
  interfaces) comes from the worker's host stats, with no login.
- Accounts and groups need a login: Linux over SSH as the environment's admin
  user with the VM key (it has passwordless sudo), reusing the KVM host
  collector and account commands; Windows over WinRM as the inventory's
  Administrator, through Ansible (local users and groups; a domain controller
  has no local accounts, so it reports that).
- Owners and admins may change accounts and groups (question 120).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..models import Environment
from ..naming import instance_names
from . import engine_secrets, hostadmin, hostio
from .vmaccess import _hosts, _inventory

WINDOWS_PROTECTED = {"Administrator", "Guest", "DefaultAccount", "WDAGUtilityAccount"}
WIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class VmAdminError(RuntimeError):
    pass


def _vm(db: Session, env: Environment, vm: str) -> tuple[dict, dict, dict]:
    """(group, inventory host vars, whole inventory) for one VM."""
    groups = {n: g for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}
    if vm not in groups:
        raise VmAdminError(f"{vm} isn't a VM of {env.name}")
    inv = _inventory(db, env)
    host = _hosts(inv).get(vm)
    if not host or not host.get("ansible_host"):
        raise VmAdminError(f"{vm} has no address yet")
    return groups[vm], host, inv


def is_windows(group: dict) -> bool:
    return str(group.get("os", "")).startswith("windows")


class VmSsh(hostadmin.Ssh):
    """SSH to a Linux VM as its admin user with the VM key (written to a 0600 temp file)."""

    def __init__(self, address: str, user: str, key_text: str):
        super().__init__(address, 22, user)
        self._dir = tempfile.TemporaryDirectory()
        self._key = Path(self._dir.name) / "vm_key"
        self._key.write_text(key_text if key_text.endswith("\n") else key_text + "\n", encoding="utf-8")
        os.chmod(self._key, 0o600)

    def base(self) -> list[str]:
        # Lab VMs are rebuilt with new host keys under old addresses: no known_hosts for them
        # (ssh keeps the first value of an option, so these come before SSH_OPTS).
        return ["ssh", "-i", str(self._key), "-o", "UserKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=no",
                *hostio.SSH_OPTS, f"{self.user}@{self.address}"]

    def close(self) -> None:
        self._dir.cleanup()


def _login(address: str, user: str, key_text: str) -> tuple[bool, bool]:
    """(logs in, was refused for its key) for a key on a VM."""
    ssh = VmSsh(address, user, key_text)
    try:
        r = ssh.run("true", timeout=30)
    except subprocess.TimeoutExpired:
        return False, False
    finally:
        ssh.close()
    return r.returncode == 0, b"Permission denied" in (r.stderr or b"")


def vm_key_text(db: Session, address: str, user: str) -> str:
    """The VM key to log in to a Linux VM with. A VM that was still deploying when the key was rotated never got
    the new one (it keeps an earlier key, which Ansible still tries); when the current key is refused, an earlier
    one that works is used to add the current key there, so the VM is back on it. Returns the current key when
    nothing works, and the caller reports the usual error."""
    cur = engine_secrets._get(db, engine_secrets.SSH_KEY)
    ok, refused = _login(address, user, cur)
    if ok or not refused:
        return cur
    pub = engine_secrets._get(db, engine_secrets.SSH_PUB).strip()
    for h in engine_secrets._history(db, engine_secrets.SSH_HISTORY):
        if not _login(address, user, h["private"])[0]:
            continue
        ssh = VmSsh(address, user, h["private"])
        try:
            line = shlex.quote(pub)
            r = ssh.run(f"grep -qxF {line} ~/.ssh/authorized_keys || echo {line} >> ~/.ssh/authorized_keys")
        finally:
            ssh.close()
        return cur if r.returncode == 0 and _login(address, user, cur)[0] else h["private"]
    return cur


def _linux_ssh(db: Session, host: dict) -> VmSsh:
    address, user = str(host["ansible_host"]), str(host.get("ansible_user") or "labape")
    return VmSsh(address, user, vm_key_text(db, address, user))


# --- Windows through Ansible (WinRM), with JSON results -------------------------------------

def _win(inv: dict, vm: str, task: dict, timeout: int = 180) -> dict:
    """Run one task on one Windows VM and return its result. The image has no JSON stdout
    callback, so a second task writes the registered result to a local 0600 file."""
    one = {"all": {"hosts": {vm: _hosts(inv)[vm]}, "vars": (inv.get("all") or {}).get("vars", {})}}
    pw = str(_hosts(inv)[vm].get("ansible_password") or "")

    def scrub(text: str) -> str:
        return text.replace(pw, "***") if pw else text

    with tempfile.TemporaryDirectory() as d:
        inv_file, play, out = Path(d) / "inventory.yml", Path(d) / "play.yml", Path(d) / "result.json"
        inv_file.write_text(yaml.safe_dump(one), encoding="utf-8")
        os.chmod(inv_file, 0o600)
        tasks = [{**task, "register": "labape_result", "ignore_errors": True},
                 {"name": "result", "delegate_to": "localhost", "no_log": True,
                  "ansible.builtin.copy": {"content": "{{ labape_result | to_json }}", "dest": str(out), "mode": "0600"},
                  "vars": {"ansible_connection": "local", "ansible_python_interpreter": "{{ ansible_playbook_python }}"}}]
        play.write_text(yaml.safe_dump([{"name": "labape", "hosts": vm, "gather_facts": False, "tasks": tasks}]),
                        encoding="utf-8")
        env = {**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False", "ANSIBLE_NOCOLOR": "1", "ANSIBLE_FORCE_COLOR": "0",
               "HOME": os.environ.get("HOME", d)}
        r = subprocess.run(["ansible-playbook", "-i", str(inv_file), str(play)], capture_output=True, text=True,
                           timeout=timeout, env=env, check=False)
        if not out.is_file():
            m = re.search(r'"msg": "((?:[^"\\]|\\.)*)"', r.stdout)
            raise VmAdminError(scrub(f"WinRM to {vm} failed: {m.group(1) if m else (r.stderr or r.stdout).strip()[-300:]}")[:400])
        res = json.loads(out.read_text(encoding="utf-8"))
    if res.get("failed") or res.get("unreachable"):
        raise VmAdminError(scrub(str(res.get("msg") or res.get("stderr") or "failed"))[:400])
    return res


WIN_READ = r'''
$ErrorActionPreference = 'Stop'
$dc = (Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4
if ($dc) { @{ domain_controller = $true } | ConvertTo-Json -Compress; return }
$users = @(Get-LocalUser | ForEach-Object { [pscustomobject]@{ name = $_.Name; enabled = $_.Enabled;
  comment = $_.Description; last_logon = if ($_.LastLogon) { $_.LastLogon.ToString('o') } else { $null } } })
$groups = @(Get-LocalGroup | ForEach-Object { $g = $_; [pscustomobject]@{ name = $g.Name; comment = $g.Description;
  builtin = $g.SID.Value -like 'S-1-5-32-*';
  members = @(Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }) } })
foreach ($u in $users) { $u | Add-Member groups @($groups | Where-Object { $_.members -match ('\\' + [regex]::Escape($u.name) + '$') } | ForEach-Object { $_.name }) }
@{ users = $users; groups = $groups } | ConvertTo-Json -Depth 5 -Compress
'''


def read_accounts(db: Session, env: Environment, vm: str) -> dict:
    """{"accounts": [...], "groups": [...]} in the KVM host sections' shape, or a note."""
    group, host, inv = _vm(db, env, vm)
    if is_windows(group):
        res = _win(inv, vm, {"name": "read", "ansible.windows.win_powershell": {"script": WIN_READ}})
        out = (res.get("output") or [None])[-1]
        data = json.loads(out) if isinstance(out, str) else out or {}
        if data.get("domain_controller"):
            return {"accounts": [], "groups": [], "note": "A domain controller has no local accounts: manage domain "
                                                          "users and groups in Active Directory."}
        return {"accounts": [{"name": u["name"], "comment": u.get("comment") or "", "locked": not u.get("enabled", True),
                              "groups": u.get("groups") or [], "admin": "Administrators" in (u.get("groups") or [])}
                             for u in data.get("users") or []],
                "groups": [{"name": g["name"], "comment": g.get("comment") or "", "builtin": bool(g.get("builtin")),
                            "members": [m.split("\\")[-1] for m in (g.get("members") or [])]} for g in data.get("groups") or []],
                "windows": True}
    ssh = _linux_ssh(db, host)
    try:
        facts = ssh.python(hostadmin.FACTS, {}, timeout=120)
    except hostadmin.AdminError as exc:
        raise VmAdminError(str(exc)) from exc
    finally:
        ssh.close()
    return {"accounts": facts.get("accounts", []), "groups": facts.get("groups", []), "windows": False}


def manage(db: Session, env: Environment, vm: str, op: str, name: str, **kw) -> None:
    group, host, inv = _vm(db, env, vm)
    if not is_windows(group):
        ssh = _linux_ssh(db, host)
        try:
            hostadmin.manage_over(ssh, hostadmin.PROTECTED | {str(host.get("ansible_user") or "labape")}, op, name, **kw)
        except hostadmin.AdminError as exc:
            raise VmAdminError(str(exc)) from exc
        finally:
            ssh.close()
        return
    if not WIN_NAME_RE.match(name):
        raise VmAdminError(f"{name!r} isn't a valid Windows user or group name")
    if op in ("user.delete", "user.lock") and name in WINDOWS_PROTECTED:
        raise VmAdminError(f"{name} is protected: LABaPe needs it")
    if op.startswith("group.member") and not WIN_NAME_RE.match(kw.get("member") or ""):
        raise VmAdminError("give a valid member name")
    groups = [g for g in (kw.get("groups") or []) if WIN_NAME_RE.match(g)]
    tasks = {
        "user.add": {"ansible.windows.win_user": {"name": name, "description": kw.get("comment") or "", "state": "present",
                                                  **({"password": kw["password"], "update_password": "on_create"} if kw.get("password") else {}),
                                                  **({"groups": groups, "groups_action": "add"} if groups else {})}},
        "user.delete": {"ansible.windows.win_user": {"name": name, "state": "absent"}},
        "user.lock": {"ansible.windows.win_user": {"name": name, "account_disabled": True}},
        "user.unlock": {"ansible.windows.win_user": {"name": name, "account_disabled": False}},
        "user.groups": {"ansible.windows.win_user": {"name": name, "groups": groups, "groups_action": "replace"}},
        "user.password": {"ansible.windows.win_user": {"name": name, "password": kw.get("password") or "",
                                                       "update_password": "always"}},
        "group.add": {"ansible.windows.win_group": {"name": name, "state": "present"}},
        "group.delete": {"ansible.windows.win_group": {"name": name, "state": "absent"}},
        "group.member_add": {"ansible.windows.win_group_membership": {"name": name, "members": [kw.get("member") or ""],
                                                                      "state": "present"}},
        "group.member_remove": {"ansible.windows.win_group_membership": {"name": name, "members": [kw.get("member") or ""],
                                                                         "state": "absent"}},
    }
    if op not in tasks:
        raise VmAdminError(f"unknown operation {op}")
    if op == "user.password" and not kw.get("password"):
        raise VmAdminError("give the new password")
    task = {"name": op, **tasks[op], "no_log": bool(kw.get("password"))}
    _win(inv, vm, task)
