"""The engine's secrets as catalog secrets (design §26, phase S2).

The Ansible vault password, the encrypted engine vault and the VM bootstrap
SSH key (with its previous keys) used to be files in container/engine-secrets/,
rotated by the host's update agent (§24.3). Once imported they're ordinary
catalog secrets in the platform store (OpenBao, question 104):

    platform/engine/vault_password           the vault password
    platform/engine/vault_password_history   JSON list, newest first: earlier passwords,
                                             so the command line can re-encrypt its own
                                             vault after a rotation (scripts/lib/store_get.py)
    platform/engine/vault                    the encrypted vault file
    platform/engine/ssh_key, ssh_key_pub     the VM key
    platform/engine/ssh_history              JSON list, newest first: {"stamp", "private", "public"}

Each job takes its own copies when it starts; the worker rotates them (no
restart, no agent). Until the import, the files are used as before.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Secret
from ..security import get_secret, put_secret

P = "platform/engine/"
VAULT_PASSWORD = P + "vault_password"
VAULT_PASSWORD_HISTORY = P + "vault_password_history"
VAULT = P + "vault"
SSH_KEY = P + "ssh_key"
SSH_PUB = P + "ssh_key_pub"
SSH_HISTORY = P + "ssh_history"
NAMES = (VAULT_PASSWORD, VAULT_PASSWORD_HISTORY, VAULT, SSH_KEY, SSH_PUB, SSH_HISTORY)
KEEP = 7                                   # previous VM keys and vault passwords kept


class EngineSecretsError(RuntimeError):
    pass


def in_catalog(db: Session) -> bool:
    return db.get(Secret, VAULT_PASSWORD) is not None and db.get(Secret, VAULT) is not None


def _get(db: Session, name: str) -> str:
    value = get_secret(db, name)
    if value is None:
        raise EngineSecretsError(f"{name} is missing from the secret store")
    return value


def _history(db: Session, name: str) -> list:
    try:
        return json.loads(get_secret(db, name) or "[]")
    except ValueError:
        return []


# --- import (stores.sh runs `labape engine-secrets import`) -------------------------------------

def import_files(db: Session, store: str, force: bool = False) -> list[str]:
    """Copy container/engine-secrets/ into the catalog and move it to `store`."""
    from ..stores import move
    if in_catalog(db) and not force:
        return []
    src = get_settings().secrets_dir
    vault, pw = src / "secrets.vault.yml", src / "vault-pass"
    key, pub = src / "ssh" / "labape_bootstrap", src / "ssh" / "labape_bootstrap.pub"
    missing = [str(f) for f in (vault, pw, key, pub) if not f.is_file()]
    if missing:
        raise EngineSecretsError(f"can't import, missing: {', '.join(missing)}")
    history = []
    hist_dir = src / "ssh" / "history"
    for f in sorted(hist_dir.glob("labape_bootstrap-*"), reverse=True) if hist_dir.is_dir() else []:
        if f.suffix != ".pub" and f.with_name(f.name + ".pub").is_file():
            history.append({"stamp": f.name.removeprefix("labape_bootstrap-"), "private": f.read_text(),
                            "public": f.with_name(f.name + ".pub").read_text()})
    values = {VAULT_PASSWORD: pw.read_text().strip(), VAULT_PASSWORD_HISTORY: "[]", VAULT: vault.read_text(),
              SSH_KEY: key.read_text(), SSH_PUB: pub.read_text(), SSH_HISTORY: json.dumps(history[:KEEP])}
    done = []
    for name, value in values.items():
        put_secret(db, name, value)
        done.append(f"{name}: {move(db, name, store)}" if store != "builtin" else name)
    return done


# --- each job's copies --------------------------------------------------------------------------

def _opens(vault: Path, pw: Path) -> bool:
    if not shutil.which("ansible-vault"):
        return True
    return subprocess.run(["ansible-vault", "view", "--vault-password-file", str(pw), str(vault)],
                          capture_output=True, timeout=60, check=False).returncode == 0


def write_vault(db: Session, work: Path) -> None:
    """The job's vault and password. A rotation writes the two one after the
    other, so a pair read in between doesn't open: read it again."""
    vault, pw = work / "secrets.vault.yml", work / ".vault-pass"
    for _ in range(5):
        vault.write_text(_get(db, VAULT), encoding="utf-8")
        pw.write_text(_get(db, VAULT_PASSWORD) + "\n", encoding="utf-8")
        os.chmod(vault, 0o600)
        os.chmod(pw, 0o600)
        if _opens(vault, pw):
            return
        time.sleep(2)
    raise EngineSecretsError("the stored vault doesn't open with the stored password")


