"""Secret health (S1, Planning_Lists-Secrets-Settings.md; question 161).

Every secret LABaPe rotates or holds is **checked**: does the stored value still work (a sign-in, an
API call, a key that opens what it locks). The check runs every quarter of the rotation period, at
least daily; a secret rotating daily or more often needs none (its rotation is the check); one that
isn't rotated by LABaPe is checked weekly. Each kind is **recoverable** or not:
- Recoverable yes: when a check fails, LABaPe can put a working value back by itself: an earlier
  value it kept, or a new one set with another credential it holds (the rotation, the update agent).
- Recoverable no: if the value is lost there's no way back for LABaPe; the secret is flagged, an
  alert is raised (monitoring), and its notes say what a person must do.
A check that can't reach what it tests (a VM that's off, a host that's down) is inconclusive, not a
failure.
"""
from __future__ import annotations

import datetime as dt
import re
import smtplib
import ssl
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import yaml
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import Environment, Job, KvmHost, Secret, SecretPolicy, utcnow
from ..security import audit, get_secret, put_secret

MIN_CHECK = dt.timedelta(hours=24)
UNROTATED_CHECK = dt.timedelta(days=7)
RETRY_AFTER = dt.timedelta(hours=1)


class CheckError(RuntimeError):
    pass


class NotInUse(RuntimeError):
    """Nothing to check (not set up, nothing deployed); the status is `none`."""


@dataclass
class Health:
    recoverable: bool
    how: str                                                   # what recovery does, or what a person must do
    check: Callable[[Session, SecretPolicy], str] | None = None
    recover: Callable[[Session, SecretPolicy, object], str] | None = None   # None: run the rotation


# --- checks ------------------------------------------------------------------------------------

def _builtin_decrypts(db: Session, p: SecretPolicy) -> str:
    n = 0
    for row in db.scalars(select(Secret).where(Secret.store == "builtin", Secret.value != "")):
        try:
            get_secret(db, row.name)
        except Exception as exc:  # noqa: BLE001 — a key that doesn't open its data
            raise CheckError(f"{row.name} doesn't decrypt: {exc}") from exc
        n += 1
    return f"all {n} built-in secrets decrypt"


def _database(db: Session, p: SecretPolicy) -> str:
    db.execute(text("SELECT 1"))
    return "the database accepts the stored password"


def _ca_signs(db: Session, p: SecretPolicy) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from .. import ca
    if not ca.configured():
        raise NotInUse("the platform CA isn't set up")
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "labape-secret-check.invalid")]))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName("labape-secret-check.invalid")]), critical=False)
           .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    try:
        ca.sign(csr, hours=1)
    except Exception as exc:  # noqa: BLE001
        raise CheckError(f"the CA doesn't sign with the stored password: {exc}") from exc
    return "the CA signs with the stored password"


def _vault_opens(db: Session, p: SecretPolicy) -> str:
    from . import engine_secrets
    if not engine_secrets.in_catalog(db):
        raise NotInUse("the engine secrets aren't in the secret store")
    with tempfile.TemporaryDirectory() as d:
        try:
            engine_secrets.write_vault(db, Path(d))
        except Exception as exc:  # noqa: BLE001
            raise CheckError(f"the engine vault doesn't open with its stored password: {exc}") from exc
    return "the engine vault opens with its stored password"


def _vault_recover(db: Session, p: SecretPolicy, log) -> str:
    """An earlier password that still opens the vault becomes the stored one."""
    from . import engine_secrets as E
    vault = E._get(db, E.VAULT)
    with tempfile.TemporaryDirectory() as d:
        vf, pf = Path(d) / "v.yml", Path(d) / "pw"
        vf.write_text(vault, encoding="utf-8")
        for i, old in enumerate(E._history(db, E.VAULT_PASSWORD_HISTORY)):
            pf.write_text(old, encoding="utf-8")
            if E._opens(vf, pf):
                put_secret(db, E.VAULT_PASSWORD, old)
                log.write(f"labape: the vault opens with the {i + 1}. previous password; it's the stored one again\n")
                return "an earlier password opens the vault; stored again"
    raise CheckError("none of the kept earlier passwords opens the vault")


