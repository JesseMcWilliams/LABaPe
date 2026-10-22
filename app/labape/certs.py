"""The platform CA's certificate register and requests (design §65, batch C2).

step-ca has no list a client can read, so LABaPe records every certificate it signs (a person's request, a deploy's
VM certificates, the stack's own through `labape ca-sign`) and the page shows that register. Only the public
certificate is kept; a key LABaPe makes for a requester is returned once and not stored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID as EKU
from cryptography.x509.oid import ExtensionOID, NameOID
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import appsettings, ca
from .models import Certificate, Environment, utcnow
from .permissions import visible_ids
from .security import Principal

END = "-----END CERTIFICATE-----"
WARN_DAYS = 14


class CertError(ValueError):
    pass


# Extra attributes a request may ask for (row 25 of the 2026-10-21 backlog). `eku` entries are extended key usages, `ku` entries
# key usages. "Signing" and "Encryption" are key usages; step-ca's default already gives serverAuth, clientAuth and
# digitalSignature, so a request with none chosen gets exactly that.
USES: dict[str, dict] = {
    "server_auth": {"label": "Server authentication", "kind": "eku", "oid": EKU.SERVER_AUTH, "name": "serverAuth"},
    "client_auth": {"label": "Client authentication", "kind": "eku", "oid": EKU.CLIENT_AUTH, "name": "clientAuth"},
    "code_signing": {"label": "Code signing", "kind": "eku", "oid": EKU.CODE_SIGNING, "name": "codeSigning"},
    "email_protection": {"label": "Email protection (S/MIME)", "kind": "eku", "oid": EKU.EMAIL_PROTECTION, "name": "emailProtection"},
    "time_stamping": {"label": "Time stamping", "kind": "eku", "oid": EKU.TIME_STAMPING, "name": "timeStamping"},
    "ocsp_signing": {"label": "OCSP signing", "kind": "eku", "oid": EKU.OCSP_SIGNING, "name": "ocspSigning"},
    "smartcard_logon": {"label": "Smart-card logon", "kind": "eku", "oid": x509.ObjectIdentifier("1.3.6.1.4.1.311.20.2.2"),
                        "name": "1.3.6.1.4.1.311.20.2.2"},
    "signing": {"label": "Signing (digital signature, non-repudiation)", "kind": "ku", "name": ["digitalSignature", "contentCommitment"]},
    "encryption": {"label": "Encryption (key and data encipherment)", "kind": "ku", "name": ["keyEncipherment", "dataEncipherment"]},
}
# What the CA gives with no attributes chosen: nothing is promised beyond it, so asking for only these needs no template.
DEFAULT_USES = {"server_auth", "client_auth"}


def check_uses(uses: list[str], enabled: bool) -> list[str]:
    """The requested attributes, de-duplicated and in table order; refused when unknown, or when the CA is not set up for them."""
    bad = [u for u in uses if u not in USES]
    if bad:
        raise CertError("unknown attribute " + ", ".join(bad) + "; choose from " + ", ".join(USES))
    out = [u for u in USES if u in set(uses)]
    if out and set(out) != DEFAULT_USES and not enabled:
        raise CertError("Extra attributes are off: a CA administrator turns them on (Certificates, The CA) once the CA's "
                        "template is installed (container/ca/enable-attributes.sh). Without it the CA would ignore them.")
    return out


def sign_vars(uses: list[str]) -> dict | None:
    """The template data for `step ca sign --set-file` (container/ca/leaf.tpl); None when the CA's defaults are enough."""
    if not uses or set(uses) == DEFAULT_USES:
        return None
    eku = [USES[u]["name"] for u in uses if USES[u]["kind"] == "eku"]
    ku = [n for u in uses if USES[u]["kind"] == "ku" for n in USES[u]["name"]]
    return {"eku": eku, "ku": ku or ["digitalSignature"]}


