"""Secret rotation (design §24).

Each kind of secret has a rotator (make the new value, apply it where it's
used, verify, commit; undo when verifying fails). Policies say how often,
from which store, with which elevated account, and whether LABaPe or an
external provider rotates it. The worker queues a `secret.rotate` job when
a policy is due; Rotate now does the same. A rotation that needs a service
restart only runs inside the maintenance window, with maintenance mode on.
A failure keeps the old secret, records why, and retries an hour later.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets as _secrets
import shutil
import subprocess
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, maintenance_mode
from ..models import Job, KvmHost, Secret, SecretDataKey, SecretPolicy, utcnow
from ..security import _fernet, audit, decrypt_value, encrypt_value, get_secret, put_secret
from . import hostio

log = logging.getLogger("labape.rotation")
RETRY_HOURS = 1
RESTART_INTERVAL_HOURS = 168          # restart-needing rotations: weekly, in the window (question 81)
ELEVATED_PREFIX = "elevated/"


class RotationError(RuntimeError):
    pass


@dataclass
class Rotator:
    kind: str
    label: str
    secret_class: str = "privileged"
    interval_hours: int = 24
    needs_restart: bool = False
    automatic: bool = True                # False: LABaPe only reminds (it can't change it)
    needs_elevated: bool = False
    notes: str = ""
    run: object = None                    # fn(db, policy, log) -> summary
    defaults: dict = field(default_factory=dict)
    via_agent: bool = False               # the host's update agent rotates it (it restarts services)


REGISTRY: dict[str, Rotator] = {}


def rotator(**kw):
    def wrap(fn):
        r = Rotator(run=fn, **kw)
        REGISTRY[r.kind] = r
        return fn
    return wrap


def _write(log, text: str) -> None:
    if log:
        log.write(text + "\n")


# --- rotators --------------------------------------------------------------------

@rotator(kind="builtin_data_key", label="Built-in secret store: data key",
         notes="Re-encrypts every stored secret with a new data key; no restart. The master key "
               "(LABAPE_SECRET_KEY) wraps the data key and rotates separately.")
def rotate_data_key(db: Session, policy: SecretPolicy, log) -> str:
    rows = list(db.scalars(select(Secret).where(Secret.store == "builtin")))     # external rows hold no value
    plain = {}
    for r in rows:
        value = decrypt_value(db, r.value)
        if value is None:
            raise RotationError(f"secret {r.name} doesn't decrypt; nothing was changed")
        plain[r.name] = value
    old = list(db.scalars(select(SecretDataKey).where(SecretDataKey.active.is_(True))))
    new = SecretDataKey(wrapped=_fernet().encrypt(Fernet.generate_key()).decode(), active=True)
    db.add(new)
    db.flush()
    for o in old:
        o.active, o.retired_at = False, utcnow()
    for r in rows:
        r.value = encrypt_value(db, plain[r.name])
    db.flush()
    for r in rows:                                      # verify before committing
        if decrypt_value(db, r.value) != plain[r.name]:
            raise RotationError(f"secret {r.name} didn't read back after re-encrypting")
    # Retired keys go after two days, once nothing in flight can still use them.
    cutoff = utcnow() - dt.timedelta(days=2)
    for k in db.scalars(select(SecretDataKey).where(SecretDataKey.active.is_(False))):
        when = k.retired_at if (k.retired_at is None or k.retired_at.tzinfo) else k.retired_at.replace(tzinfo=dt.timezone.utc)
        if when and when < cutoff and not any(r.value.startswith(f"v2:{k.id}:") for r in rows):
            db.delete(k)
    db.commit()
    _write(log, f"labape: re-encrypted {len(rows)} secret(s) with data key {new.id}")
    return f"{len(rows)} secret(s) re-encrypted with data key {new.id}"


@rotator(kind="worker_ssh_key", label="Worker SSH key for remote KVM hosts",
         notes="Adds the new key on every enabled remote host using the old one, checks the new one works, then "
               "removes the old key there and replaces it here.")
def rotate_worker_key(db: Session, policy: SecretPolicy, log) -> str:
    key = hostio.key_path()
    if not key.is_file():
        hostio.ensure_key()
        return "generated the first key"
    hosts = [h for h in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))) if hostio.is_remote(h.libvirt_uri)]
    old_pub = key.with_name(key.name + ".pub").read_text(encoding="utf-8").strip()
    new = key.with_name(key.name + ".new")
    for f in (new, new.with_name(new.name + ".pub")):
        f.unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "labape-worker", "-f", str(new)],
                   check=True, capture_output=True, timeout=30)
    new_pub = new.with_name(new.name + ".pub").read_text(encoding="utf-8").strip()
    added = []
    try:
        for h in hosts:
            host = hostio.Host(h.libvirt_uri)
            r = host.run(f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qxF '{new_pub}' ~/.ssh/authorized_keys 2>/dev/null "
                         f"|| echo '{new_pub}' >> ~/.ssh/authorized_keys", timeout=30)
            if r.returncode != 0:
                raise RotationError(f"{h.name}: couldn't add the new key: {r.stderr.decode(errors='replace').strip()}")
            added.append(h)
            check = subprocess.run([*host.ssh_base()[:1], "-i", str(new), *hostio.SSH_OPTS,
                                    *(["-p", str(host.port)] if host.port else []), host.target, "true"],
                                   capture_output=True, timeout=30, env=hostio.tool_env(), check=False)
            if check.returncode != 0:
                raise RotationError(f"{h.name}: the new key doesn't log in")
            _write(log, f"labape: {h.name}: new key added and working")
        for h in added:                                   # only now drop the old key there
            hostio.Host(h.libvirt_uri).run(f"grep -vxF '{old_pub}' ~/.ssh/authorized_keys > ~/.ssh/ak.tmp; "
                                           f"mv ~/.ssh/ak.tmp ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys")
    except Exception:
        for h in added:                                   # undo: remove the new key again
            hostio.Host(h.libvirt_uri).run(f"grep -vxF '{new_pub}' ~/.ssh/authorized_keys > ~/.ssh/ak.tmp; "
                                           f"mv ~/.ssh/ak.tmp ~/.ssh/authorized_keys")
        for f in (new, new.with_name(new.name + ".pub")):
            f.unlink(missing_ok=True)
        raise
    shutil.move(str(new), str(key))
    shutil.move(str(new.with_name(new.name + ".pub")), str(key.with_name(key.name + ".pub")))
    key.chmod(0o600)
    hostio.Host._primed.clear()
    return f"rotated on {len(hosts)} remote host(s)"


@rotator(kind="directory_token", label="Authentik directory token (group search)", secret_class="read_only",
         interval_hours=168, needs_elevated=True, defaults={"identifier": "labape-directory"},
         notes="Sets a new key on the token through the Authentik API, using an elevated Authentik token that may "
               "change it; the read-only token can't replace itself.")
def rotate_directory_token(db: Session, policy: SecretPolicy, log) -> str:
    from ..api.directory import api_base, authentik_groups
    elevated = elevated_value(db, policy)
    cfg = appsettings.get(db, "directory")
    base = api_base(cfg)
    if not base:
        raise RotationError("no Authentik URL (Settings > Directory)")
    ident = (policy.params or {}).get("identifier") or "labape-directory"
    old = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) or ""
    new = _secrets.token_urlsafe(48)
    r = httpx.post(f"{base}/api/v3/core/tokens/{ident}/set_key/", json={"key": new},
                   headers={"Authorization": f"Bearer {elevated}"}, timeout=15, verify=cfg.verify_tls)
    if r.status_code >= 300:
        raise RotationError(f"Authentik refused setting the key (HTTP {r.status_code}); the old token stays")
    try:
        authentik_groups(cfg, new, "")
    except Exception as exc:  # noqa: BLE001
        if old:
            httpx.post(f"{base}/api/v3/core/tokens/{ident}/set_key/", json={"key": old},
                       headers={"Authorization": f"Bearer {elevated}"}, timeout=15, verify=cfg.verify_tls)
        raise RotationError(f"the new token didn't work ({exc}); the old one was put back") from exc
    put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, new)
    return f"token {ident} has a new key"


@rotator(kind="oidc_client_secret", label="OIDC client secret (Authentik)", needs_elevated=True,
         notes="Sets a new client secret on LABaPe's Authentik provider through the Authentik API, using an elevated "
               "Authentik token that may change the provider, checks Authentik accepts it, then signs in with it from "
               "the next sign-in on; no restart. The secret file the stack was installed with is kept as a fallback. "
               "Without an elevated token it's a reminder.")
def rotate_oidc_client_secret(db: Session, policy: SecretPolicy, log) -> str:
    from ..api.directory import api_base
    from ..auth import oidc
    elevated = elevated_value(db, policy)
    cfg = appsettings.get(db, "directory")
    base, client_id = api_base(cfg), oidc.get_settings().oidc_client_id
    if not base or not client_id:
        raise RotationError("sign-in through OIDC isn't configured (no issuer or client ID)")
    old = oidc.client_secret(db)
    if not old:
        raise RotationError("there's no current client secret to rotate")
    auth = {"Authorization": f"Bearer {elevated}", "Accept": "application/json"}
    r = httpx.get(f"{base}/api/v3/providers/oauth2/", params={"client_id": client_id}, headers=auth, timeout=15,
                  verify=cfg.verify_tls)
    found = [p for p in (r.json().get("results", []) if r.status_code == 200 else []) if p.get("client_id") == client_id]
    if not found:
        raise RotationError(f"the elevated token can't see the Authentik provider for client {client_id} "
                            f"(HTTP {r.status_code})")
    url = f"{base}/api/v3/providers/oauth2/{found[0]['pk']}/"

    def set_secret(value: str) -> httpx.Response:
        return httpx.patch(url, json={"client_secret": value}, headers=auth, timeout=15, verify=cfg.verify_tls)

    new = _secrets.token_urlsafe(96)
    r = set_secret(new)
    if r.status_code >= 300:
        raise RotationError(f"Authentik refused the new client secret (HTTP {r.status_code}); the old one stays")
    _write(log, f"labape: Authentik provider {found[0].get('name', found[0]['pk'])} has a new client secret")
    try:
        oidc.check_client_secret(client_id, new)
        oidc.store_client_secret(db, new)
    except Exception as exc:  # noqa: BLE001
        back = set_secret(old)
        why = "the old one was put back" if back.status_code < 300 else             f"putting the old one back failed too (HTTP {back.status_code}): set it in Authentik by hand"
        raise RotationError(f"the new client secret didn't work ({exc}); {why}") from exc
    return f"provider {found[0].get('name', found[0]['pk'])} has a new client secret"


# Inventory of the rest (design §24): listed so every internal secret has a
# policy, rotated in later phases or reminded about when LABaPe can't change it.
# Restart-needing secrets live in host files the containers only read; the
# host's update agent rotates them (phase R3), inside the maintenance window.
for kind, label, notes in (
    ("master_key", "Master key (LABAPE_SECRET_KEY: sessions, wraps the data keys)",
     "The update agent makes a new key, re-wraps the store's data keys to it, swaps the file and restarts the API "
     "and worker (everyone signs in again). Rolled back if the health checks fail."),
    ("postgres_password", "PostgreSQL password",
     "The update agent sets a new password (ALTER ROLE), rewrites the connection secrets and restarts the API and "
     "worker. Rolled back if the health checks fail."),
    ("ca_password", "Platform CA key password and signing provisioner",
     "The update agent re-encrypts the CA's keys and the signing provisioner's key with a new password, swaps the "
     "file and restarts the CA, API and worker. The health check signs a test request; rolled back on failure."),
):
    REGISTRY[kind] = Rotator(kind=kind, label=label, needs_restart=True, via_agent=True,
                             interval_hours=RESTART_INTERVAL_HOURS, notes=notes)

# The engine's own secrets (phase R2, then S2 of design §26). Once they're in
# the catalog (engine_secrets.in_catalog) the worker rotates them in the
# platform store; until then they're files in the engine-secrets folder that
# the host agent swaps. No restart either way, so no maintenance window.
ENGINE_KINDS = ("vault_password", "bootstrap_ssh_key")


def _engine_rotator(kind: str):
    def run(db: Session, policy: SecretPolicy, log) -> str:
        from . import engine_secrets
        if not engine_secrets.in_catalog(db):
            raise RotationError("the engine secrets aren't in the secret store yet: run container/stores/stores.sh "
                                "setup on the LABaPe host, which moves them there")
        if kind == "vault_password":
            return engine_secrets.rotate_vault_password(db, log)
        return engine_secrets.rotate_ssh_key(db, log)
    return run


for kind, label, notes in (
    ("vault_password", "Engine Ansible vault password",
     "The worker re-encrypts the engine vault in the secret store with a new password, checks it opens, then stores "
     "both. Running jobs keep their own copies; the command line re-encrypts its own vault on its next run (it keeps "
     "the 7 previous passwords for that). No restart."),
    ("bootstrap_ssh_key", "VM bootstrap SSH key (Ansible to VMs)",
     "The worker makes a new key and adds it on the Linux VMs of every deployed environment (with the current or a "
     "previous key), checks it logs in, then stores it. Jobs and the command line read it from the secret store. "
     "The 7 previous keys are kept and still tried, for VMs that were off; keys older than that are removed from "
     "the VMs. Environments on remote KVM hosts are skipped."),
):
    REGISTRY[kind] = Rotator(kind=kind, label=label, via_agent=False, notes=notes, run=_engine_rotator(kind))


def uses_agent(db: Session, kind: str) -> bool:
    """Whether the host's update agent rotates this kind (the worker otherwise). The engine
    secrets are the worker's: the agent's file-based rotation of them (R2) was removed once
    every install had moved them into the store (design §26)."""
    return REGISTRY[kind].via_agent

REGISTRY["smtp_password"] = Rotator(kind="smtp_password", label="SMTP password", secret_class="external",
                                    automatic=False, interval_hours=168,
                                    notes="LABaPe can't change it on the mail server: a reminder only.")

# Lab environment accounts (phase R4, question 83): one policy per deployed
# environment and account, weekly by default, changeable or off per environment.
# The DSRM password is never rotated.
ENV_ACCOUNTS = {
    "windows_admin": ("Windows Administrator (local, member servers)",
                      "Sets a new password for the local Administrator on the environment's Windows hosts other than "
                      "domain controllers (Ansible's WinRM account), checks it signs in, and stores it for later jobs."),
    "domain_admin": ("Domain Administrator",
                     "Sets a new password for the domain's Administrator on its domain controller, checks it signs in, "
                     "and stores it for later jobs (joins, new hosts). The DSRM password is not rotated."),
}


def _env_rotator(kind: str):
    def run(db: Session, policy: SecretPolicy, log) -> str:
        from ..models import Environment
        from .vmaccess import AccessError, rotate_env_password
        env = db.get(Environment, (policy.params or {}).get("environment_id"))
        if env is None or env.status != "deployed":
            raise RotationError("the environment isn't deployed")
        try:
            return rotate_env_password(db, env, kind, log)
        except AccessError as exc:
            raise RotationError(str(exc)) from exc
    return run


for kind, (label, notes) in ENV_ACCOUNTS.items():
    REGISTRY[kind] = Rotator(kind=kind, label=label, interval_hours=168, notes=notes, run=_env_rotator(kind))

PLATFORM = ("builtin_data_key", "worker_ssh_key", "directory_token", "master_key", "postgres_password", "ca_password",
            "vault_password", "bootstrap_ssh_key", "oidc_client_secret", "smtp_password")


# --- policies and scheduling -------------------------------------------------------

def ensure_policies(db: Session) -> None:
    """One policy per platform secret, with the rotator's defaults (kept once edited)."""
    have = {p.name: p for p in db.scalars(select(SecretPolicy))}
    for kind in PLATFORM:
        name = f"platform.{kind}"
        if name in have:
            p, r = have[name], REGISTRY[kind]
            if p.status == "manual" and r.automatic:       # its rotator arrived in a later phase
                p.status, p.notes = "pending", r.notes
                p.next_due_at = p.next_due_at or utcnow() + dt.timedelta(hours=p.interval_hours)
            continue
        r = REGISTRY[kind]
        db.add(SecretPolicy(name=name, kind=kind, label=r.label, secret_class=r.secret_class,
                            interval_hours=r.interval_hours, needs_restart=r.needs_restart, params=dict(r.defaults),
                            notes=r.notes, status="pending" if r.automatic else "manual",
                            next_due_at=utcnow() + dt.timedelta(hours=r.interval_hours)))
    from .rotation_stores import STORE_ROTATORS, store_ready
    for kind in STORE_ROTATORS:                     # once its store is set up (design §25, P3)
        name, r = f"platform.{kind}", REGISTRY[kind]
        if name not in have and store_ready(db, kind):
            db.add(SecretPolicy(name=name, kind=kind, label=r.label, secret_class=r.secret_class,
                                interval_hours=r.interval_hours, needs_restart=False, notes=r.notes, status="pending",
                                next_due_at=utcnow() + dt.timedelta(hours=r.interval_hours)))
    db.commit()
    ensure_env_policies(db)
    from .vmaccess import split_legacy_passwords
    split_legacy_passwords(db)
    link_secrets(db)