def _ssh_pair(db: Session, p: SecretPolicy) -> str:
    from . import engine_secrets as E
    if not E.in_catalog(db):
        raise NotInUse("the engine secrets aren't in the secret store")
    with tempfile.TemporaryDirectory() as d:
        key = Path(d) / "k"
        key.write_text(E._get(db, E.SSH_KEY), encoding="utf-8")
        key.chmod(0o600)
        r = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], capture_output=True, text=True, timeout=30, check=False)
    pub = E._get(db, E.SSH_PUB).split()[:2]
    if r.returncode != 0 or r.stdout.split()[:2] != pub:
        raise CheckError("the stored VM key is unreadable or doesn't match its public key")
    return "the VM key is intact and matches its public key"


def _remote_hosts(db: Session) -> list[KvmHost]:
    from . import hostio
    return [h for h in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))) if hostio.is_remote(h.libvirt_uri)]


# OpenSSH 10's per-source penalties: after a refused key, sshd drops every connection from the same
# address for a while ("penalty: failed authentication"), the access account's too. Such a drop is
# a host we can't tell about yet, not a refusal; recovery waits and tries again.
DROPPED = ("Connection reset", "Connection closed", "kex_exchange_identification")
PENALTY_WAIT = 20
_refused: dict[int, list[str]] = {}       # policy id -> the hosts its last check found refusing the key


def _probe(h) -> str:
    """ok | refused | dropped | down"""
    from . import hostio
    r = hostio.Host(h.libvirt_uri).run("true", timeout=30)
    err = r.stderr.decode(errors="replace")
    if r.returncode == 0:
        return "ok"
    if "Permission denied" in err:
        return "refused"
    return "dropped" if any(x in err for x in DROPPED) else "down"


def _worker_key(db: Session, p: SecretPolicy) -> str:
    hosts = _remote_hosts(db)
    if not hosts:
        raise NotInUse("no remote KVM host uses the worker's key")
    state = {h.name: _probe(h) for h in hosts}
    for h in hosts:
        if state[h.name] == "dropped":                   # a penalty from an earlier refusal: once more, later
            time.sleep(PENALTY_WAIT)
            state[h.name] = _probe(h)
    refused = [n for n, s in state.items() if s == "refused"]
    _refused[p.id] = refused
    if refused:
        raise CheckError(f"{', '.join(refused)} refuse the worker's key")
    down = [n for n, s in state.items() if s in ("down", "dropped")]
    return f"{len(hosts) - len(down)} remote host(s) accept the worker's key" + (f"; {', '.join(down)} not reachable" if down else "")


AUTHORIZE = r'''
import json, os, pwd
u = pwd.getpwnam(ARGS["user"])
d = os.path.join(u.pw_dir, ".ssh")
os.makedirs(d, mode=0o700, exist_ok=True)
ak = os.path.join(d, "authorized_keys")
lines = open(ak).read().splitlines() if os.path.exists(ak) else []
if ARGS["key"] not in lines:
    with open(ak, "a") as f:
        f.write(ARGS["key"] + "\n")
for path in (d, ak):
    os.chown(path, u.pw_uid, u.pw_gid)
os.chmod(ak, 0o600)
print(json.dumps({"ok": True}))
'''


