"""Which SSH host keys and WinRM certificate a VM is known by (question 198).

Commands, text sessions and browser SSH send a VM's password or key to whatever answers at its address.
So each VM's identity is recorded once and checked on every later connection from the web interface:
SSH host keys (the `type key` lines of a known_hosts file) and the SHA-256 of the WinRM listener's
certificate. The record is made right after a deploy (`record_env`, from what the VM answers then), or at
the first connection when there is none (trust on first use). A deploy forgets the old record first and a
destroy removes it, since a rebuilt VM has new keys. Ansible's own deploy-time connections still accept
what they first see; this closes the later ones. A VM rebuilt outside LABaPe is let in again with
`labape vm-identity forget ADDRESS`.
"""
from __future__ import annotations

import hashlib
import ssl
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Environment, VmIdentity, utcnow

SSH, WINRM = "ssh", "winrm"
CHANGED_HINT = ("LABaPe recorded a different SSH host key for this VM. If the VM was rebuilt outside LABaPe, an "
                "administrator can run `labape vm-identity forget <address>`; redeploying the environment "
                "records the new key. Otherwise treat this as a possible impersonation of the VM.")


def trusted(db: Session, address: str, kind: str) -> str | None:
    row = db.scalar(select(VmIdentity).where(VmIdentity.address == address, VmIdentity.kind == kind))
    return row.value if row else None


def remember(db: Session, address: str, kind: str, value: str, env_id: int | None = None, replace: bool = True) -> None:
    row = db.scalar(select(VmIdentity).where(VmIdentity.address == address, VmIdentity.kind == kind))
    if row is None:
        db.add(VmIdentity(address=address, kind=kind, value=value, environment_id=env_id))
    elif replace:
        row.value, row.environment_id, row.recorded_at = value, env_id, utcnow()
    db.commit()


def forget(db: Session, address: str | None = None, env_id: int | None = None) -> int:
    q = delete(VmIdentity)
    if address is not None:
        q = q.where(VmIdentity.address == address)
    elif env_id is not None:
        q = q.where(VmIdentity.environment_id == env_id)
    else:
        return 0
    n = db.execute(q).rowcount
    db.commit()
    return n or 0


# --- SSH --------------------------------------------------------------------------------------

def key_lines(text: str) -> str:
    """The `type key` part of each known_hosts or ssh-keyscan line (the host field is dropped; comments skipped)."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.startswith("#"):
            out.append(f"{parts[1]} {parts[2]}")
    return "\n".join(sorted(set(out)))


def known_hosts(address: str, value: str) -> str:
    """The known_hosts text for `address` from a stored value."""
    return "".join(f"{address} {line}\n" for line in value.splitlines() if line.strip())


def ssh_options(known_hosts_file: Path, address: str, value: str | None) -> list[str]:
    """ssh options that check the VM's host key against the record, or learn it into `known_hosts_file`
    when there is none (the caller stores what was learned with `learn_file`)."""
    known_hosts_file.write_text(known_hosts(address, value) if value else "", encoding="utf-8")
    return ["-o", f"UserKnownHostsFile={known_hosts_file}", "-o", "HashKnownHosts=no",
            "-o", f"StrictHostKeyChecking={'yes' if value else 'accept-new'}"]


def learn_file(address: str, known_hosts_file: Path, env_id: int | None = None) -> bool:
    """Store the keys ssh learned into `known_hosts_file` (only when nothing is recorded yet)."""
    from ..db import session_factory
    try:
        value = key_lines(known_hosts_file.read_text(encoding="utf-8"))
    except OSError:
        return False
    if not value:
        return False
    with session_factory()() as db:
        if trusted(db, address, SSH) is None:
            remember(db, address, SSH, value, env_id, replace=False)
            return True
    return False


def scan_ssh(address: str, port: int = 22, timeout: int = 8) -> str:
    r = subprocess.run(["ssh-keyscan", "-T", str(timeout), "-p", str(port), "-t", "ed25519,ecdsa,rsa", address],
                       capture_output=True, text=True, timeout=timeout + 5, check=False)
    return key_lines(r.stdout)


# --- WinRM ------------------------------------------------------------------------------------

def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fetch_certificate(address: str, port: int = 5986, timeout: float = 8.0) -> str:
    """SHA-256 (hex) of the certificate the WinRM listener presents now. Raises OSError when it can't be reached."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    import socket
    with socket.create_connection((address, port), timeout=timeout) as sock, ctx.wrap_socket(sock) as tls:
        return fingerprint(tls.getpeercert(binary_form=True) or b"")


# --- after a deploy ---------------------------------------------------------------------------

def record_env(db: Session, env: Environment, inventory: dict, log=None) -> dict[str, str]:
    """Record the SSH keys and WinRM certificates of an environment's VMs as they answer now. Best effort:
    a VM that doesn't answer is left unrecorded and is learned at its first connection. Environments on a
    remote KVM host are skipped (the scan can't tunnel); they are learned at the first connection."""
    from .vmaccess import _hosts, remote_env
    if remote_env(env):
        return {}
    todo = {vm: h for vm, h in _hosts(inventory).items() if h.get("ansible_host")}

    def one(item):
        vm, h = item
        address = str(h["ansible_host"])
        try:
            if h.get("ansible_connection") == "winrm":
                return vm, address, WINRM, fetch_certificate(address, int(h.get("ansible_port") or 5986))
            return vm, address, SSH, scan_ssh(address)
        except (OSError, subprocess.SubprocessError, ssl.SSLError) as exc:
            return vm, address, "", str(exc)

    done: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for vm, address, kind, value in pool.map(one, todo.items()):
            if kind and value:
                remember(db, address, kind, value, env.id)
                done[vm] = kind
            elif log:
                log(f"labape: could not record the identity of {vm} ({address}): {value or 'no key'}")
    return done
