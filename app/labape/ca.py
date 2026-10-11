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


# --- the signing provisioner's key (phase R3) --------------------------------
# step-ca stores labape-runner's private JWK as a compact JWE encrypted with
# the CA password (PBES2-HS256+A128KW, A256GCM). `step crypto jwe encrypt`
# only prompts for a password, so the agent's rotation does it here.

def _b64d(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _pbes2_kek(header: dict, password: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    if header.get("alg") != "PBES2-HS256+A128KW":
        raise CaError(f"unexpected key-encryption algorithm {header.get('alg')}")
    salt = header["alg"].encode() + b"\x00" + _b64d(header["p2s"])
    return PBKDF2HMAC(hashes.SHA256(), 16, salt, int(header["p2c"])).derive(password)


def jwe_decrypt(token: str, password: bytes) -> bytes:
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
    protected, ek, iv, ct, tag = token.split(".")
    header = json.loads(_b64d(protected))
    if header.get("enc") != "A256GCM":
        raise CaError(f"unexpected content encryption {header.get('enc')}")
    cek = aes_key_unwrap(_pbes2_kek(header, password), _b64d(ek))
    return AESGCM(cek).decrypt(_b64d(iv), _b64d(ct) + _b64d(tag), protected.encode())


def jwe_encrypt(plaintext: bytes, password: bytes, template: dict) -> str:
    import json
    import os as _os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
    header = {k: v for k, v in template.items() if k not in ("p2s",)}
    header.update(alg="PBES2-HS256+A128KW", enc="A256GCM", p2s=_b64e(_os.urandom(16)),
                  p2c=int(template.get("p2c", 600000)))
    protected = _b64e(json.dumps(header, separators=(",", ":")).encode())
    cek, iv = _os.urandom(32), _os.urandom(12)
    ek = aes_key_wrap(_pbes2_kek(header, password), cek)
    sealed = AESGCM(cek).encrypt(iv, plaintext, protected.encode())
    return ".".join((protected, _b64e(ek), _b64e(iv), _b64e(sealed[:-16]), _b64e(sealed[-16:])))


def repass_provisioner(config: str, old_password: bytes, new_password: bytes) -> None:
    """Re-encrypt labape-runner's key in ca.json with the new CA password."""
    import json
    from pathlib import Path as _P
    path = _P(config)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    prov = next(p for p in cfg["authority"]["provisioners"] if p.get("name") == PROVISIONER)
    token = prov["encryptedKey"]
    jwk = jwe_decrypt(token, old_password)
    header = json.loads(_b64d(token.split(".")[0]))
    new_token = jwe_encrypt(jwk, new_password, header)
    if jwe_decrypt(new_token, new_password) != jwk:
        raise CaError("the re-encrypted key didn't read back")
    prov["encryptedKey"] = new_token
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent="\t"), encoding="utf-8")
    tmp.replace(path)


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