def _worker_key_recover(db: Session, p: SecretPolicy, log) -> str:
    """Authorize the worker's key again through each refusing host's LABaPe access account."""
    from . import hostadmin, hostio
    pub = hostio.ensure_key()
    names = set(_refused.pop(p.id, []))
    fixed, left = [], []
    for h in [h for h in _remote_hosts(db) if not names or h.name in names]:
        user = urlsplit(h.libvirt_uri).username or ""
        if h.access_status != "ready" or not user:
            left.append(f"{h.name} (no LABaPe access account set up there)")
            continue
        for attempt in range(4):
            try:
                hostadmin.for_host(h).python(AUTHORIZE, {"user": user, "key": pub}, sudo=True, timeout=60)
                fixed.append(h.name)
                break
            except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
                if attempt < 3 and any(x in str(exc) for x in DROPPED):
                    log.write(f"labape: {h.name} dropped the connection (an SSH penalty?); again in {PENALTY_WAIT} s\n")
                    time.sleep(PENALTY_WAIT)
                    continue
                log.write(f"labape: {h.name}: {exc}\n")
                left.append(f"{h.name} ({str(exc)[:150]})")
                break
    log.write(f"labape: the worker's key authorized again on {', '.join(fixed) or 'no host'}\n")
    if left:
        raise CheckError(f"couldn't authorize it on {', '.join(left)}")
    time.sleep(PENALTY_WAIT)                             # before the check signs in again
    return f"authorized again on {', '.join(fixed)}"


def _directory_token(db: Session, p: SecretPolicy) -> str:
    from ..api.directory import api_base, authentik_groups
    cfg = appsettings.get(db, "directory")
    token = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) or ""
    if not cfg.enabled or not token or not api_base(cfg):
        raise NotInUse("the directory search isn't set up")
    try:
        authentik_groups(cfg, token, "")
    except Exception as exc:  # noqa: BLE001
        if "401" in str(exc) or "403" in str(exc):
            raise CheckError(f"Authentik refuses the stored token: {exc}") from exc
        raise NotInUse(f"Authentik isn't answering: {exc}") from exc
    return "Authentik accepts the stored token"


def _oidc_secret(db: Session, p: SecretPolicy) -> str:
    from ..auth import oidc
    from ..config import get_settings
    s = get_settings()
    secret = oidc.client_secret(db)
    if not s.oidc_issuer or not secret:
        raise NotInUse("sign-in isn't through OIDC")
    try:
        oidc.check_client_secret(s.oidc_client_id, secret)
    except RuntimeError as exc:
        raise CheckError(f"the provider doesn't accept the stored client secret ({exc})") from exc
    return "the provider accepts the stored client secret"


def _authentik_admin(db: Session, p: SecretPolicy) -> str:
    import httpx
    from .. import authentik_admin as aa, ca
    from ..api.directory import api_base
    password = get_secret(db, aa.SECRET) or ""
    cfg = appsettings.get(db, "directory")
    base = api_base(cfg)
    if not password:
        raise NotInUse("no akadmin password is stored yet: import it (labape authentik-admin-import) or rotate")
    if not base:
        raise NotInUse("no Authentik URL (Settings > Directory)")
    try:
        ok = aa.signs_in(base, aa.USER, password, verify=ca.trust() if cfg.verify_tls else False)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise NotInUse(f"Authentik isn't answering as expected: {exc}") from exc
    if not ok:
        raise CheckError(f"Authentik refuses the stored password for {aa.USER}")
    return f"{aa.USER} signs in with the stored password"


def _elevated_account(db: Session, p: SecretPolicy) -> str:
    import httpx
    from .. import elevated
    account = (p.params or {}).get("account", "")
    if not elevated.token_identifier(account):
        raise NotInUse(f"{account} isn't an Authentik token, so there is nothing LABaPe can ask Authentik about")
    try:
        ok = elevated.works(db, account)
    except elevated.ElevatedError as exc:
        raise NotInUse(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise NotInUse(f"Authentik isn't answering: {exc}") from exc
    if ok is None:
        raise NotInUse("no credential is stored for it")
    if not ok:
        raise CheckError(f"Authentik refuses the stored credential for {account}")
    return f"Authentik accepts the stored credential for {account}"


def _elevated_restore(db: Session, p: SecretPolicy, log) -> str:
    from .. import elevated
    try:
        return elevated.restore(db, (p.params or {}).get("account", ""), log)
    except elevated.ElevatedError as exc:
        raise CheckError(str(exc)) from exc


def _smtp_login(db: Session, p: SecretPolicy) -> str:
    cfg = appsettings.get(db, "smtp")
    password = get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or ""
    if not cfg.enabled or not cfg.host or not cfg.username or not password:
        raise NotInUse("email isn't set up with a sign-in")
    try:
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=10, context=ssl.create_default_context()) \
            if cfg.security == "ssl" else smtplib.SMTP(cfg.host, cfg.port, timeout=10)
        try:
            if cfg.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            server.login(cfg.username, password)
        finally:
            server.quit()
    except smtplib.SMTPAuthenticationError as exc:
        raise CheckError(f"the mail server refuses the stored password: {exc}") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise NotInUse(f"the mail server isn't answering: {exc}") from exc
    return "the mail server accepts the stored password"