def policy_secret(p: SecretPolicy) -> str | None:
    """The stored secret a policy rotates, when it has one in the catalog. The
    data key lives in its own table, and the worker key and the agent's
    secrets in files on the host (question 95)."""
    if p.kind == "directory_token":
        return appsettings.DIRECTORY_TOKEN_SECRET
    if p.kind == "smtp_password":
        return appsettings.SMTP_PASSWORD_SECRET
    if p.kind in STORE_ROTATORS:
        from .. import stores
        return stores.credential_secret(STORE_ROTATORS[p.kind])
    if p.kind == "oidc_client_secret":
        from ..auth.oidc import STORED_SECRET
        return STORED_SECRET
    if p.kind in ENGINE_KINDS:            # catalog rows once imported (design §26)
        from . import engine_secrets
        return engine_secrets.VAULT_PASSWORD if p.kind == "vault_password" else engine_secrets.SSH_KEY
    if p.kind in ENV_ACCOUNTS and (p.params or {}).get("environment_id"):
        from .vmaccess import env_password_name
        return env_password_name(p.params["environment_id"], p.kind)
    return None


def link_secrets(db: Session) -> None:
    """Point each catalog row at the policy that rotates it (design §25)."""
    for p in db.scalars(select(SecretPolicy)):
        name = policy_secret(p)
        row = db.get(Secret, name) if name else None
        if row is not None and row.policy_id != p.id:
            row.policy_id = p.id
        if row is not None and p.store != row.store:
            p.store = row.store
    db.commit()


