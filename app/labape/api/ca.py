"""Platform CA endpoints (design §22.5): the root certificate for anyone,
and signing for deploy jobs (one-time token per job)."""
from __future__ import annotations

import hmac
import ipaddress

import yaml
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ca
from ..config import get_settings
from ..db import get_db
from ..errors import FieldError
from ..models import Environment, IpAllocation, Job, Network
from ..naming import instance_names
from ..security import audit, client_ip, get_secret

router = APIRouter(tags=["ca"])


def token_name(job_id: int) -> str:
    return f"job/{job_id}/cert_token"


@router.get("/api/ca/root.crt")
def root_certificate():
    """Public: browsers and hosts trust this root (one anchor for every environment)."""
    try:
        pem = ca.root_pem()
    except ca.CaError as exc:
        raise FieldError("ca", str(exc), status_code=404) from exc
    return Response(pem, media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="labape-root-ca.crt"'})


class SignIn(BaseModel):
    host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
    csr: str = Field(min_length=100, max_length=20000)


def _domain() -> str:
    try:
        env_yml = yaml.safe_load((get_settings().config_dir / "environment.yml").read_text(encoding="utf-8")) or {}
    except OSError:
        return ""
    return str(env_yml.get("domain_name") or "").lower()


@router.post("/api/engine/certificates")
def sign_certificate(body: SignIn, request: Request, db: Session = Depends(get_db),
                     x_labape_job: int = Header(...), x_labape_token: str = Header(...)):
    """Called by a deploy job's Ansible run: sign one VM's CSR."""
    job = db.get(Job, x_labape_job)
    expected = get_secret(db, token_name(x_labape_job)) or "" if job else ""
    if job is None or job.state != "running" or not expected or not hmac.compare_digest(expected, x_labape_token):
        raise FieldError("token", "Not a running job's certificate token.", status_code=403)
    env = db.get(Environment, job.environment_id) if job.environment_id else None
    names = {n for g in ((env.spec or {}).get("host_groups", []) if env else []) for n in instance_names(g)}
    if body.host not in names:
        raise FieldError("host", f"{body.host} isn't a host of this job's environment.", status_code=403)
    domain = _domain()
    allowed_dns = {body.host.lower()} | ({f"{body.host.lower()}.{domain}"} if domain else set())
    # Addresses: the host's static allocation, or anything in the networks the environment uses (DHCP).
    allowed_ips = {a.address for a in db.scalars(select(IpAllocation).where(
        IpAllocation.environment_id == env.id, IpAllocation.vm_name == body.host))}
    nets = {g.get("network") for g in (env.spec or {}).get("host_groups", []) if g.get("network")}
    cidrs = [ipaddress.ip_network(n.cidr, strict=False) for n in db.scalars(select(Network)) if not nets or n.name in nets]
    _, _, ips = ca.csr_names(body.csr)
    allowed_ips |= {i for i in ips if any(ipaddress.ip_address(i) in c for c in cidrs)}
    try:
        ca.check_names(body.csr, body.host, allowed_dns, allowed_ips)
        chain = ca.sign(body.csr)
    except ca.CaError as exc:
        audit(db, job.requested_by, "ca.sign", object_type="job", object_id=job.id, outcome="denied",
              detail={"host": body.host, "error": str(exc)}, source_ip=client_ip(request))
        raise FieldError("csr", str(exc), status_code=422) from exc
    audit(db, job.requested_by, "ca.sign", object_type="job", object_id=job.id,
          detail={"host": body.host, "environment": env.name}, source_ip=client_ip(request))
    return {"certificate": chain, "root": ca.root_pem(), "renew_url": get_settings().ca_renew_url}