def _store_login(store: str):
    def check(db: Session, p: SecretPolicy) -> str:
        from .. import stores
        if store not in stores.configured(db):
            raise NotInUse(f"{store} isn't set up")
        ok, msg = stores.check(db, store)
        if not ok:
            raise CheckError(f"{store} refuses LABaPe's stored login: {msg}")
        return f"{store} accepts LABaPe's stored login"
    return check


def _stores_recover(db: Session, p: SecretPolicy, log) -> str:
    """The stores' root credentials are on the host: the update agent runs stores.sh setup, which gives
    LABaPe a new login where the old one doesn't work."""
    from .. import update
    if not update.agent_installed() or update.busy():
        raise CheckError("the update agent isn't installed or is busy; run container/stores/stores.sh setup on the host")
    update.write_request("stores_setup", "", "secret-check")
    log.write("labape: asked the update agent to run stores.sh setup (new logins for LABaPe)\n")
    return "asked the update agent for new store logins"


AUTH_FAILURE = re.compile(r"rejected|401|unauthori[sz]ed|authenticat|password|logon failure", re.I)


def _env_password(kind: str):
    def check(db: Session, p: SecretPolicy) -> str:
        from .vmaccess import _ansible, _hosts, _inventory, env_targets, probe, recovery_user, remote_env, stored_password
        env = db.get(Environment, (p.params or {}).get("environment_id"))
        if env is None or env.status != "deployed" or remote_env(env):
            raise NotInUse("the environment isn't deployed here")
        inv = _inventory(db, env)
        targets = env_targets(env, inv, kind)
        if not targets:
            raise NotInUse("no hosts use this account")
        if kind.endswith("2"):                 # the recovery pair's second admin (A1), per VM on members (A2)
            pw = {h: stored_password(db, env, inv, kind, h) for h in targets}
            if not all(pw.values()):
                raise NotInUse("no labape-admin2 here yet (a redeploy creates it)")
            r = probe(inv, targets, recovery_user(inv, kind), pw)
        else:
            r = _ansible(inv, ":".join(targets), "ansible.windows.win_ping", {}, timeout=300,
                         scrub=tuple(_hosts(inv)[h].get("ansible_password", "") for h in targets))
        refused = [h for h in targets if r.get(h) not in ("ok", "changed") and AUTH_FAILURE.search(r.errors.get(h, ""))]
        if refused:
            raise CheckError(f"{', '.join(refused)} refuse the stored password")
        ok = [h for h in targets if r.get(h) in ("ok", "changed")]
        if not ok:
            raise NotInUse("none of its hosts answered (off?)")
        return f"{len(ok)} host(s) accept the stored password"
    return check


