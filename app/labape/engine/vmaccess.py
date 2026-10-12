"""Access to lab VMs for secret rotation (design §24, phases R2 and R4).

- push_vm_key: the update agent's VM SSH key rotation pushes the new public
  key to every Linux VM of the deployed environments (with the current or a
  previous key), checks the new key logs in, and removes keys older than the
  window. Windows VMs are reached over WinRM with passwords, not this key.
- rotate_env_password: an environment's Windows Administrator (members) or
  domain Administrator (on the domain controller) gets a new password; the
  stored inventory and the environment's password overlay follow.
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import string
import subprocess
import tempfile
import time
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Environment
from ..security import get_secret, put_secret
from . import hostio

KEEP = 7                                  # previous VM keys still accepted (question 90)


class AccessError(RuntimeError):
    pass


def _inventory(db: Session, env: Environment) -> dict:
    text = get_secret(db, f"environment/{env.id}/inventory") or ""
    return yaml.safe_load(text) or {} if text else {}


def _hosts(inv: dict) -> dict[str, dict]:
    return (inv.get("all") or {}).get("hosts") or {}


def _group(inv: dict, name: str) -> set[str]:
    return set((((inv.get("all") or {}).get("children") or {}).get(name) or {}).get("hosts") or {})


class Outcome(dict):
    """{host: ok|changed|failed|unreachable}, with each failure's message in .errors."""
    errors: dict


def why(result: dict, hosts) -> str:
    errors = getattr(result, "errors", {})
    return "; ".join(f"{h}: {errors.get(h) or result.get(h, 'no result')}" for h in hosts)


def _ansible(inv: dict, pattern: str, module: str, args: dict, extra: dict | None = None,
             env: dict | None = None, timeout: int = 600, scrub: tuple = (), task: dict | None = None) -> Outcome:
    """Run one module on the hosts through a generated playbook (variables in a
    0600 file, never on the command line); the PLAY RECAP gives each host's result."""
    with tempfile.TemporaryDirectory() as d:
        inv_file, vars_file, play = Path(d) / "inventory.yml", Path(d) / "vars.yml", Path(d) / "play.yml"
        inv_file.write_text(yaml.safe_dump(inv), encoding="utf-8")
        os.chmod(inv_file, 0o600)
        vars_file.write_text(yaml.safe_dump(extra or {}), encoding="utf-8")
        os.chmod(vars_file, 0o600)
        play.write_text(yaml.safe_dump([{"name": "labape", "hosts": pattern, "gather_facts": False,
                                         "tasks": [{"name": module, module: args, **(task or {})}]}]),
                        encoding="utf-8")
        run_env = {**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False", "ANSIBLE_NOCOLOR": "1",
                   "ANSIBLE_FORCE_COLOR": "0", "ANSIBLE_STDOUT_CALLBACK": "default",
                   "HOME": os.environ.get("HOME", d), **(env or {})}
        r = subprocess.run(["ansible-playbook", "-i", str(inv_file), "-e", f"@{vars_file}", str(play)],
                           capture_output=True, text=True, timeout=timeout, env=run_env, check=False)
    secrets = [x for x in (*scrub, *((extra or {}).get(k) for k in ("new_pw", "ansible_password"))) if x]
    text = r.stdout
    for x in secrets:
        text = text.replace(str(x), "***")
    out = Outcome()
    out.errors = {}
    for m in re.finditer(r"^fatal: \[([^\]]+)\]: (?:FAILED|UNREACHABLE)! => (.*)$", text, re.M):
        msg = re.search(r'"msg": "((?:[^"\\]|\\.)*)"', m.group(2))
        out.errors[m.group(1)] = (msg.group(1) if msg else m.group(2))[:300]
    for m in re.finditer(r"^(\S+)\s+:\s+ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", text, re.M):
        host, (_ok, changed, unreachable, failed) = m.group(1), map(int, m.groups()[1:])
        out[host] = "unreachable" if unreachable else "failed" if failed else "changed" if changed else "ok"
    if not out:
        tail = (r.stderr or text).strip()[-400:]
        for x in secrets:
            tail = tail.replace(str(x), "***")
        raise AccessError(f"ansible failed: {tail}")
    return out


def _linux(inv: dict) -> list[str]:
    return [h for h, v in _hosts(inv).items() if (v or {}).get("ansible_connection", "ssh") == "ssh"]


def remote_env(env: Environment) -> bool:
    """VMs on a remote KVM host sit on that host's networks, out of the stack's reach."""
    return bool(env.kvm_host and hostio.is_remote(env.kvm_host.libvirt_uri))


