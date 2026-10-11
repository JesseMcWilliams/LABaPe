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
    rows = list(db.scalars(select(Secret)))
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


# Inventory of the rest (design §24): listed so every internal secret has a
# policy, rotated in later phases or reminded about when LABaPe can't change it.
for kind, label, cls, restart, notes in (
    ("master_key", "Master key (LABAPE_SECRET_KEY: sessions, wraps the data key)", "privileged", True,
     "Needs a restart of the API and worker: weekly, in the maintenance window (rotator coming, phase R3)."),
    ("postgres_password", "PostgreSQL password", "privileged", True,
     "Needs a restart of the API and worker: weekly, in the maintenance window (rotator coming, phase R3)."),
    ("ca_password", "Platform CA key password and signing provisioner", "privileged", True,
     "Needs a restart of the CA: weekly, in the maintenance window (rotator coming, phase R3)."),
    ("vault_password", "Engine Ansible vault password", "privileged", False, "Rotator coming (phase R2)."),
    ("bootstrap_ssh_key", "VM bootstrap SSH key (Ansible to VMs)", "privileged", False, "Rotator coming (phase R2)."),
    ("oidc_client_secret", "OIDC client secret (Authentik)", "privileged", False,
     "Rotates with an elevated Authentik token (rotator coming); until then a reminder."),
    ("smtp_password", "SMTP password", "external", False,
     "LABaPe can't change it on the mail server: a reminder only."),
):
    REGISTRY[kind] = Rotator(kind=kind, label=label, secret_class=cls, needs_restart=restart, automatic=False,
                             interval_hours=RESTART_INTERVAL_HOURS if restart else (24 if cls == "privileged" else 168),
                             notes=notes)

PLATFORM = ("builtin_data_key", "worker_ssh_key", "directory_token", "master_key", "postgres_password", "ca_password",
            "vault_password", "bootstrap_ssh_key", "oidc_client_secret", "smtp_password")


# --- policies and scheduling -------------------------------------------------------

def ensure_policies(db: Session) -> None:
    """One policy per platform secret, with the rotator's defaults (kept once edited)."""
    have = {p.name for p in db.scalars(select(SecretPolicy))}
    for kind in PLATFORM:
        name = f"platform.{kind}"
        if name in have:
            continue
        r = REGISTRY[kind]
        db.add(SecretPolicy(name=name, kind=kind, label=r.label, secret_class=r.secret_class,
                            interval_hours=r.interval_hours, needs_restart=r.needs_restart, params=dict(r.defaults),
                            notes=r.notes, status="pending" if r.automatic else "manual",
                            next_due_at=utcnow() + dt.timedelta(hours=r.interval_hours)))
    db.commit()


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
        out.append(p)
    return out


def schedule(db: Session, now: dt.datetime | None = None) -> list[int]:
    """Queue a rotation job for each due policy that hasn't one queued or running."""
    busy = {(j.params or {}).get("policy_id") for j in db.scalars(
        select(Job).where(Job.type == "secret.rotate", Job.state.in_(("queued", "running"))))}
    queued = []
    for p in due(db, now):
        if p.id in busy:
            continue
        job = Job(type="secret.rotate", params={"policy_id": p.id}, requested_by="system")
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
        audit(db, job.requested_by, "secret.rotate", object_type="secret_policy", object_id=policy.id,
              detail={"name": policy.name, "summary": summary})
        log.write(f"labape: done: {summary}; next rotation {policy.next_due_at:%Y-%m-%d %H:%M} UTC\n")
        return 0

