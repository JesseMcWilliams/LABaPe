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

from ..models import Environment, Secret
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

# One secret per rotated account (design §25), each linked to its own rotation
# policy; a deploy puts them over the vault's matching keys.
ENV_PASSWORD_VAULT_KEYS = {"windows_admin": "windows_bootstrap_admin_password",
                           "domain_admin": "domain_admin_password",
                           # A1: the domain's second admin (Ansible creates it at deploy). The members'
                           # are per VM (A2), in each VM's host variables, not in the vault.
                           "domain_admin2": "labape_recovery_domain_password"}
# A1 (Planning_Lists-Secrets-Settings.md): a second admin next to each Administrator, so each of the
# pair can set the other's password again when it's lost. Same name in the domain and on members.
RECOVERY_ADMIN = "labape-admin2"
PAIR = {"windows_admin": "windows_admin2", "windows_admin2": "windows_admin",
        "domain_admin": "domain_admin2", "domain_admin2": "domain_admin"}


def env_password_name(env_id: int, kind: str) -> str:
    return f"environment/{env_id}/{kind}"


def env_password_overrides(db: Session, env_id: int) -> dict[str, str]:
    """Vault key -> this environment's rotated password, for those that have one."""
    out = {}
    for kind, key in ENV_PASSWORD_VAULT_KEYS.items():
        value = get_secret(db, env_password_name(env_id, kind))
        if value:
            out[key] = value
    return out


def clear_env_passwords(db: Session, env_id: int) -> None:
    """When an environment is destroyed: its rotated passwords, its own accounts' passwords and any
    value a rotation was trying (S2 found accounts kept after a destroy)."""
    from sqlalchemy import select
    names = {env_password_name(env_id, kind) for kind in ENV_PASSWORD_VAULT_KEYS}
    names.add(env_password_name(env_id, "windows_admin2"))          # A1's environment-wide one, before A2
    for prefix in (f"environment/{env_id}/account/", f"environment/{env_id}/rotation-candidate/",
                   f"environment/{env_id}/vm/"):
        names |= set(db.scalars(select(Secret.name).where(Secret.name.startswith(prefix))))
    for name in sorted(names):
        if db.get(Secret, name) is not None:
            put_secret(db, name, "")


def split_legacy_passwords(db: Session) -> int:
    """Before §25 both passwords shared one JSON secret, environment/<id>/passwords.
    Split it into the per-account secrets, then drop it. Idempotent."""
    by_key = {v: k for k, v in ENV_PASSWORD_VAULT_KEYS.items()}
    done = 0
    for row in list(db.scalars(select(Secret).where(Secret.name.like("environment/%/passwords")))):
        env_id = row.name.split("/")[1]
        text = get_secret(db, row.name) or ""
        for key, value in (json.loads(text) if text.strip() else {}).items():
            if key in by_key and value:
                put_secret(db, f"environment/{env_id}/{by_key[key]}", value)
        if row.store != "builtin":
            from ..stores import open_store
            open_store(db, row.store).delete(row.name)
        db.delete(row)
        db.commit()
        done += 1
    return done