def env_policy_name(env_id: int, kind: str) -> str:
    return f"env.{env_id}.{kind}"


def ensure_env_policies(db: Session) -> None:
    """A policy per deployed environment with Windows hosts (windows_admin) or a
    domain controller (domain_admin). Kept when the environment goes away, so
    its settings survive a redeploy; due() skips it meanwhile."""
    from ..models import Environment
    from .vmaccess import _inventory, env_targets, remote_env
    have = set(db.scalars(select(SecretPolicy.name).where(SecretPolicy.name.startswith("env."))))
    for env in db.scalars(select(Environment).where(Environment.status == "deployed")):
        missing = [k for k in ENV_ACCOUNTS if env_policy_name(env.id, k) not in have]
        if not missing or remote_env(env):
            continue
        inv = _inventory(db, env)
        for kind in missing:
            if not env_targets(env, inv, kind):
                continue
            r = REGISTRY[kind]
            db.add(SecretPolicy(name=env_policy_name(env.id, kind), kind=kind, label=f"{env.name}: {r.label}",
                                secret_class=r.secret_class, interval_hours=r.interval_hours, needs_restart=False,
                                params={"environment_id": env.id}, notes=r.notes, status="pending",
                                next_due_at=utcnow() + dt.timedelta(hours=r.interval_hours)))
    db.commit()