def _env_password_recover(kind: str):
    def recover(db: Session, p: SecretPolicy, log) -> str:
        """In order: the value a rotation tried last (kept until it's sure), where it signs in; the other admin
        of the recovery pair setting the stored password again (A1); for member servers' Administrator,
        the domain admin doing the same."""
        from .vmaccess import (AccessError, _ansible, _hosts, _inventory, env_targets, probe, recovery_user,
                               reset_with_pair, store_password, stored_password)
        env = db.get(Environment, (p.params or {}).get("environment_id"))
        inv = _inventory(db, env)
        targets = env_targets(env, inv, kind)
        per_vm = kind in ("windows_admin", "windows_admin2")
        base = f"environment/{env.id}/rotation-candidate/{kind}"
        cand = {h: get_secret(db, f"{base}/{h}" if per_vm else base) or "" for h in targets}
        tried = [h for h in targets if cand[h]]
        if tried:
            r = probe(inv, tried, recovery_user(inv, kind) if kind.endswith("2") else None, {h: cand[h] for h in tried})
            took = [h for h in tried if r.get(h) in ("ok", "changed")]
            for h in took:
                store_password(db, env, kind, h, cand[h])
                if not kind.endswith("2"):
                    _hosts(inv)[h]["ansible_password"] = cand[h]
                put_secret(db, f"{base}/{h}" if per_vm else base, "")
            if took and not kind.endswith("2"):
                put_secret(db, f"environment/{env.id}/inventory", yaml.safe_dump(inv, sort_keys=False))
            if took and set(took) == set(targets if per_vm else targets[:1]):
                log.write(f"labape: the last attempted password signs in on {', '.join(took)}; stored\n")
                return "the last attempted password works; stored"
        try:
            return reset_with_pair(db, env, kind, log)
        except AccessError as exc:
            log.write(f"labape: the recovery pair couldn't: {exc}\n")
            if kind != "windows_admin":
                raise CheckError(f"no kept value signs in and the other admin of the pair couldn't reset it ({exc})") from exc
        dcs = env_targets(env, inv, "domain_admin")
        if not dcs:
            raise CheckError("no kept value signs in, the pair couldn't reset it, and there's no domain admin to")
        stored = {h: stored_password(db, env, inv, "windows_admin", h) for h in targets}
        domain_pw = _hosts(inv)[dcs[0]].get("ansible_password", "")
        netbios = str((inv.get("all", {}).get("vars") or {}).get("netbios_name") or "").upper()
        user = f"{netbios}\\Administrator" if netbios else "Administrator@" + str((inv.get("all", {}).get("vars") or {}).get("domain_name", ""))
        as_admin = {**inv, "all": {**inv["all"], "hosts": {h: {**_hosts(inv)[h], "ansible_password": domain_pw,
                                                               "ansible_user": user, "labape_new_pw": stored[h]}
                                                           for h in targets}}}
        r = _ansible(as_admin, ":".join(targets), "ansible.windows.win_user",
                     {"name": "Administrator", "password": "{{ labape_new_pw }}", "update_password": "always"},
                     scrub=(*stored.values(), domain_pw))
        bad = [h for h in targets if r.get(h) not in ("ok", "changed")]
        if bad:
            raise CheckError(f"the domain admin couldn't set it again on {', '.join(bad)}")
        log.write(f"labape: the domain admin set the stored password again on {', '.join(targets)}\n")
        return "the domain admin set the stored password again"
    return recover