def uses_of(c: x509.Certificate) -> list[str]:
    """The attributes (keys of USES) a certificate carries, from its extended key usage and key usage extensions."""
    got: set[str] = set()
    try:
        ekus = set(c.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value)
        got |= {k for k, u in USES.items() if u["kind"] == "eku" and u["oid"] in ekus}
    except x509.ExtensionNotFound:
        pass
    try:
        ku = c.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
        if ku.digital_signature and ku.content_commitment:
            got.add("signing")
        if ku.key_encipherment and ku.data_encipherment:
            got.add("encryption")
    except x509.ExtensionNotFound:
        pass
    return [k for k in USES if k in got]


def verify_uses(chain: str, uses: list[str]) -> None:
    """After signing: the certificate must carry what was asked for. step-ca ignores attributes its provisioner has no
    template for, so a miss means the CA is not set up for them; the certificate is then revoked, not handed out."""
    if not uses or set(uses) == DEFAULT_USES:
        return
    c = _leaf(chain)
    missing = [u for u in uses if u not in uses_of(c)]
    if missing:
        try:
            ca.revoke(str(c.serial_number), "attributes not applied")
        except ca.CaError:
            pass
        raise CertError("The CA did not apply " + ", ".join(USES[m]["label"] for m in missing) + " (its provisioner has no "
                        "template for attributes: run container/ca/enable-attributes.sh). The certificate was revoked.")


def _leaf(pem: str) -> x509.Certificate:
    block = pem.strip().split(END)[0] + END
    try:
        return x509.load_pem_x509_certificate(block.encode())
    except ValueError as exc:
        raise CertError(f"not a PEM certificate: {exc}") from exc


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def describe(pem: str) -> dict:
    """What the register keeps about a certificate (the first one in a chain: the leaf)."""
    c = _leaf(pem)
    cn = next((a.value for a in c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)), "")
    issuer = next((a.value for a in c.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)), "")
    sans: list[str] = []
    try:
        san = c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        sans = sorted(san.get_values_for_type(x509.DNSName)) + sorted(str(i) for i in san.get_values_for_type(x509.IPAddress))
    except x509.ExtensionNotFound:
        pass
    return {"serial": str(c.serial_number), "subject": str(cn), "sans": sans, "issuer": str(issuer),
            "not_before": c.not_valid_before_utc, "not_after": c.not_valid_after_utc,
            "fingerprint": hashlib.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()}


def record(db: Session, chain: str, *, requested_by: str, source: str, environment_id: int | None = None,
           host: str = "", note: str = "") -> Certificate:
    """Add a signed certificate to the register (once: the serial number is unique)."""
    info = describe(chain)
    row = db.scalar(select(Certificate).where(Certificate.serial == info["serial"]))
    if row is not None:
        return row
    row = Certificate(**info, requested_by=requested_by, source=source, environment_id=environment_id, host=host[:64],
                      note=note, pem=chain.strip() + "\n")
    db.add(row)
    db.commit()
    return row


def _uses(c: Certificate) -> list[str]:
    try:
        return uses_of(_leaf(c.pem)) if c.pem else []
    except CertError:
        return []


def out(db: Session, c: Certificate, with_pem: bool = False) -> dict:
    now = utcnow()
    left = (_aware(c.not_after) - now).total_seconds() / 86400
    state = "revoked" if c.status == "revoked" else "expired" if left < 0 else "expiring" if left < WARN_DAYS else "active"
    env = db.get(Environment, c.environment_id) if c.environment_id else None
    d = {"id": c.id, "serial": c.serial, "serial_hex": format(int(c.serial), "x"), "subject": c.subject, "sans": c.sans,
         "not_before": c.not_before, "not_after": c.not_after, "days_left": int(left) if left >= 0 else 0, "state": state,
         "fingerprint": c.fingerprint, "issuer": c.issuer, "requested_by": c.requested_by, "environment_id": c.environment_id,
         "environment": env.name if env else "", "host": c.host, "source": c.source, "note": c.note,
         "uses": _uses(c), "created_at": c.created_at, "revoked_at": c.revoked_at, "revoked_by": c.revoked_by, "revoke_reason": c.revoke_reason}
    if with_pem:
        d["pem"] = c.pem
    return d