def push_vm_key(db: Session, new_key: Path, current_key: Path, history_dir: Path, log) -> dict:
    new_pub = new_key.with_name(new_key.name + ".pub").read_text(encoding="utf-8").strip()
    hist = sorted(history_dir.glob("*.pub"), key=lambda p: p.name, reverse=True) if history_dir.is_dir() else []
    expired = [p.read_text(encoding="utf-8").strip() for p in hist[KEEP - 1:]]
    others = [str(p.with_suffix("")) for p in hist]
    ssh_args = {"ANSIBLE_SSH_COMMON_ARGS": " ".join(f"-i {k}" for k in others)} if others else {}
    report: dict = {"environments": {}, "unreachable": []}
    for env in db.scalars(select(Environment).where(Environment.status == "deployed")):
        inv = _inventory(db, env)
        hosts = _linux(inv)
        if not hosts:
            continue
        if remote_env(env):
            report["environments"][env.name] = {"skipped": "on a remote KVM host; its VMs keep the previous keys"}
            log.write(f"labape: {env.name}: skipped (remote KVM host: the stack can't reach its VMs)\n")
            continue
        pattern = ":".join(hosts)
        log.write(f"labape: {env.name}: adding the new key on {', '.join(hosts)}\n")
        added = _ansible(inv, pattern, "ansible.builtin.lineinfile",
                         {"path": "~/.ssh/authorized_keys", "line": "{{ new_pub }}", "create": True, "mode": "0600"},
                         {"new_pub": new_pub, "ansible_ssh_private_key_file": str(current_key)}, ssh_args)
        reach = [h for h, s in added.items() if s in ("ok", "changed")]
        report["unreachable"] += [f"{env.name}/{h}" for h, s in added.items() if s == "unreachable"]
        if any(s == "failed" for s in added.values()):
            raise AccessError(f"{env.name}: adding the key failed: "
                              f"{why(added, [h for h, s in added.items() if s == 'failed'])}")
        if reach:
            check = _ansible(inv, ":".join(reach), "ansible.builtin.ping", {},
                             {"ansible_ssh_private_key_file": str(new_key)}, {"ANSIBLE_SSH_COMMON_ARGS": ""})
            bad = [h for h in reach if check.get(h) not in ("ok", "changed")]
            if bad:
                _ansible(inv, ":".join(reach), "ansible.builtin.lineinfile",
                         {"path": "~/.ssh/authorized_keys", "line": "{{ new_pub }}", "state": "absent"},
                         {"new_pub": new_pub, "ansible_ssh_private_key_file": str(current_key)}, ssh_args)
                raise AccessError(f"{env.name}: the new key doesn't log in ({why(check, bad)}); it was removed again")
            for pub in expired:
                _ansible(inv, ":".join(reach), "ansible.builtin.lineinfile",
                         {"path": "~/.ssh/authorized_keys", "line": "{{ old_pub }}", "state": "absent"},
                         {"old_pub": pub, "ansible_ssh_private_key_file": str(new_key)}, {"ANSIBLE_SSH_COMMON_ARGS": ""})
        report["environments"][env.name] = {"updated": reach, "unreachable": [h for h, s in added.items()
                                                                              if s == "unreachable"]}
        log.write(f"labape: {env.name}: new key works on {len(reach)} host(s)\n")
    return report


# --- lab environment passwords (R4) ----------------------------------------------------

