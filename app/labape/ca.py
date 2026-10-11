"""The platform CA (design §22.5, Claude_Docs/Planning_Certificate-Authority.md §5).

step-ca runs in its own container. The API holds the JWK provisioner's
password and signs certificate requests with the step CLI: VMs make their
own key and CSR during a deploy, Ansible posts the CSR here with the job's
one-time token, and the CSR's names are checked against the environment
before anything is signed. VMs renew directly against step-ca (mTLS).
"""
from __future__ import annotations

import ipaddress
import subprocess
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

from .config import get_settings

PROVISIONER = "labape-runner"
LEAF_HOURS = 720            # 30 days (question 70c); step-ca allows up to 90


class CaError(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.ca_url) and s.ca_root.is_file() and s.ca_password_file.is_file()


def root_pem() -> str:
    s = get_settings()
    if not s.ca_root.is_file():
        raise CaError("the platform CA isn't set up (no root certificate)")
    return s.ca_root.read_text(encoding="ascii")


def csr_names(csr_pem: str) -> tuple[str, set[str], set[str]]:
    """(common name, DNS names, IP addresses) a CSR asks for."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
    except ValueError as exc:
        raise CaError(f"not a PEM certificate request: {exc}") from exc
    if not csr.is_signature_valid:
        raise CaError("the request's signature doesn't verify")
    cn = next((a.value for a in csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)), "")
    dns, ips = set(), set()
    try:
        san = csr.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        dns = {n.lower() for n in san.get_values_for_type(x509.DNSName)}
        ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    except x509.ExtensionNotFound:
        pass
    return str(cn).lower(), dns, ips


def check_names(csr_pem: str, host: str, allowed_dns: set[str], allowed_ips: set[str]) -> None:
    """A VM may only ask for its own names: its host name, FQDN and addresses."""
    cn, dns, ips = csr_names(csr_pem)
    names = {n for n in ({cn} | dns) if n}
    bad = sorted(n for n in names if n not in allowed_dns) + sorted(i for i in ips if i not in allowed_ips)
    if bad:
        raise CaError(f"{host} may not request: {', '.join(bad)}")
    for i in ips:
        ipaddress.ip_address(i)


def sign(csr_pem: str, hours: int = LEAF_HOURS) -> str:
    """Sign a CSR with the JWK provisioner; returns the leaf and intermediate (PEM)."""
    s = get_settings()
    if not configured():
        raise CaError("the platform CA isn't set up (LABAPE_CA_URL, root or password missing)")
    with tempfile.TemporaryDirectory() as d:
        csr, crt = Path(d) / "req.csr", Path(d) / "host.crt"
        csr.write_text(csr_pem, encoding="ascii")
        r = subprocess.run(["step", "ca", "sign", str(csr), str(crt), "--ca-url", s.ca_url, "--root", str(s.ca_root),
                            "--provisioner", PROVISIONER, "--provisioner-password-file", str(s.ca_password_file),
                            "--not-after", f"{hours}h", "--force"],
                           capture_output=True, text=True, timeout=60, check=False,
                           env={"HOME": str(Path(d)), "STEPPATH": str(Path(d) / ".step"), "PATH": "/usr/local/bin:/usr/bin:/bin"})
        if r.returncode != 0:
            raise CaError(f"step ca sign failed: {(r.stderr or r.stdout).strip()[-400:]}")
        return crt.read_text(encoding="ascii")


def health() -> tuple[bool | None, str]:
    s = get_settings()
    if not s.ca_url:
        return None, "Not set up (LABAPE_CA_URL)"
    if not s.ca_root.is_file():
        return False, f"No root certificate at {s.ca_root}"
    try:
        r = subprocess.run(["step", "ca", "health", "--ca-url", s.ca_url, "--root", str(s.ca_root)],
                           capture_output=True, text=True, timeout=15, check=False,
                           env={"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Couldn't run step: {exc}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:200]
    fp = subprocess.run(["step", "certificate", "fingerprint", str(s.ca_root)], capture_output=True, text=True,
                        timeout=15, check=False).stdout.strip()
    return True, f"Healthy; root fingerprint {fp[:16]}…, leaf certificates last {LEAF_HOURS // 24} days"