def new_windows_password() -> str:
    """20 characters with every class Windows' complexity rule asks for."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(_secrets.choice(alphabet) for _ in range(16))
    return core + _secrets.choice(string.ascii_uppercase) + _secrets.choice(string.ascii_lowercase) + \
        _secrets.choice(string.digits) + _secrets.choice("!#%+")


def env_targets(env: Environment, inv: dict, kind: str) -> list[str]:
    dcs = _group(inv, "domain_controller")
    windows = [h for h, v in _hosts(inv).items() if (v or {}).get("ansible_connection") == "winrm"]
    if kind in ("domain_admin", "domain_admin2"):
        return sorted(h for h in windows if h in dcs)
    return sorted(h for h in windows if h not in dcs)


def ensure_recovery_passwords(db: Session, env: Environment) -> list[str]:
    """A1: the domain's labape-admin2 password before the deploy that creates it (members' are per VM,
    A2: vm_passwords.host_vars makes them)."""
    groups = (env.spec or {}).get("host_groups", [])
    has_dc = any("domain_controller" in (g.get("roles") or []) for g in groups)
    if has_dc and not get_secret(db, env_password_name(env.id, "domain_admin2")):
        put_secret(db, env_password_name(env.id, "domain_admin2"), new_windows_password())
        return ["domain_admin2"]
    return []


def recovery_user(inv: dict, kind: str) -> str:
    """How the second admin signs in. Always the plain name: WinRM here is Basic authentication, which
    takes no domain-qualified form, and the domain's labape-admin2 only ever signs in on a domain controller,
    where the plain name is the domain account (as Administrator's is)."""
    return RECOVERY_ADMIN


def stored_password(db: Session, env: Environment, inv: dict, kind: str, vm: str) -> str:
    """The password LABaPe holds for `kind`'s account on `vm`: per VM for member servers' accounts (A2,
    with the environment-wide value of before as a fallback), per environment for the domain's."""
    from .. import vm_passwords
    if kind == "windows_admin":
        return (get_secret(db, vm_passwords.name(env.id, vm, "windows_admin")) or _hosts(inv)[vm].get("ansible_password", "")
                or get_secret(db, env_password_name(env.id, kind)) or "")
    if kind == "windows_admin2":
        return (get_secret(db, vm_passwords.name(env.id, vm, "windows_admin2"))
                or get_secret(db, env_password_name(env.id, kind)) or "")
    if kind == "domain_admin":
        return get_secret(db, env_password_name(env.id, kind)) or _hosts(inv)[vm].get("ansible_password", "")
    return get_secret(db, env_password_name(env.id, kind)) or ""


def store_password(db: Session, env: Environment, kind: str, vm: str, value: str) -> None:
    from .. import vm_passwords
    if kind in ("windows_admin", "windows_admin2"):
        put_secret(db, vm_passwords.name(env.id, vm, kind), value)
    else:
        put_secret(db, env_password_name(env.id, kind), value)


def _per_host(inv: dict, hosts: list[str], values: dict[str, dict]) -> dict:
    """The inventory with these hosts' variables set (values: host -> vars)."""
    return {**inv, "all": {**inv["all"], "hosts": {h: {**_hosts(inv)[h], **values.get(h, {})} for h in hosts}}}


def probe(inv: dict, hosts: list[str], user: str | None, password: str | dict[str, str]) -> Outcome:
    """win_ping on `hosts` as `user` (None: the inventory's own) with `password` (one, or one per host)."""
    pw = password if isinstance(password, dict) else {h: password for h in hosts}
    as_ = _per_host(inv, hosts, {h: {"ansible_password": pw[h], **({"ansible_user": user} if user else {})} for h in hosts})
    return _ansible(as_, ":".join(hosts), "ansible.windows.win_ping", {}, scrub=tuple(set(pw.values())), timeout=300)


def rotate_recovery_password(db: Session, env: Environment, kind: str, log) -> str:
    """A1: a new password for a second admin (windows_admin2 per member VM, A2; domain_admin2 for the
    domain), set while signed in as the Administrator of the pair, so it needs no old value of its own."""
    if remote_env(env):
        raise AccessError("the environment is on a remote KVM host; the stack can't reach its VMs to change passwords")
    inv = _inventory(db, env)
    targets = env_targets(env, inv, kind)
    if not targets:
        return "no hosts to change in this environment"
    change_on = targets[:1] if kind == "domain_admin2" else targets
    one = new_windows_password()
    new = {h: one if kind == "domain_admin2" else new_windows_password() for h in targets}
    for h in change_on:
        put_secret(db, f"environment/{env.id}/rotation-candidate/{kind}" + ("" if kind == "domain_admin2" else f"/{h}"), new[h])
    module = "microsoft.ad.user" if kind == "domain_admin2" else "ansible.windows.win_user"
    r = _ansible(_per_host(inv, change_on, {h: {"labape_new_pw": new[h]} for h in change_on}), ":".join(change_on), module,
                 {"name": RECOVERY_ADMIN, "password": "{{ labape_new_pw }}", "update_password": "always"},
                 scrub=tuple(new.values()))
    failed = [h for h in change_on if r.get(h) not in ("ok", "changed")]
    if failed:
        raise AccessError(f"couldn't set {RECOVERY_ADMIN}'s new password on {', '.join(failed)}: {why(r, failed)}")
    check = probe(inv, targets, recovery_user(inv, kind), new)
    bad = [h for h in targets if check.get(h) not in ("ok", "changed")]
    if bad:
        raise AccessError(f"{RECOVERY_ADMIN} doesn't sign in with the new password on {', '.join(bad)} "
                          "(the attempted value is kept in the secret store)")
    for h in change_on:
        store_password(db, env, kind, h, new[h])
        put_secret(db, f"environment/{env.id}/rotation-candidate/{kind}" + ("" if kind == "domain_admin2" else f"/{h}"), "")
    log.write(f"labape: {env.name}: new {RECOVERY_ADMIN} password{'s' if len(change_on) > 1 else ''} on "
              f"{', '.join(change_on)}\n")
    return f"{len(change_on)} host(s) updated"


def reset_with_pair(db: Session, env: Environment, kind: str, log) -> str:
    """A1: set the stored password of `kind`'s account again, signed in as the other admin of its pair
    (each member VM with its own values, A2)."""
    inv = _inventory(db, env)
    other = PAIR[kind]
    targets = env_targets(env, inv, kind)
    if not targets:
        raise AccessError("no hosts use this account")
    change_on = targets[:1] if kind.startswith("domain") else targets
    stored = {h: stored_password(db, env, inv, kind, h) for h in change_on}
    if not all(stored.values()):
        raise AccessError("there's no stored password to set again")
    if other.endswith("2"):                    # Administrator lost: its second admin signs in
        other_pw = {h: stored_password(db, env, inv, other, h) for h in change_on}
        if not all(other_pw.values()):
            raise AccessError(f"this environment has no {RECOVERY_ADMIN} yet (a redeploy creates it)")
        login = {h: {"ansible_user": recovery_user(inv, other), "ansible_password": other_pw[h]} for h in change_on}
    else:                                      # the second admin lost: Administrator signs in, as LABaPe has it
        other_pw = {h: stored_password(db, env, inv, other, h) for h in change_on}
        login = {h: {"ansible_password": other_pw[h]} for h in change_on if other_pw[h]}
    account = RECOVERY_ADMIN if kind.endswith("2") else "Administrator"
    module = "microsoft.ad.user" if kind.startswith("domain") else "ansible.windows.win_user"
    values = {h: {**login.get(h, {}), "labape_new_pw": stored[h]} for h in change_on}
    r = _ansible(_per_host(inv, change_on, values), ":".join(change_on), module,
                 {"name": account, "password": "{{ labape_new_pw }}", "update_password": "always"},
                 scrub=tuple(x for x in (*stored.values(), *other_pw.values()) if x))
    bad = [h for h in change_on if r.get(h) not in ("ok", "changed")]
    if bad:
        raise AccessError(f"the other admin couldn't set {account}'s password on {', '.join(bad)}: {why(r, bad)}")
    log.write(f"labape: {env.name}: {account}'s stored password set again by the other admin of its pair "
              f"on {', '.join(change_on)}\n")
    return f"{account}'s stored password set again by its pair"


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
    the change worked. Its result is therefore ignored, and signing in decides:
    the new password (or `expect`, per host) first, the old one if that never
    works. It runs in the same shell, not detached: a detached (async) process
    lost its access to the account database once the shell closed
    (Testing_Troubleshooting-Log.md, A2)."""
    target = expect or {h: new for h in hosts}
    _ansible(inv, ":".join(hosts), module, args, extra, scrub=(*olds.values(), *target.values()))
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


def _rotate_member_admins(db: Session, env: Environment, inv: dict, targets: list[str], log) -> str:
    """A2: each member server's Administrator gets a new password of its own. Each VM changes or not on
    its own; the ones that did are stored, the others keep theirs (and are reported)."""
    from .. import vm_passwords
    new = {h: new_windows_password() for h in targets}
    olds = {h: stored_password(db, env, inv, "windows_admin", h) for h in targets}
    for h in targets:                          # kept before anything changes
        put_secret(db, f"environment/{env.id}/rotation-candidate/windows_admin/{h}", new[h])
    state = _set_password(_per_host(inv, targets, {h: {"labape_new_pw": new[h]} for h in targets}), targets,
                          "ansible.windows.win_user",
                          {"name": "Administrator", "password": "{{ labape_new_pw }}", "update_password": "always"},
                          {}, None, olds, expect=new)
    changed = [h for h in targets if state[h] == "new"]
    for h in changed:
        _hosts(inv)[h]["ansible_password"] = new[h]
        put_secret(db, vm_passwords.name(env.id, h, "windows_admin"), new[h])
        put_secret(db, f"environment/{env.id}/rotation-candidate/windows_admin/{h}", "")
    for h in targets:
        if state[h] == "old":
            put_secret(db, f"environment/{env.id}/rotation-candidate/windows_admin/{h}", "")
    put_secret(db, f"environment/{env.id}/inventory", yaml.safe_dump(inv, sort_keys=False))
    creds = get_secret(db, f"environment/{env.id}/credentials") or ""
    if creds:
        put_secret(db, f"environment/{env.id}/credentials", vm_passwords.apply_to_sheet(db, env, creds, targets))
    if changed:
        log.write(f"labape: {env.name}: a new Administrator password of its own on {', '.join(changed)}\n")
    unknown = [h for h in targets if state[h] == "unknown"]
    kept = [h for h in targets if state[h] == "old"]
    if unknown:
        raise AccessError(f"check {', '.join(unknown)} by hand: neither the old nor the new password signs in (the "
                          "attempted ones are in the secret store under rotation-candidate)")
    if kept:
        raise AccessError(f"couldn't change it on {', '.join(kept)} (they keep theirs); changed on "
                          f"{', '.join(changed) or 'none'}")
    return f"{len(changed)} host(s) updated, each with its own"


def rotate_env_password(db: Session, env: Environment, kind: str, log) -> str:
    """kind: windows_admin (local Administrator on members) or domain_admin (on a DC)."""
    if remote_env(env):
        raise AccessError("the environment is on a remote KVM host; the stack can't reach its VMs to change passwords")
    inv = _inventory(db, env)
    targets = env_targets(env, inv, kind)
    if not targets:
        return "no hosts to change in this environment"
    if kind == "windows_admin":
        return _rotate_member_admins(db, env, inv, targets, log)
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
    put_secret(db, env_password_name(env.id, kind), new)
    put_secret(db, candidate, "")
    log.write(f"labape: {env.name}: new {kind.replace('_', ' ')} password on {', '.join(change_on)}\n")
    return f"{len(change_on)} host(s) updated"