HEALTH: dict[str, Health] = {
    "builtin_data_key": Health(False, "Without its data key the built-in store's secrets can't be read: restore the "
                                      "stack from a backup.", _builtin_decrypts),
    "master_key": Health(False, "The master key wraps the data keys: restore container/secrets (or the platform store's "
                                "copy) from a backup.", _builtin_decrypts),
    "postgres_password": Health(True, "The update agent sets a new password through the database container's own "
                                      "superuser.", _database),
    "ca_password": Health(False, "The CA password encrypts the CA's keys: restore secrets/ca_password with the labape-ca "
                                 "volume from a backup, or start a new CA (every certificate is issued again).", _ca_signs),
    "vault_password": Health(True, "An earlier password it kept that still opens the vault becomes the stored one.",
                             _vault_opens, _vault_recover),
    "bootstrap_ssh_key": Health(True, "A rotation makes a new key and adds it on the VMs with the current or a kept "
                                      "earlier key.", _ssh_pair),
    "worker_ssh_key": Health(True, "Each host's LABaPe access account authorizes the worker's key again.",
                             _worker_key, _worker_key_recover),
    "directory_token": Health(True, "The rotation account sets a new token key in Authentik.", _directory_token),
    "oidc_client_secret": Health(True, "The rotation account sets a new client secret in Authentik.", _oidc_secret),
    "elevated_account": Health(True, "akadmin's stored password signs in, sets a new key on the account's Authentik token, "
                                     "and the new key is stored.", _elevated_account, _elevated_restore),
    "authentik_admin": Health(True, "The rotation account sets a new akadmin password in Authentik.", _authentik_admin),
    "smtp_password": Health(False, "It's the mail server's: set the new password in Settings → Notifications.",
                            _smtp_login),
    "openbao_secret_id": Health(True, "The update agent runs stores.sh setup with the stores' root credentials kept on "
                                      "the host, which gives LABaPe a new login.", _store_login("openbao"), _stores_recover),
    "conjur_api_key": Health(True, "The update agent runs stores.sh setup with the stores' root credentials kept on the "
                                   "host, which gives LABaPe a new login.", _store_login("conjur"), _stores_recover),
    "windows_admin": Health(True, "The last attempted password if it signs in; else labape-admin2 (its recovery pair) "
                                  "sets the stored password again; else the domain admin does.", _env_password("windows_admin"),
                            _env_password_recover("windows_admin")),
    "domain_admin": Health(True, "The last attempted password if it signs in; else the domain's labape-admin2 (its "
                                 "recovery pair) sets the stored password again. Before a redeploy has made labape-admin2, "
                                 "a person (DSRM sign-in or a reset).", _env_password("domain_admin"),
                           _env_password_recover("domain_admin")),
    "windows_admin2": Health(True, "Administrator (its recovery pair) sets the stored password again, on each VM.",
                             _env_password("windows_admin2"), _env_password_recover("windows_admin2")),
    "domain_admin2": Health(True, "The domain Administrator (its recovery pair) sets the stored password again.",
                            _env_password("domain_admin2"), _env_password_recover("domain_admin2")),
    "env_account": Health(True, "LABaPe sets accounts as the VMs' admin, without the old password: a rotation sets a "
                                "new one."),
}


# --- scheduling --------------------------------------------------------------------------------

def check_interval(p: SecretPolicy) -> dt.timedelta | None:
    """Question 161: a quarter of the rotation period, at least a day; none when it rotates daily or
    more often; weekly when LABaPe doesn't rotate it."""
    from .rotation import REGISTRY
    r = REGISTRY.get(p.kind)
    if r is not None and r.automatic and p.enabled and not p.managed_externally:
        if p.interval_hours <= 24:
            return None
        return max(MIN_CHECK, dt.timedelta(hours=p.interval_hours / 4))
    return UNROTATED_CHECK