def write_ssh(db: Session, ssh_dir: Path) -> None:
    """The VM key and its previous keys into the job HOME's .ssh (history/ beside it)."""
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    hist = ssh_dir / "history"
    shutil.rmtree(hist, ignore_errors=True)
    hist.mkdir()
    os.chmod(hist, 0o700)
    files = {ssh_dir / "labape_bootstrap": (_get(db, SSH_KEY), 0o600),
             ssh_dir / "labape_bootstrap.pub": (_get(db, SSH_PUB), 0o644)}
    for h in _history(db, SSH_HISTORY):
        files[hist / f"labape_bootstrap-{h['stamp']}"] = (h["private"], 0o600)
        files[hist / f"labape_bootstrap-{h['stamp']}.pub"] = (h["public"], 0o644)
    for path, (text, mode) in files.items():
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        os.chmod(path, mode)


# --- rotation by the worker (replaces the agent's R2 rotations once imported) -------------------

def rotate_vault_password(db: Session, log) -> str:
    """New password; the vault re-encrypted with it and checked; then both stored.
    The old password goes into the history the command line uses to catch up."""
    old_pw, old_vault = _get(db, VAULT_PASSWORD), _get(db, VAULT)
    import secrets as _secrets
    new_pw = _secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as d:
        vault, cur, new = Path(d) / "vault.yml", Path(d) / "cur", Path(d) / "new"
        for path, text in ((vault, old_vault), (cur, old_pw + "\n"), (new, new_pw + "\n")):
            path.write_text(text, encoding="utf-8")
            os.chmod(path, 0o600)
        r = subprocess.run(["ansible-vault", "rekey", "--vault-password-file", str(cur),
                            "--new-vault-password-file", str(new), str(vault)], capture_output=True, timeout=120)
        if r.returncode != 0 or not _opens(vault, new):
            raise EngineSecretsError("re-encrypting the vault failed; nothing changed")
        new_vault = vault.read_text(encoding="utf-8")
    log.write("labape: vault re-encrypted with a new password and checked\n")
    history = [old_pw] + [p for p in _history(db, VAULT_PASSWORD_HISTORY) if p != old_pw]
    put_secret(db, VAULT_PASSWORD_HISTORY, json.dumps(history[:KEEP]))
    try:
        put_secret(db, VAULT, new_vault)
        put_secret(db, VAULT_PASSWORD, new_pw)
    except Exception:
        put_secret(db, VAULT, old_vault)
        put_secret(db, VAULT_PASSWORD, old_pw)
        raise
    log.write("labape: new vault and password stored; jobs and the command line use them from now on\n")
    return "new vault password in use (the command line re-encrypts its vault on its next run)"


def rotate_ssh_key(db: Session, log) -> str:
    """New ed25519 key, added on every deployed environment's Linux VMs, then stored."""
    from .vmaccess import AccessError, push_vm_key
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as d:
        new = Path(d) / "new"
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"labape-bootstrap-{stamp}",
                            "-f", str(new)], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise EngineSecretsError("ssh-keygen failed; nothing changed")
        ssh = Path(d) / "ssh"
        write_ssh(db, ssh)
        try:
            report = push_vm_key(db, new, ssh / "labape_bootstrap", ssh / "history", log)
        except AccessError as exc:
            raise EngineSecretsError(f"adding the new key on the VMs failed: {exc}; the old key stays") from exc
        history = [{"stamp": stamp, "private": _get(db, SSH_KEY), "public": _get(db, SSH_PUB)}]
        history += _history(db, SSH_HISTORY)
        put_secret(db, SSH_HISTORY, json.dumps(history[:KEEP]))
        put_secret(db, SSH_KEY, new.read_text())
        put_secret(db, SSH_PUB, (Path(d) / "new.pub").read_text())
    log.write(f"labape: new VM key stored; previous keys kept: {min(len(history), KEEP)}\n")
    return f"new VM key in use ({json.dumps(report)[:300]})"
