"""The environment's mail server (M1, question 169; design §50.6): Mailpit on the Linux VMs whose host
group has the catalog service `mail_server`.

Every VM of the environment can send to it on port 25; it keeps the messages and shows them in its web
UI (8025, HTTPS with the VM's platform certificate) to user `mailpit` with a password LABaPe generates
and keeps (`environment/<id>/vm/<vm>/mailpit`, on the environment's Secrets). With Settings → Lab mail
relay on, messages to the allowed recipients are forwarded through that account; nothing else leaves.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets as _secrets
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from . import appsettings
from .models import Environment
from .naming import instance_names

SERVICE = "mail_server"
SMTP_PORT, UI_PORT = 25, 8025
RELAY_PASSWORD = "settings/lab_mail_relay_password"


def vms(db: Session, env: Environment) -> list[str]:
    """The VMs running the mail server: Linux groups with the service (directly or in a bundle)."""
    from .catalog import expand
    out = []
    for g in (env.spec or {}).get("host_groups", []):
        if str(g.get("os", "")).startswith("windows"):
            continue
        chosen = expand(db, g.get("services", []), [], [])
        if SERVICE in chosen["services"]:
            out += instance_names(g)
    return out


def ui_secret(env_id: int, vm: str) -> str:
    return f"environment/{env_id}/vm/{vm}/mailpit"


def htpasswd_sha(user: str, password: str) -> str:
    """An htpasswd line Mailpit reads ({SHA}: base64 of the SHA-1 digest)."""
    return f"{user}:{{SHA}}" + base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()


def relay_matching(allow: list[str]) -> str:
    """Mailpit's --smtp-relay-matching: the recipients that are forwarded."""
    parts = [(".*" + re.escape(a)) if a.startswith("@") else re.escape(a) for a in allow]
    return "^(" + "|".join(parts) + ")$" if parts else ""


def host_vars(db: Session, env: Environment) -> dict[str, dict]:
    from .security import get_secret, put_secret
    names = vms(db, env)
    if not names:
        return {}
    relay = appsettings.get(db, "lab_mail_relay")
    relay_vars = {}
    if relay.enabled and relay.host and relay.allow:
        relay_vars = {"host": relay.host, "port": relay.port, "starttls": relay.starttls,
                      "username": relay.username, "password": get_secret(db, RELAY_PASSWORD) or "",
                      "matching": relay_matching(relay.allow)}
    out = {}
    for vm in names:
        pw = get_secret(db, ui_secret(env.id, vm)) or ""
        if not pw:
            pw = _secrets.token_urlsafe(18)
            put_secret(db, ui_secret(env.id, vm), pw)
        out[vm] = {"labape_mailpit_ui_auth": htpasswd_sha("mailpit", pw), "labape_mailpit_relay": relay_vars}
    return out


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


def info(db: Session, env: Environment) -> list[dict]:
    """For the environment page: each mail server's addresses."""
    from .engine.vmaccess import _hosts, _inventory
    names = vms(db, env)
    if not names:
        return []
    hosts = _hosts(_inventory(db, env))
    relay = appsettings.get(db, "lab_mail_relay", cached=True)
    out = []
    for vm in names:
        addr = str((hosts.get(vm) or {}).get("ansible_host") or "")
        out.append({"vm": vm, "address": addr, "smtp": f"{addr or vm}:{SMTP_PORT}",
                    "ui": f"https://{addr or vm}:{UI_PORT}/", "user": "mailpit",
                    "relay": relay.allow if relay.enabled else []})
    return out