def describe(p: SecretPolicy) -> dict:
    h = HEALTH.get(p.kind)
    every = check_interval(p)
    return {"recoverable": bool(h and h.recoverable), "recovery": h.how if h else "",
            "checkable": bool(h and h.check), "check_every_hours": int(every.total_seconds() // 3600) if every else None}


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t.replace(tzinfo=dt.timezone.utc) if t is not None and t.tzinfo is None else t


def due(db: Session, now: dt.datetime | None = None) -> list[SecretPolicy]:
    now = now or utcnow()
    out = []
    for p in db.scalars(select(SecretPolicy)):
        h = HEALTH.get(p.kind)
        if h is None or h.check is None:
            continue
        every = check_interval(p)
        if every is None:
            if p.check_status != "none":
                p.check_status, p.check_error = "none", ""
            continue
        if p.next_check_at is None:                      # spread the first checks out
            p.next_check_at = now + dt.timedelta(minutes=5 + (p.id or 0) % 55)
        elif _aware(p.next_check_at) <= now:
            out.append(p)
    db.commit()
    return out


def schedule(db: Session, now: dt.datetime | None = None) -> list[int]:
    busy = {(j.params or {}).get("policy_id") for j in db.scalars(
        select(Job).where(Job.type.in_(("secret.check", "secret.rotate")), Job.state.in_(("queued", "running"))))}
    queued = []
    for p in due(db, now):
        if p.id in busy or p.status == "running":
            continue
        from .rotation import ENV_KINDS
        job = Job(type="secret.check", params={"policy_id": p.id}, requested_by="system",
                  environment_id=(p.params or {}).get("environment_id") if p.kind in ENV_KINDS else None)
        db.add(job)
        db.commit()
        queued.append(job.id)
    return queued


def _recover(db: Session, p: SecretPolicy, h: Health, log) -> str:
    if h.recover is not None:
        return h.recover(db, p, log)
    from .rotation import REGISTRY, RotationError, request_agent, uses_agent
    r = REGISTRY[p.kind]
    if r.needs_elevated and not p.elevated_ref:
        raise CheckError("recovery is a rotation, which needs an elevated account (choose one on the secret's page)")
    if uses_agent(db, p.kind) or r.via_agent or p.needs_restart:
        try:
            request_agent(db, p, "secret-check")
        except RotationError as exc:
            raise CheckError(str(exc)) from exc
        return "asked the update agent to set a new value"
    summary = r.run(db, p, log)
    p.status, p.last_error, p.last_rotated_at = "ok", "", utcnow()
    p.next_due_at = utcnow() + dt.timedelta(hours=p.interval_hours)
    return f"a new value was set: {summary}"


def run_check(db: Session, p: SecretPolicy, log, actor: str = "system") -> str:
    """Check one secret; recover it when it fails and can be; record the outcome. Returns the status."""
    h = HEALTH.get(p.kind)
    now = utcnow()
    every = check_interval(p) or UNROTATED_CHECK
    if h is None or h.check is None:
        p.check_status, p.check_error, p.last_checked_at = "none", "", now
        db.commit()
        log.write(f"labape: {p.label}: no check for this kind of secret\n")
        return "none"
    status, message = "ok", ""
    try:
        message = h.check(db, p)
    except NotInUse as exc:
        status, message = "none", str(exc)
    except CheckError as exc:
        log.write(f"labape: check failed: {exc}\n")
        message = str(exc)
        if not h.recoverable:
            status = "failed"
            message += f". Not recoverable by LABaPe: {h.how}"
        else:
            log.write(f"labape: recovering: {h.how}\n")
            try:
                done = _recover(db, p, h, log)
                if "asked the update agent" in done:
                    status, message = "recovering", done
                else:
                    try:
                        message = f"recovered ({done}); {h.check(db, p)}"
                        status = "recovered"
                    except (CheckError, NotInUse) as again:
                        status, message = "failed", f"recovery ran ({done}) but the check still fails: {again}"
            except Exception as rexc:  # noqa: BLE001 — recovery failing leaves the secret flagged
                db.rollback()
                p = db.get(SecretPolicy, p.id)
                status, message = "failed", f"{message}; recovery failed: {rexc}"
    except Exception as exc:  # noqa: BLE001 — the check itself broke: inconclusive
        status, message = "unchecked", f"the check couldn't run: {exc}"
    p.check_status, p.check_error, p.last_checked_at = status, message[:2000], now
    p.next_check_at = now + (RETRY_AFTER if status in ("failed", "recovering", "unchecked") else every)
    db.commit()
    audit(db, actor, "secret.check", object_type="secret_policy", object_id=p.id,
          outcome="ok" if status in ("ok", "recovered", "none") else "failed",
          detail={"name": p.name, "status": status, "message": message[:500]})
    log.write(f"labape: {p.label}: {status}: {message}\n")
    return status


def run_job(db: Session, job: Job) -> int:
    """The worker's `secret.check` job."""
    p = db.get(SecretPolicy, (job.params or {}).get("policy_id"))
    with open(job.log_path, "a", encoding="utf-8", buffering=1) as log:
        if p is None:
            log.write("labape: no such secret policy\n")
            return 1
        log.write(f"labape: checking {p.label}\n")
        return 0 if run_check(db, p, log, job.requested_by) in ("ok", "recovered", "none", "recovering") else 1


def alerts(db: Session) -> dict[str, tuple[None, str, str]]:
    """For monitoring: each secret whose check failed and wasn't recovered."""
    return {f"secret:{p.name}": (None, "error", f"secret {p.label}: {p.check_error[:300]}")
            for p in db.scalars(select(SecretPolicy).where(SecretPolicy.check_status == "failed"))}