def _env_deployed(db: Session, p: SecretPolicy) -> bool:
    from ..models import Environment
    env = db.get(Environment, (p.params or {}).get("environment_id"))
    return env is not None and env.status == "deployed"


def elevated_value(db: Session, policy: SecretPolicy) -> str:
    if not policy.elevated_ref:
        raise RotationError("this secret needs an elevated account; choose one on its page")
    value = get_secret(db, ELEVATED_PREFIX + policy.elevated_ref)
    if not value:
        raise RotationError(f"elevated account {policy.elevated_ref} has no stored credential")
    return value


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def due(db: Session, now: dt.datetime | None = None) -> list[SecretPolicy]:
    """Policies LABaPe should rotate now. Restart-needing ones wait for the window."""
    from ..api.maintenance import next_window
    now = now or utcnow()
    window = next_window(db, now)
    out = []
    for p in db.scalars(select(SecretPolicy).where(SecretPolicy.enabled.is_(True))):
        r = REGISTRY.get(p.kind)
        if r is None or not r.automatic or p.managed_externally:
            continue
        if p.next_due_at and _aware(p.next_due_at) > now:
            continue
        if p.needs_restart and not (window and window["in_window"]):
            continue
        if p.kind in ENV_ACCOUNTS and not _env_deployed(db, p):
            continue
        if r.needs_elevated and not p.elevated_ref:      # a reminder until it has an elevated account
            continue
        if p.kind in STORE_ROTATORS and not store_ready(db, p.kind):
            continue
        out.append(p)
    return out


