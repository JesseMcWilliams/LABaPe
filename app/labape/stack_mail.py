"""The stack's own mail server, Mailpit (design §52).

Every stack runs Mailpit (container/compose.yaml `labape-mailpit`): it accepts mail from the stack on port
1025, keeps it, and shows it in a web page (Admin -> Mail opens it, behind Caddy at /mail/ for admins).
LABaPe's Settings -> Email points at it by default, so alerts and job emails are caught and can be read
without any mail account.

Forwarding mail outside is Mailpit's relay. Mailpit reads its relay options only at start, so LABaPe writes
them into a folder it shares with the Mailpit container (`LABAPE_MAILPIT_CONFIG`, volume
labape-mailpit-config) as `mailpit.env` (which options, plus a revision) and `relay.yaml` (the upstream
server and its login), and container/mailpit/supervise.sh restarts Mailpit within seconds. The revision is
a hash of what was written; Mailpit leaves the same one in `applied` once it runs with it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from sqlalchemy.orm import Session

from . import appsettings
from .security import get_secret

log = logging.getLogger(__name__)
PASSWORD_SECRET = "settings/stack_mail_password"


def config_dir() -> Path:
    return Path(os.environ.get("LABAPE_MAILPIT_CONFIG", "/var/lib/labape-mailpit"))


def available() -> bool:
    """This stack runs Mailpit (its compose file sets the address; a CLI or test install doesn't)."""
    return bool(os.environ.get("LABAPE_MAIL_HOST"))


def mailpit_url() -> str:
    return os.environ.get("LABAPE_MAILPIT_URL", "http://labape-mailpit:8025/mail").rstrip("/")


def relay_regex(allow: list[str]) -> str:
    """Mailpit's --smtp-relay-matching (Go syntax): a recipient that is one of the addresses or in one of the
    @domains, any case."""
    parts = [(re.escape(a) + "$") if a.startswith("@") else ("^" + re.escape(a) + "$") for a in sorted(set(allow))]
    return "(?i)(" + "|".join(parts) + ")" if parts else ""


def render(cfg: appsettings.StackMailSettings, password: str) -> tuple[str, str | None]:
    """(mailpit.env, relay.yaml or None) for these settings; the env's first line carries the revision."""
    relay = cfg.relay and bool(cfg.host)
    lines = [f"MP_MAX_MESSAGES={cfg.keep_messages}", f"MP_MAX_AGE={cfg.keep_days}d"]
    relay_yaml = None
    if relay:
        data: dict = {"host": cfg.host, "port": cfg.port}
        if cfg.security == "starttls":
            data["starttls"] = True
        elif cfg.security == "ssl":
            data["tls"] = True
        if not cfg.verify_tls:
            data["allow-insecure"] = True
        if cfg.username:
            data.update({"auth": cfg.auth, "username": cfg.username, "password": password})
        if cfg.override_from:
            data["override-from"] = cfg.override_from
        relay_yaml = yaml.safe_dump(data, sort_keys=False)
        lines.append("MP_SMTP_RELAY_CONFIG=/config/relay.yaml")
        if cfg.forward == "all":
            lines.append("MP_SMTP_RELAY_ALL=true")
        elif cfg.allow:
            lines.append(f"MP_SMTP_RELAY_MATCHING={relay_regex(cfg.allow)}")
    body = "\n".join(lines) + "\n"
    rev = hashlib.sha256((body + (relay_yaml or "")).encode()).hexdigest()[:12]
    return f"# Written by LABaPe (stack_mail.apply); Mailpit's supervisor reads it. Do not edit.\nLABAPE_REV={rev}\n{body}", relay_yaml


def _write(path: Path, text: str | None, mode: int = 0o600) -> bool:
    if text is None:
        if path.exists():
            path.unlink()
            return True
        return False
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def apply(db: Session) -> bool:
    """Write Mailpit's options from the settings; True when something changed (Mailpit restarts by itself)."""
    d = config_dir()
    if not d.is_dir():
        return False
    cfg = appsettings.get(db, "stack_mail")
    env, relay = render(cfg, get_secret(db, PASSWORD_SECRET) or "")
    changed = _write(d / "relay.yaml", relay)
    changed = _write(d / "mailpit.env", env, 0o644) or changed
    return changed


def wanted_revision() -> str:
    try:
        for line in (config_dir() / "mailpit.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("LABAPE_REV="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return ""


def applied_revision() -> str:
    try:
        return (config_dir() / "applied").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def email_defaults(current: appsettings.SmtpSettings) -> appsettings.SmtpSettings:
    """Settings -> Email pointed at the stack's Mailpit (keeping the From address and the job-mail switch)."""
    host = urlsplit(os.environ.get("LABAPE_PUBLIC_URL", "")).hostname or ""
    domain = host if host and not re.fullmatch(r"[0-9.:]+", host) else "labape.local"
    return current.model_copy(update={
        "enabled": True, "host": os.environ.get("LABAPE_MAIL_HOST", "labape-mailpit"),
        "port": int(os.environ.get("LABAPE_MAIL_PORT", "1025")), "security": "none", "username": "",
        "from_address": current.from_address if "@" in current.from_address else f"labape@{domain}"})


def uses_mailpit(cfg: appsettings.SmtpSettings) -> bool:
    return cfg.enabled and cfg.host == os.environ.get("LABAPE_MAIL_HOST", "labape-mailpit")


def startup(db: Session) -> list[str]:
    """At every start: Settings -> Email goes to Mailpit if nobody ever set it up (off, and no server named: a
    saved row of defaults counts as never set up), and Mailpit gets its options. A server an admin named, even
    if Email is off, is left alone."""
    done = []
    if not available():
        return done
    smtp = appsettings.get(db, "smtp")
    if not smtp.enabled and not smtp.host:
        appsettings.put(db, "smtp", email_defaults(smtp))
        done.append("Settings -> Email now sends through the stack's Mailpit")
    try:
        if apply(db):
            done.append("Mailpit's options written")
    except OSError as exc:
        log.warning("stack mail: couldn't write Mailpit's options: %s", exc)
    return done


def status(db: Session) -> dict:
    """What the Mail page shows: is Mailpit up, what it holds, whether it runs the latest options."""
    out = {"available": available(), "reachable": False, "version": "", "messages": 0, "unread": 0, "database_bytes": 0,
           "applied": False, "ui_path": "/mail/", "error": ""}
    if not available():
        out["error"] = "This install has no Mailpit (it is part of the container stack)."
        return out
    try:
        r = httpx.get(f"{mailpit_url()}/api/v1/info", timeout=3)
        r.raise_for_status()
        info = r.json()
        out.update(reachable=True, version=str(info.get("Version", "")), messages=int(info.get("Messages", 0)),
                   unread=int(info.get("Unread", 0)), database_bytes=int(info.get("DatabaseSize", 0)))
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = f"Mailpit doesn't answer: {exc}"
    want = wanted_revision()
    out["applied"] = bool(want) and want == applied_revision()
    smtp = appsettings.get(db, "smtp")
    out["email_uses_mailpit"] = uses_mailpit(smtp)
    out["email"] = {"enabled": smtp.enabled, "host": smtp.host, "port": smtp.port, "from_address": smtp.from_address}
    out["alert_recipients"] = appsettings.get(db, "monitoring").email_to
    return out
