"""More accounts per environment (design §42, batch E3; question 113).

Beyond the built-in admin accounts, an environment's owners list extra accounts in its spec
(`accounts`): **domain** users (made on its domain controller, in `CN=Users` unless an OU is given)
and **local** users on the hosts of chosen roles (Windows and Linux). Each gets a generated password,
kept in the secret store (`environment/<id>/account/<name>`). A deploy adds them to the job's
directory manifest (the engine's existing directory objects, Design_Directory-Objects.md) with their
passwords laid over the vault, and lists them on the credentials sheet. Each has a rotation policy
(weekly by default, like the admin accounts, R4) that sets a new password on the VMs.

Taking an account off the list stops LABaPe managing it and forgets its password; it isn't deleted
from the VMs (the VM page's Accounts can do that).
"""
from __future__ import annotations

import re

import yaml
from sqlalchemy.orm import Session

from .models import Environment
from .naming import instance_names
from .security import get_secret, put_secret

NAME = re.compile(r"^[a-z][a-z0-9._-]{0,19}$")          # 20: the Windows (SAM) limit
RESERVED = {"administrator", "admin", "root", "labape", "guest", "krbtgt", "defaultaccount", "wdagutilityaccount",
            "nobody", "ansible"}
ROLES = ("domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation")
LINUX_ROLES = {"linux_server", "linux_workstation"}
SECTION = "## Environment accounts"


class AccountError(ValueError):
    pass


def secret_name(env_id: int, name: str) -> str:
    return f"environment/{env_id}/account/{name}"


def vault_key(name: str) -> str:
    return "labape_account_" + re.sub(r"[^a-z0-9_]", "_", name) + "_password"


def env_roles(env: Environment) -> set[str]:
    return {r for g in (env.spec or {}).get("host_groups", []) for r in (g.get("roles") or [])}


def accounts(env: Environment) -> list[dict]:
    return list((env.spec or {}).get("accounts") or [])


def validate(env: Environment, items: list[dict]) -> list[dict]:
    roles, seen, out = env_roles(env), set(), []
    for i, a in enumerate(items):
        name, kind = str(a.get("name", "")).strip(), a.get("kind")
        where = f"account {i + 1}" + (f" ({name})" if name else "")
        if not NAME.match(name) or name in RESERVED:
            raise AccountError(f"{where}: a name is a lowercase letter, then letters, digits, dots, dashes or "
                               "underscores, up to 20, and not a built-in account")
        if name in seen:
            raise AccountError(f"{where}: {name} is listed twice")
        seen.add(name)
        groups = [str(g)[:64] for g in (a.get("groups") or [])][:20]
        if kind == "domain":
            if "domain_controller" not in roles:
                raise AccountError(f"{where}: a domain account needs a domain controller in the environment")
            out.append({"name": name, "kind": "domain", "display_name": str(a.get("display_name", ""))[:64],
                        "ou": str(a.get("ou", ""))[:255], "groups": groups})
        elif kind == "local":
            hosts = [r for r in (a.get("hosts") or []) if r]
            # A domain controller has no local accounts (the engine makes none there).
            local_roles = (roles & set(ROLES)) - {"domain_controller"}
            bad = [r for r in hosts if r not in local_roles]
            if not hosts or bad:
                raise AccountError(f"{where}: choose the roles whose hosts get it, from this environment's: "
                                   f"{', '.join(sorted(local_roles)) or 'none (only domain controllers)'}")
            entry = {"name": name, "kind": "local", "hosts": hosts, "groups": groups}
            if a.get("sudo_nopasswd"):
                # Passwordless sudo (2026-10-20 round): Linux hosts only; Windows has no equivalent.
                if not set(hosts) & LINUX_ROLES:
                    raise AccountError(f"{where}: passwordless sudo is for accounts on Linux hosts")
                entry["sudo_nopasswd"] = True
            out.append(entry)
        else:
            raise AccountError(f"{where}: the kind is domain or local")
    return out


def set_accounts(db: Session, env: Environment, items: list[dict]) -> list[dict]:
    """Replace the list: new accounts get a password, removed ones lose theirs."""
    from .engine.vmaccess import new_windows_password
    new = validate(env, items)
    before = {a["name"] for a in accounts(env)}
    for a in new:
        if not get_secret(db, secret_name(env.id, a["name"])):
            put_secret(db, secret_name(env.id, a["name"]), new_windows_password())
    for name in before - {a["name"] for a in new}:
        put_secret(db, secret_name(env.id, name), "")
    env.spec = {**(env.spec or {}), "accounts": new}
    db.commit()
    return new


def merge_manifest(path, env: Environment, domain_name: str) -> int:
    """Add the environment's accounts to the job's directory manifest (written next to the
    playbooks); returns how many. The global manifest's own objects stay."""
    items = accounts(env)
    if not items:
        return 0
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
    dn = ",".join(f"DC={p}" for p in domain_name.split(".") if p) if domain_name else ""
    for a in items:
        if a["kind"] == "domain":
            u = {"name": a["name"], "ou": a.get("ou") or f"CN=Users,{dn}", "password_vault_key": vault_key(a["name"])}
            if a.get("display_name"):
                u["display_name"] = a["display_name"]
            if a.get("groups"):
                u["groups"] = a["groups"]
            data.setdefault("domain_users", []).append(u)
        else:
            u = {"name": a["name"], "hosts": a["hosts"], "password_vault_key": vault_key(a["name"])}
            if a.get("groups"):
                u["groups"] = a["groups"]
            if a.get("sudo_nopasswd"):
                u["sudo_nopasswd"] = True
            data.setdefault("local_users", []).append(u)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return len(items)