def request_agent(db: Session, policy: SecretPolicy, actor: str) -> str:
    """Hand a restart-needing rotation to the host's update agent."""
    from .. import update
    if not update.agent_installed():
        raise RotationError("the update agent isn't installed on this host (it rotates restart-needing secrets)")
    if update.busy():
        raise RotationError("the update agent is busy; try again when it's done")
    req = update.write_request("rotate", policy.kind, actor, policy_id=policy.id)
    policy.status, policy.last_error = "running", ""
    policy.params = {**(policy.params or {}), "request_id": req["id"]}
    db.commit()
    audit(db, actor, "secret.rotate.start", object_type="secret_policy", object_id=policy.id,
          detail={"name": policy.name, "agent_request": req["id"]})
    return req["id"]


def reconcile_agent(db: Session) -> None:
    """Record the outcome of rotations the agent finished."""
    from .. import update
    st = update.status()
    if st.get("action") != "rotate" or st.get("state") == "running" or not st.get("id"):
        return
    for p in db.scalars(select(SecretPolicy).where(SecretPolicy.status == "running")):
        if (p.params or {}).get("request_id") != st["id"]:
            continue
        if st.get("state") == "succeeded":
            p.status, p.last_error = "ok", ""
            p.last_rotated_at = utcnow()
            p.next_due_at = utcnow() + dt.timedelta(hours=p.interval_hours)
        else:
            p.status, p.last_error = "failed", (st.get("message") or "the agent reported a failure")[:2000]
            p.next_due_at = utcnow() + dt.timedelta(hours=RETRY_HOURS)
        db.commit()
        audit(db, "update-agent", "secret.rotate", object_type="secret_policy", object_id=p.id,
              outcome="ok" if p.status == "ok" else "failed", detail={"name": p.name, "message": st.get("message")})


