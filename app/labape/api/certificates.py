"""Certificates from the platform CA (design §65, batch C2): the register, requests, revoking, the CA's own details.

Roles: `ca_admin` manages the CA (everything, the settings, revoking); `ca_requester` may request certificates and
sees their own; the auditor role and admins see all; everyone else sees the certificates they requested and the
ones of environments they have a grant on.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import appsettings, ca, certs
from ..db import get_db
from ..errors import FieldError
from ..models import Certificate, Environment
from ..permissions import can_see as can_see_object
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


class RequestIn(BaseModel):
    common_name: str = Field(default="", max_length=253)
    dns: list[str] = Field(default_factory=list, max_length=50)
    ips: list[str] = Field(default_factory=list, max_length=20)
    days: int | None = Field(default=None, ge=1, le=90)
    csr: str = Field(default="", max_length=20000)          # a CSR of the requester's own; else LABaPe makes the key
    environment_id: int | None = None

    @field_validator("dns", "ips", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str):
            v = v.replace(",", " ").split()
        return [x.strip().lower() for x in v if x and x.strip()]


@router.get("")
def list_certificates(scope: str = Query(default="mine", pattern="^(mine|all)$"), q: str = "", state: str = "",
                      source: str = "", limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0),
                      response: Response = None, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """`scope=all` is for those who see every certificate; `mine` is what the user requested or their environments hold."""
    if scope == "all" and not certs.sees_all(p):
        raise FieldError("scope", "Only CA administrators and auditors list every certificate.", status_code=403)
    rows = [c for c in db.scalars(certs.visible(db, p, everything=scope == "all").order_by(Certificate.not_after.desc()))]
    items = [certs.out(db, c) for c in rows]
    if q:
        needle = q.lower()
        items = [i for i in items if needle in " ".join([i["subject"], i["serial_hex"], i["requested_by"], i["host"],
                                                         i["environment"], *i["sans"]]).lower()]
    if state:
        items = [i for i in items if i["state"] == state]
    if source:
        items = [i for i in items if i["source"] == source]
    response.headers["X-Total-Count"] = str(len(items))
    return items[offset:offset + limit]


class SettingsIn(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    allowed_networks: list[str] = Field(default_factory=list, max_length=50)
    default_days: int = Field(default=30, ge=1, le=90)
    max_days: int = Field(default=90, ge=1, le=90)


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    cfg = appsettings.get(db, "certificates")
    return {**cfg.model_dump(), "always_allowed": sorted(certs._domains(db)), "can_edit": p.has_role("ca_admin")}


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("ca_admin"))):
    import ipaddress
    for n in body.allowed_networks:
        try:
            ipaddress.ip_network(n, strict=False)
        except ValueError as exc:
            raise FieldError("allowed_networks", f"{n!r} isn't a network (for example 10.0.0.0/8)") from exc
    if body.default_days > body.max_days:
        raise FieldError("default_days", "The default lifetime can't be longer than the longest.")
    model = appsettings.CertificateSettings(
        allowed_domains=[d.strip().lower().strip(".") for d in body.allowed_domains if d.strip()],
        allowed_networks=[n.strip() for n in body.allowed_networks if n.strip()],
        default_days=body.default_days, max_days=body.max_days)
    appsettings.put(db, "certificates", model)
    audit(db, p.username, "certificate.settings", detail=model.model_dump(), source_ip=client_ip(request))
    return model.model_dump()


@router.get("/ca")
def ca_details(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """The CA itself: its root, whether it answers, its provisioners, and counts from the register."""
    if not certs.sees_all(p):
        raise FieldError("ca", "Only CA administrators and auditors see the CA's details.", status_code=403)
    root = None
    try:
        info = certs.describe(ca.root_pem())
        root = {k: info[k] for k in ("subject", "serial", "not_before", "not_after", "fingerprint")}
    except (ca.CaError, certs.CertError):
        pass
    ok, why = ca.health()
    by_state: dict[str, int] = {}
    for c in db.scalars(select(Certificate)):
        s = certs.out(db, c)["state"]
        by_state[s] = by_state.get(s, 0) + 1
    return {"configured": ca.configured(), "root": root, "healthy": ok, "health": why, "url": ca.get_settings().ca_url,
            "provisioners": ca.provisioners(), "counts": by_state, "total": sum(by_state.values())}


@router.post("", status_code=201)
def request_certificate(body: RequestIn, request: Request, db: Session = Depends(get_db),
                        p: Principal = Depends(require_roles("ca_requester", "ca_admin"))):
    """Sign a certificate. With a CSR, only the certificate comes back; without one LABaPe makes a P-256 key pair and
    returns the private key once (it is not stored)."""
    cfg = appsettings.get(db, "certificates")
    try:
        if body.csr.strip():
            cn, dns_set, ip_set = ca.csr_names(body.csr)
            dns, ips = sorted(dns_set), sorted(ip_set)
            if cn and cn not in dns and not _is_ip(cn):
                dns = [cn, *dns]                                  # the common name is a name too: it is checked like the rest
        else:
            cn, dns, ips = body.common_name.strip().lower(), body.dns, body.ips
            if cn and cn not in dns and not _is_ip(cn):
                dns = [cn, *dns]
            if not (dns or ips):
                raise FieldError("dns", "Give at least one DNS name or IP address.")
        certs.check_names(db, p, [n for n in dns if n], ips)
    except (ca.CaError, certs.CertError) as exc:
        raise FieldError("csr" if body.csr.strip() else "dns", str(exc)) from exc
    days = body.days or cfg.default_days
    if days > cfg.max_days:
        raise FieldError("days", f"The longest lifetime is {cfg.max_days} days.")
    if body.environment_id is not None:
        env = db.get(Environment, body.environment_id)
        if env is None or not can_see_object(db, p, "environment", env.id):
            raise FieldError("environment_id", "No such environment.", status_code=404)
    key_pem = ""
    csr = body.csr
    try:
        if not csr.strip():
            key_pem, csr = certs.make_request(cn or (dns[0] if dns else ips[0]), [n for n in dns if n], ips)
        chain = ca.sign(csr, hours=days * 24)
        row = certs.record(db, chain, requested_by=p.username, source="request", environment_id=body.environment_id)
    except (ca.CaError, certs.CertError) as exc:
        audit(db, p.username, "certificate.request", outcome="failed", detail={"names": [*dns, *ips], "error": str(exc)[:300]},
              source_ip=client_ip(request))
        raise FieldError("csr", str(exc), status_code=502) from exc
    audit(db, p.username, "certificate.request", object_type="certificate", object_id=row.id,
          detail={"subject": row.subject, "names": row.sans, "days": days, "own_csr": bool(body.csr.strip())},
          source_ip=client_ip(request))
    return {"certificate": certs.out(db, row, with_pem=True), "chain": chain, "key": key_pem, "root": _root()}


def _is_ip(v: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def _root() -> str:
    try:
        return ca.root_pem()
    except ca.CaError:
        return ""


class ImportIn(BaseModel):
    pem: str = Field(min_length=100, max_length=40000)
    note: str = Field(default="", max_length=500)


@router.post("/import", status_code=201)
def import_certificate(body: ImportIn, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(require_roles("ca_admin"))):
    """Add a certificate the CA signed outside LABaPe (for example the stack's own before the register existed)."""
    try:
        row = certs.record(db, body.pem, requested_by=p.username, source="imported", note=body.note)
    except certs.CertError as exc:
        raise FieldError("pem", str(exc)) from exc
    audit(db, p.username, "certificate.import", object_type="certificate", object_id=row.id,
          detail={"subject": row.subject}, source_ip=client_ip(request))
    return certs.out(db, row)


def _get(db: Session, cert_id: int, p: Principal) -> Certificate:
    c = db.get(Certificate, cert_id)
    if c is None or not certs.can_see(db, p, c):
        raise FieldError("certificate", "No such certificate.", status_code=404)
    return c


@router.get("/{cert_id}")
def get_certificate(cert_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    return certs.out(db, _get(db, cert_id, p), with_pem=True)


@router.get("/{cert_id}/download")
def download_certificate(cert_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    c = _get(db, cert_id, p)
    name = (c.subject or c.serial).replace("*", "wildcard").replace("/", "_")
    return Response(c.pem, media_type="application/x-pem-file",
                    headers={"Content-Disposition": f'attachment; filename="{name}.crt"'})


class RevokeIn(BaseModel):
    reason: str = Field(default="", max_length=200)


@router.post("/{cert_id}/revoke")
def revoke_certificate(cert_id: int, body: RevokeIn, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(require_roles("ca_admin"))):
    """Mark the certificate revoked and tell step-ca. Revocation is passive: the CA refuses to renew it, but a client
    that never checks revocation still accepts it until it expires."""
    c = db.get(Certificate, cert_id)
    if c is None:
        raise FieldError("certificate", "No such certificate.", status_code=404)
    try:
        certs.revoke(db, c, p.username, body.reason)
    except (ca.CaError, certs.CertError) as exc:
        audit(db, p.username, "certificate.revoke", object_type="certificate", object_id=c.id, outcome="failed",
              detail={"error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("certificate", str(exc), status_code=409) from exc
    audit(db, p.username, "certificate.revoke", object_type="certificate", object_id=c.id,
          detail={"subject": c.subject, "reason": body.reason}, source_ip=client_ip(request))
    return certs.out(db, c)