# --- who sees what ------------------------------------------------------------------------------------------

def sees_all(p: Principal) -> bool:
    """Admins, CA administrators, CA auditors and auditors see every certificate (question 193)."""
    return p.has_role("ca_admin", "ca_auditor", "auditor")


def visible(db: Session, p: Principal, everything: bool = True):
    """Certificates the user may see: all of them for those who see all (when `everything`), else the ones they
    requested and the ones of environments they have a grant on."""
    q = select(Certificate)
    if everything and sees_all(p):
        return q
    mine = Certificate.requested_by == p.username
    if sees_all(p):                                   # "My certificates" for someone who could see all
        return q.where(mine)
    envs = visible_ids(db, p, "environment") or set()
    return q.where(or_(mine, Certificate.environment_id.in_(envs)) if envs else mine)


def can_see(db: Session, p: Principal, c: Certificate) -> bool:
    return db.scalar(visible(db, p).where(Certificate.id == c.id)) is not None


# --- requests ----------------------------------------------------------------------------------------------------

def _domains(db: Session) -> set[str]:
    from .auth.oidc import stack_names
    cfg = appsettings.get(db, "certificates")
    out = {d.lower().strip(".") for d in cfg.allowed_domains if d.strip()}
    out |= set(stack_names())                         # the stack's own names
    from .config import get_settings
    host = urlsplit(get_settings().public_url).hostname or ""
    if host.count(".") >= 2 and not host.replace(".", "").isdigit():
        out.add(host.split(".", 1)[1].lower())        # the parent domain of the address (jlm.abq for oz.jlm.abq)
    return out


def check_names(db: Session, p: Principal, dns: list[str], ips: list[str]) -> None:
    """Names must be valid; a requester without the CA administrator role may use only the allowed domains and networks."""
    for i in ips:
        try:
            ipaddress.ip_address(i)
        except ValueError as exc:
            raise CertError(f"{i!r} isn't an IP address") from exc
    for n in dns:
        body = n[2:] if n.startswith("*.") else n
        if not body or any(not (c.isalnum() or c in "-.") for c in body) or body.startswith(".") or ".." in body:
            raise CertError(f"{n!r} isn't a DNS name")
    if p.has_role("ca_admin"):
        return
    cfg = appsettings.get(db, "certificates")
    domains = _domains(db)
    nets = [ipaddress.ip_network(x, strict=False) for x in cfg.allowed_networks]
    bad = [n for n in dns if n.startswith("*.") or not any(n.lower() == d or n.lower().endswith("." + d) for d in domains)]
    bad += [i for i in ips if not any(ipaddress.ip_address(i) in net for net in nets)]
    if bad:
        raise CertError("You may not request " + ", ".join(bad) + ". Allowed: names under " + ", ".join(sorted(domains))
                        + " (no wildcards), and addresses in " + ", ".join(str(n) for n in nets) + ".")


def make_request(cn: str, dns: list[str], ips: list[str], uses: list[str] | None = None) -> tuple[str, str]:
    """A new key pair (P-256) and its CSR; returns (private key PEM, CSR PEM)."""
    key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName(n) for n in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    b = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
    if names:
        b = b.add_extension(x509.SubjectAlternativeName(names), critical=False)
    ekus = [USES[u]["oid"] for u in uses or [] if USES[u]["kind"] == "eku"]
    if ekus:                                       # informational: the CA's template decides (container/ca/leaf.tpl)
        b = b.add_extension(x509.ExtendedKeyUsage(ekus), critical=False)
    csr = b.sign(key, hashes.SHA256())
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            csr.public_bytes(serialization.Encoding.PEM).decode())


def revoke(db: Session, c: Certificate, by: str, reason: str) -> None:
    if c.status == "revoked":
        raise CertError("it is already revoked")
    ca.revoke(c.serial, reason)
    c.status, c.revoked_at, c.revoked_by, c.revoke_reason = "revoked", utcnow(), by, reason[:255]
    db.commit()