def schedule(db: Session, now: dt.datetime | None = None) -> list[int]:
    """Queue a rotation job for each due policy that hasn't one queued or running;
    restart-needing ones go to the host agent instead (it restarts the worker)."""
    reconcile_agent(db)
    busy = {(j.params or {}).get("policy_id") for j in db.scalars(
        select(Job).where(Job.type == "secret.rotate", Job.state.in_(("queued", "running"))))}
    queued = []
    for p in due(db, now):
        if p.id in busy or p.status == "running":
            continue
        if uses_agent(db, p.kind):
            try:
                request_agent(db, p, "system")
            except RotationError as exc:
                p.status, p.last_error = "failed", str(exc)
                p.next_due_at = utcnow() + dt.timedelta(hours=RETRY_HOURS)
                db.commit()
            continue
        job = Job(type="secret.rotate", params={"policy_id": p.id}, requested_by="system",
                  environment_id=(p.params or {}).get("environment_id") if p.kind in ENV_ACCOUNTS else None)
        db.add(job)
        db.commit()
        queued.append(job.id)
    return queued


def run_job(db: Session, job: Job) -> int:
    """The worker's side of a secret.rotate job."""
    policy = db.get(SecretPolicy, (job.params or {}).get("policy_id"))
    with open(job.log_path, "a", encoding="utf-8", buffering=1) as log:
        if policy is None:
            log.write("labape: no such rotation policy\n")
            return 1
        r = REGISTRY.get(policy.kind)
        if r is None or not r.automatic:
            log.write(f"labape: {policy.label} isn't rotated by LABaPe ({r.notes if r else 'unknown kind'})\n")
            return 1
        log.write(f"labape: rotating {policy.label}\n")
        started_maintenance = False
        if policy.needs_restart and not maintenance_mode.get(db)["active"]:
            maintenance_mode.turn_on(db, "system", f"rotating {policy.label}", "rotation")
            started_maintenance = True
        try:
            summary = r.run(db, policy, log)
        except Exception as exc:  # noqa: BLE001 — any failure keeps the old secret
            db.rollback()
            policy = db.get(SecretPolicy, policy.id)
            policy.status, policy.last_error = "failed", str(exc)[:2000]
            policy.next_due_at = utcnow() + dt.timedelta(hours=RETRY_HOURS)
            db.commit()
            audit(db, job.requested_by, "secret.rotate", object_type="secret_policy", object_id=policy.id,
                  outcome="failed", detail={"name": policy.name, "error": str(exc)[:500]})
            log.write(f"labape: rotation failed, the old secret stays: {exc}\nlabape: retrying in {RETRY_HOURS} hour(s)\n")
            return 1
        finally:
            if started_maintenance:
                maintenance_mode.turn_off(db)
        policy.status, policy.last_error = "ok", ""
        policy.last_rotated_at = utcnow()
        policy.next_due_at = utcnow() + dt.timedelta(hours=policy.interval_hours)
        db.commit()
        link_secrets(db)                                  # a first rotation makes the catalog row
        audit(db, job.requested_by, "secret.rotate", object_type="secret_policy", object_id=policy.id,
              detail={"name": policy.name, "summary": summary})
        log.write(f"labape: done: {summary}; next rotation {policy.next_due_at:%Y-%m-%d %H:%M} UTC\n")
        return 0


# The stores' own logins (design §25, P3); imported last, since it registers with this module.
from .rotation_stores import STORE_ROTATORS, store_ready  # noqa: E402
