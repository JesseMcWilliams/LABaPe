"""KVM host registration (admin only), capped at LABAPE_MAX_KVM_HOSTS."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import KvmHost
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


class HostIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    libvirt_uri: str = "qemu:///system"
    vm_storage_path: str = "/data/VMs/LABaPe"
    template_storage_path: str = "/data/VMs/LABaPe/templates"
    bridge: str = "br0"
    concurrency_limit: int = Field(default=2, ge=1, le=16)
    enabled: bool = True


def _out(h: KvmHost) -> dict:
    return {c: getattr(h, c) for c in ("id", "name", "libvirt_uri", "vm_storage_path", "template_storage_path",
                                       "bridge", "concurrency_limit", "enabled")}


@router.get("")
def list_hosts(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    return [_out(h) for h in db.scalars(select(KvmHost).order_by(KvmHost.name))]


@router.post("", status_code=201)
def create_host(body: HostIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    limit = get_settings().max_kvm_hosts
    if db.scalar(select(func.count()).select_from(KvmHost)) >= limit:
        raise HTTPException(status_code=409, detail=f"At most {limit} KVM hosts (LABAPE_MAX_KVM_HOSTS)")
    if not body.libvirt_uri.startswith(("qemu:///", "qemu+ssh://")):
        raise HTTPException(status_code=422, detail="libvirt_uri must be qemu:///system or qemu+ssh://...")
    if body.libvirt_uri.startswith("qemu+ssh://"):
        # Remote libvirt is phase 10e (design §15); the engine still assumes local storage paths.
        raise HTTPException(status_code=422, detail="Remote (qemu+ssh) hosts arrive in phase 10e")
    h = KvmHost(**body.model_dump())
    db.add(h)
    db.commit()
    audit(db, p.username, "host.create", object_type="kvm_host", object_id=h.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(h)


@router.put("/{host_id}")
def update_host(host_id: int, body: HostIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    for k, v in body.model_dump().items():
        setattr(h, k, v)
    db.commit()
    audit(db, p.username, "host.update", object_type="kvm_host", object_id=h.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(h)