def new_windows_password() -> str:
    """20 characters with every class Windows' complexity rule asks for."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(_secrets.choice(alphabet) for _ in range(16))
    return core + _secrets.choice(string.ascii_uppercase) + _secrets.choice(string.ascii_lowercase) + \
        _secrets.choice(string.digits) + _secrets.choice("!#%+")


def env_targets(env: Environment, inv: dict, kind: str) -> list[str]:
    dcs = _group(inv, "domain_controller")
    windows = [h for h, v in _hosts(inv).items() if (v or {}).get("ansible_connection") == "winrm"]
    if kind == "domain_admin":
        return sorted(h for h in windows if h in dcs)
    return sorted(h for h in windows if h not in dcs)


def update_sheet(text: str, kind: str, new: str) -> str:
    """Rewrite the Password line of the credentials sheet's matching section
    ("## Windows ..." or "## Domain administrator"); sheets from before R4
    have no domain section, so one is added."""
    want = "## Domain administrator" if kind == "domain_admin" else "## Windows"
    lines, section, done = text.splitlines(), "", False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line
        elif section.startswith(want) and line.startswith("Password: ") and not done:
            lines[i], done = f"Password: {new}", True
    if not done and kind == "domain_admin":
        lines += ["## Domain administrator", "User: Administrator (the domain's)", f"Password: {new}", ""]
    return "\n".join(lines) + "\n"


PROBE_SECONDS = 75                        # with a probe every 15 s: few failed sign-ins (lockout policies)


def _set_password(inv: dict, hosts: list[str], module: str, args: dict, extra: dict, new: str | None,
                  olds: dict[str, str], expect: dict[str, str] | None = None) -> dict[str, str]:
    """Change the password of the account Ansible signs in with, and find out
    whether it took: {host: new|old|unknown}.

    WinRM's Basic auth sends the password with every request, so a module that
    changes its own account's password fails on its next request even though
    the change worked. The change therefore runs detached (async, poll 0), and
    signing in decides: the new password (or `expect`, per host) first, the
    old one if that never works."""
    target = expect or {h: new for h in hosts}
    _ansible(inv, ":".join(hosts), module, args, extra, task={"async": 120, "poll": 0},
             scrub=(*olds.values(), *target.values()))
    state, left, deadline = {}, list(hosts), time.monotonic() + PROBE_SECONDS
    first = True
    while left:
        time.sleep(10 if first else 15)
        first = False
        probe = {**inv, "all": {**inv["all"], "hosts": {h: {**_hosts(inv)[h], "ansible_password": target[h]}
                                                        for h in left}}}
        r = _ansible(probe, ":".join(left), "ansible.windows.win_ping", {}, scrub=tuple(target.values()))
        for h in [h for h in left if r.get(h) in ("ok", "changed")]:
            state[h] = "new"
            left.remove(h)
        if time.monotonic() > deadline:
            break
    if left:
        probe = {**inv, "all": {**inv["all"], "hosts": {h: {**_hosts(inv)[h], "ansible_password": olds[h]}
                                                        for h in left}}}
        r = _ansible(probe, ":".join(left), "ansible.windows.win_ping", {}, scrub=tuple(olds.values()))
        for h in left:
            state[h] = "old" if r.get(h) in ("ok", "changed") else "unknown"
    return state


def rotate_env_password(db: Session, env: Environment, kind: str, log) -> str:
    """kind: windows_admin (local Administrator on members) or domain_admin (on a DC)."""
    if remote_env(env):
        raise AccessError("the environment is on a remote KVM host; the stack can't reach its VMs to change passwords")
    inv = _inventory(db, env)
    targets = env_targets(env, inv, kind)
    if not targets:
        return "no hosts to change in this environment"
    new = new_windows_password()
    if kind == "domain_admin":
        change_on = targets[:1]               # one DC changes it for the whole domain
        module = "microsoft.ad.user"
    else:
        change_on = targets
        module = "ansible.windows.win_user"
    olds = {h: _hosts(inv)[h].get("ansible_password", "") for h in targets}
    args = {"name": "Administrator", "password": "{{ new_pw }}", "update_password": "always"}
    # Kept before anything changes, so neither a crash nor an unclear outcome loses it.
    candidate = f"environment/{env.id}/rotation-candidate/{kind}"
    put_secret(db, candidate, new)
    state = _set_password(inv, change_on, module, args, {"new_pw": new}, new, olds)
    changed = [h for h in change_on if state[h] == "new"]
    unknown = [h for h in change_on if state[h] == "unknown"]
    if len(changed) < len(change_on):
        # All or nothing: one password per account keeps the stored one (and redeploys) right.
        if changed:
            back = {**inv, "all": {**inv["all"], "hosts": {h: {**_hosts(inv)[h], "ansible_password": new,
                                                                "labape_old_pw": olds[h]} for h in changed}}}
            undo = _set_password(back, changed, module, {**args, "password": "{{ labape_old_pw }}"}, {},
                                 None, {h: new for h in changed}, expect=olds)
            unknown += [h for h in changed if undo[h] != "new"]
        msg = f"couldn't change the password on {', '.join(h for h in change_on if h not in changed)}"
        if unknown:
            raise AccessError(f"{msg}; check {', '.join(unknown)} by hand: neither the old nor the new password "
                              f"signs in (the attempted one is in the secret store as {candidate})")
        put_secret(db, candidate, "")
        raise AccessError(msg + ("; the others were put back" if changed else "; nothing changed"))
    ok = targets
    # The stored inventory, the owners' credentials sheet and this environment's
    # overlay follow, so people and later jobs use the new password.
    for h in ok:
        _hosts(inv)[h]["ansible_password"] = new
    put_secret(db, f"environment/{env.id}/inventory", yaml.safe_dump(inv, sort_keys=False))
    creds = get_secret(db, f"environment/{env.id}/credentials") or ""
    if creds:
        put_secret(db, f"environment/{env.id}/credentials", update_sheet(creds, kind, new))
    over = json.loads(get_secret(db, f"environment/{env.id}/passwords") or "{}")
    if kind == "domain_admin":
        over["domain_admin_password"] = new
    else:
        over["windows_bootstrap_admin_password"] = new
    put_secret(db, f"environment/{env.id}/passwords", json.dumps(over))
    put_secret(db, candidate, "")
    log.write(f"labape: {env.name}: new {kind.replace('_', ' ')} password on {', '.join(change_on)}\n")
    return f"{len(change_on)} host(s) updated"