def vault_overrides(db: Session, env: Environment) -> dict[str, str]:
    return {vault_key(a["name"]): get_secret(db, secret_name(env.id, a["name"])) or "" for a in accounts(env)}


def sheet_section(db: Session, env: Environment, netbios: str = "") -> str:
    items = accounts(env)
    if not items:
        return ""
    from . import vm_passwords
    lines = [SECTION, ""]
    for a in items:
        if a["kind"] == "domain":
            who = f"{netbios}\\{a['name']}" if netbios else a["name"]
            lines += [f"User: {who} (domain)", f"Password: {get_secret(db, secret_name(env.id, a['name'])) or ''}", ""]
            continue
        # A local account has a password of its own on each VM (A2).
        lines += [f"User: {a['name']} (local on {', '.join(a['hosts'])}; each VM its own password)"]
        for vm in vm_passwords.local_account_vms(env, a):
            pw = (get_secret(db, vm_passwords.name(env.id, vm, f"account/{a['name']}"))
                  or get_secret(db, secret_name(env.id, a["name"])) or "")
            lines += [f"{vm}: {pw}"]
        lines += [""]
    return "\n".join(lines)


def apply_sheet(text: str, section: str) -> str:
    """The credentials sheet with its accounts section replaced (or added, or removed)."""
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            skip = line.startswith(SECTION)
        if not skip:
            out.append(line)
    body = "\n".join(out).rstrip("\n")
    return (body + "\n\n" + section).rstrip("\n") + "\n" if section else body + "\n"


def hosts_for(env: Environment, inv: dict, a: dict) -> list[str]:
    """The VMs an account lives on: the first domain controller for a domain one (the change
    reaches the whole domain), else the hosts of its roles."""
    from .engine.vmaccess import _group
    if a["kind"] == "domain":
        return sorted(_group(inv, "domain_controller"))[:1]
    return sorted({h for r in a["hosts"] for h in _group(inv, r)})


def rotate(db: Session, env: Environment, name: str, log) -> str:
    """A new password for one account, set on its VMs; stored only once every host took it."""
    from .engine.vmaccess import AccessError, _ansible, _hosts, _inventory, new_windows_password, remote_env
    a = next((x for x in accounts(env) if x["name"] == name), None)
    if a is None:
        raise AccessError(f"{name} isn't one of {env.name}'s accounts any more")
    if remote_env(env):
        raise AccessError("the environment is on a remote KVM host; the stack can't reach its VMs to change passwords")
    inv = _inventory(db, env)
    targets = hosts_for(env, inv, a)
    if not targets:
        return "no hosts to change in this environment"
    from . import vm_passwords
    from .engine.vmaccess import _per_host
    local = a["kind"] == "local"
    # A domain account has one password; a local one a password of its own on each VM (A2).
    one = new_windows_password()
    new = {h: new_windows_password() if local else one for h in targets}
    candidate = f"environment/{env.id}/rotation-candidate/account-{name}"
    put_secret(db, candidate, one if not local else "")
    inv2 = _per_host(inv, targets, {h: {"labape_new_pw": new[h]} for h in targets})
    windows = [h for h in targets if (_hosts(inv).get(h) or {}).get("ansible_connection") == "winrm"]
    linux = [h for h in targets if h not in windows]
    failed = []
    if windows:
        module = "microsoft.ad.user" if a["kind"] == "domain" else "ansible.windows.win_user"
        res = _ansible(inv2, ":".join(windows), module,
                       {"name": name, "password": "{{ labape_new_pw }}", "update_password": "always"},
                       scrub=tuple(new.values()))
        failed += [h for h in windows if res.get(h) not in ("ok", "changed")]
    if linux:
        res = _ansible(inv2, ":".join(linux), "ansible.builtin.user",
                       {"name": name, "password": "{{ labape_new_pw | password_hash('sha512') }}", "update_password": "always"},
                       scrub=tuple(new.values()), task={"become": True})
        failed += [h for h in linux if res.get(h) not in ("ok", "changed")]
    if local:
        for h in targets:
            if h not in failed:
                put_secret(db, vm_passwords.name(env.id, h, f"account/{name}"), new[h])
    if failed:
        raise AccessError(f"couldn't set {name}'s new password on {', '.join(failed)}; "
                          + ("the others have theirs, stored" if local else
                             f"the other hosts may already have it (it's in the secret store as {candidate}), "
                             "the stored one is unchanged"))
    if not local:
        put_secret(db, secret_name(env.id, name), one)
    creds = get_secret(db, f"environment/{env.id}/credentials") or ""
    if creds:
        put_secret(db, f"environment/{env.id}/credentials", apply_sheet(creds, sheet_section(db, env)))
    put_secret(db, candidate, "")
    log.write(f"labape: {env.name}: new password for {name} on {', '.join(targets)}\n")
    return f"{len(targets)} host(s) updated"


def vm_names(env: Environment) -> list[str]:
    return [n for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)]
