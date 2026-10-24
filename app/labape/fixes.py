"""One-click repairs for the default integrations (the Fix button on the admin dashboard's checks).

Each fix names the credentials it needs, asks for them once, repairs the integration with them and forgets them:
what is typed is used for this call only and is never stored or logged (the host "Set up access" works the same
way). What the fix leaves behind is the integration's own, least-privilege credential.

- directory: Authentik's group search. Needs an Authentik admin API token. Makes (or finds) the read-only role
  `labape-directory-reader`, the service account `labape-directory`, its non-expiring API token, saves that token,
  turns the directory on and tests the search.
- syslog: the audit log's forwarding to a syslog server. Needs the server, port, protocol and facility. Sends a test
  message (TCP and TLS must connect; UDP gives no answer, which the message says), then saves and turns it on.
- platform_ca / store.<name>: the CA or a secret store is not answering. These are repaired on the host, by the
  update agent (the API has no access to the containers): restart the service (a store is then unsealed again), or
  give LABaPe a new store login when the store refuses the stored one. They ask for nothing but a confirmation.
- smtp: outgoing email. Needs the server and, if it asks, a user and password. Saves them, turns email on and
  checks that the server accepts the login (and sends a test email when an address is given).
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from . import appsettings, ca, notify
from .errors import FieldError
from .security import get_secret, put_secret

ROLE = "labape-directory-reader"
SERVICE_USER = "labape-directory"
PERMISSIONS = ["authentik_core.view_group", "authentik_core.view_user"]   # groups for the search, users for the check that an account is still active (question 196)


@dataclass
class Fix:
    name: str                          # the admin check it repairs (dashboard check name)
    title: str
    intro: str
    fields: list[dict]
    run: Callable[[Session, dict, str], str]               # (db, typed values, who asked) -> what to tell the person
    detail_keys: list[str] = field(default_factory=list)       # the values that may go in the audit log (never secrets)


def _field(name: str, label: str, *, secret: bool = False, required: bool = True, default: str = "", help: str = "",
           kind: str = "text", options: list[str] | None = None) -> dict:
    return {"name": name, "label": label, "secret": secret, "required": required, "default": default, "help": help,
            "kind": kind, **({"options": options} if options else {})}


# --- directory (Authentik group search) -------------------------------------------------------------

def _authentik(base: str, token: str, verify):
    def call(method: str, path: str, **kw):
        try:
            r = httpx.request(method, f"{base}/api/v3{path}", timeout=20, verify=verify, **kw,
                              headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise FieldError("api_url", f"Couldn't reach Authentik at {base}: {type(exc).__name__}.") from exc
        if r.status_code in (401, 403):
            raise FieldError("token", "Authentik refused that token: it needs to be an admin's API token "
                                      "(Authentik > Directory > Tokens and App passwords).")
        if r.status_code >= 300:
            raise FieldError("token", f"Authentik answered {method} {path} with HTTP {r.status_code}: {r.text[:160]}")
        return r.json() if r.content else {}
    return call


def fix_directory(db: Session, v: dict, actor: str = "") -> str:
    from .api.directory import api_base, authentik_groups
    cfg = appsettings.get(db, "directory")
    if v.get("api_url", "").strip():
        cfg = cfg.model_copy(update={"api_url": v["api_url"].strip()})
    base = api_base(cfg)
    if not base:
        raise FieldError("api_url", "Give Authentik's address (no OIDC issuer is configured to take it from).")
    call = _authentik(base, v["token"].strip(), ca.trust() if cfg.verify_tls else False)

    def first(path: str, key: str, value: str):
        return next((x for x in call("GET", path)["results"] if x.get(key) == value), None)

    role = first(f"/rbac/roles/?name={ROLE}", "name", ROLE) or call("POST", "/rbac/roles/", json={"name": ROLE})
    call("POST", f"/rbac/permissions/assigned_by_roles/{role['pk']}/assign/", json={"permissions": PERMISSIONS})
    user = first(f"/core/users/?username={SERVICE_USER}", "username", SERVICE_USER)
    if user is None:
        user = call("POST", "/core/users/", json={"username": SERVICE_USER, "name": "LABaPe directory lookup",
                                                  "type": "service_account", "is_active": True, "path": "users"})
    have = {r["pk"] for r in (user.get("roles_obj") or [])}
    if role["pk"] not in have:
        call("PATCH", f"/core/users/{user['pk']}/", json={"roles": sorted(have | {role["pk"]})})
    if first(f"/core/tokens/?identifier={SERVICE_USER}", "identifier", SERVICE_USER) is None:
        call("POST", "/core/tokens/", json={"identifier": SERVICE_USER, "intent": "api", "user": user["pk"],
                                            "expiring": False, "description": "LABaPe group search (read-only)"})
    key = call("GET", f"/core/tokens/{SERVICE_USER}/view_key/").get("key")
    if not key:
        raise FieldError("token", "Authentik made the token but did not give its key back.")
    try:
        groups = authentik_groups(cfg, key, "")
    except (httpx.HTTPError, ValueError) as exc:
        raise FieldError("api_url", f"The new token did not search groups: {type(exc).__name__}.") from exc
    put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, key)
    appsettings.put(db, "directory", cfg.model_copy(update={"enabled": True}))
    return (f"Group search works: the read-only token for {SERVICE_USER} is saved and Authentik returned "
            f"{len(groups)} group(s). The admin token you gave was not kept.")


# --- smtp -------------------------------------------------------------------------------------------

def fix_smtp(db: Session, v: dict, actor: str = "") -> str:
    old = appsettings.get(db, "smtp")
    try:
        port = int(v.get("port") or 587)
    except ValueError as exc:
        raise FieldError("port", "The port is a number.") from exc
    host, from_address = v["host"].strip(), v["from_address"].strip()
    if "@" not in from_address:
        raise FieldError("from_address", "Give a From address (name@domain).")
    try:
        cfg = appsettings.SmtpSettings(**{**old.model_dump(), "enabled": True, "host": host, "port": port,
                                          "security": v.get("security") or "starttls", "username": v.get("username", "").strip(),
                                          "from_address": from_address})
    except ValueError as exc:
        raise FieldError("host", str(exc)) from exc
    password = v.get("password") or get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or ""
    if cfg.username and not password:
        raise FieldError("password", "Give the password for that user name, or clear the user name.")
    try:
        notify.smtp_check(cfg, password)
        to = v.get("test_to", "").strip()
        if to:
            notify.send_email(cfg, password, [to], "LABaPe test email", "Email from this stack works.")
    except (OSError, smtplib.SMTPException) as exc:
        raise FieldError("host", f"{host}:{port} did not accept that: {exc}") from exc
    appsettings.put(db, "smtp", cfg)
    if v.get("password"):
        put_secret(db, appsettings.SMTP_PASSWORD_SECRET, v["password"])
    return f"Email works: {host}:{port} accepted the login" + (f" and a test email went to {to}." if v.get("test_to", "").strip() else ".")


# --- syslog -----------------------------------------------------------------------------------------

def fix_syslog(db: Session, v: dict, actor: str = "") -> str:
    old = appsettings.get(db, "syslog")
    try:
        port = int(v.get("port") or 514)
    except ValueError as exc:
        raise FieldError("port", "The port is a number.") from exc
    try:
        cfg = appsettings.SyslogSettings(**{**old.model_dump(), "enabled": True, "host": v["host"].strip(), "port": port,
                                            "protocol": v.get("protocol") or "udp", "facility": v.get("facility") or "local0"})
    except ValueError as exc:
        raise FieldError("protocol", str(exc).splitlines()[-1] if str(exc) else "That isn't a valid setting.") from exc
    try:
        notify.send_syslog(cfg, {"action": "fix.syslog", "actor": actor, "outcome": "ok",
                                 "detail": {"message": "LABaPe syslog fix: test message"}})
    except (OSError, ValueError) as exc:
        raise FieldError("host", f"Couldn't send to {cfg.host}:{cfg.port} over {cfg.protocol}: {exc}") from exc
    appsettings.put(db, "syslog", cfg)
    note = (" UDP gives no answer: check on the server that the test message arrived."
            if cfg.protocol == "udp" else " The server accepted the connection.")
    return f"Audit events go to {cfg.host}:{cfg.port} over {cfg.protocol.upper()} now.{note}"


# --- the CA and the secret stores: repaired on the host by the update agent -----------------------------------------

STORE_CHOICES = {"new_login": "Give LABaPe a new login (runs the stores' setup)", "restart": "Restart the store"}
RESTARTABLE = {"labape-ca": "the platform CA", "labape-openbao": "OpenBao", "labape-conjur": "Conjur"}


def _agent(action: str, target: str, actor: str, message: str) -> str:
    from . import update
    if not update.agent_installed():
        raise FieldError("agent", "The update agent isn't installed on this host, so this can't be done from here: "
                                  "run `docker compose restart` (or `container/stores/stores.sh setup` for a store login) on the host.",
                         status_code=409)
    if update.busy():
        raise FieldError("agent", "The update agent is busy with something else; try again when it has finished.", status_code=409)
    update.write_request(action, target, actor or "fix")
    return message


def fix_restart_ca(db: Session, v: dict, actor: str = "") -> str:
    return _agent("restart_service", "labape-ca", actor,
                  "Asked the update agent to restart the platform CA. It takes about a minute; the check turns green when the CA answers.")


def _store_fix(store: str):
    service = f"labape-{store}"

    def run(db: Session, v: dict, actor: str = "") -> str:
        what = v.get("what") or STORE_CHOICES["restart"]
        if what == STORE_CHOICES["new_login"]:
            return _agent("stores_setup", "", actor,
                          "Asked the update agent to give LABaPe a new store login (stores.sh setup). It takes about a minute.")
        if what != STORE_CHOICES["restart"]:
            raise FieldError("what", "Choose one of the two.")
        if service not in RESTARTABLE:
            raise FieldError("what", f"{store} is not restarted from here.")
        return _agent("restart_service", service, actor,
                      f"Asked the update agent to restart {RESTARTABLE[service]} and unseal it. It takes about a minute.")
    return run


def _store_default(db: Session, store: str) -> str:
    # A refused login wants a new one; anything else (no answer, sealed) wants a restart.
    from . import stores
    from .engine.secret_health import AUTH_FAILURE
    try:
        ok, msg = stores.check(db, store, cached=True)
    except Exception:  # noqa: BLE001
        return STORE_CHOICES["restart"]
    return STORE_CHOICES["new_login"] if not ok and AUTH_FAILURE.search(msg or "") else STORE_CHOICES["restart"]


FIXES: dict[str, Fix] = {
    "directory": Fix(
        "directory", "Fix the directory (group search)",
        "LABaPe will create what it needs in Authentik: a role that can only view groups and users, a service account with that "
        "role and a non-expiring API token, then save that token and test the search. Give an Authentik admin API token "
        "for this one run; it is not stored.",
        [_field("token", "Authentik admin API token", secret=True, help="Authentik > Directory > Tokens and App passwords > Create"),
         _field("api_url", "Authentik address", required=False, help="Leave blank to use the sign-in provider's address")],
        fix_directory, ["api_url"]),
    "smtp": Fix(
        "smtp", "Fix email (SMTP)",
        "Give the mail server LABaPe sends through. It checks the login (and sends a test email if you give an address), "
        "then saves the settings and turns email on.",
        [_field("host", "SMTP server"), _field("port", "Port", default="587"),
         _field("security", "Security", kind="select", options=["starttls", "ssl", "none"], default="starttls"),
         _field("username", "User name", required=False), _field("password", "Password", secret=True, required=False,
                                                                   help="Blank keeps the saved password"),
         _field("from_address", "From address", help="name@domain"),
         _field("test_to", "Send a test email to", required=False)],
        fix_smtp, ["host", "port", "security", "username", "from_address"]),
    "syslog": Fix(
        "syslog", "Fix syslog forwarding",
        "Give the syslog server the audit log is sent to. A test message is sent first (TCP and TLS must connect); then the "
        "settings are saved and forwarding is turned on.",
        [_field("host", "Syslog server"), _field("port", "Port", required=False, default="514"),
         _field("protocol", "Protocol", kind="select", options=["udp", "tcp", "tls"], default="udp"),
         _field("facility", "Facility", kind="select", required=False, default="local0",
                options=["local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7", "auth", "daemon", "user"])],
        fix_syslog, ["host", "port", "protocol", "facility"]),
    "platform_ca": Fix(
        "platform_ca", "Restart the platform CA",
        "The CA (step-ca) is not answering. The update agent on this host restarts its container and waits for the stack's "
        "health checks. Certificates already issued are not affected. Nothing needs to be typed.",
        [], fix_restart_ca, []),
    **{f"store.{name}": Fix(
        f"store.{name}", f"Fix {label}",
        f"{label} is not working for LABaPe. Choose what the update agent on this host should do: restart it (it is unsealed "
        "again after) when it does not answer or is sealed, or run the stores' setup when it refuses the stored login (a full refresh of "
        "their configuration that gives LABaPe new logins where needed; a minute or two). "
        "Nothing else needs to be typed.",
        [_field("what", "What to do", kind="select", required=False, options=list(STORE_CHOICES.values()),
                default=STORE_CHOICES["restart"])],
        _store_fix(name), ["what"]) for name, label in (("openbao", "OpenBao"), ("conjur", "Conjur"))},
}

AGENT_FIXES = {"platform_ca", "store.openbao", "store.conjur"}       # done by the update agent: offered when it is installed


def listing(db: Session) -> list[dict]:
    """The fixes with the settings they would start from (never a secret)."""
    smtp, directory, syslog = appsettings.get(db, "smtp"), appsettings.get(db, "directory"), appsettings.get(db, "syslog")
    start = {"smtp": {"host": smtp.host, "port": str(smtp.port), "security": smtp.security, "username": smtp.username,
                      "from_address": smtp.from_address},
             "directory": {"api_url": directory.api_url},
             "syslog": {"host": syslog.host, "port": str(syslog.port), "protocol": syslog.protocol, "facility": syslog.facility}}
    from . import update
    agent = update.agent_installed()
    out = []
    for f in FIXES.values():
        if f.name in AGENT_FIXES and not agent:
            continue
        fields = [{**fd, "default": start.get(f.name, {}).get(fd["name"], fd["default"]) or fd["default"]} for fd in f.fields]
        if f.name.startswith("store."):
            fields = [{**fd, "default": _store_default(db, f.name.split(".", 1)[1])} for fd in fields]
        out.append({"name": f.name, "title": f.title, "intro": f.intro, "fields": fields})
    return out
