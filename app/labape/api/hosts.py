"""KVM host registration (admin only), capped at LABAPE_MAX_KVM_HOSTS.

Hosts are local (qemu:///system, the libvirt socket mounted into the
container) or remote over SSH (qemu+ssh://user@host/system, design §22.4).
"""
from __future__ import annotations

import re
import subprocess
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..engine import hostio
from ..engine.hoststats import assess
from ..errors import FieldError
from ..models import HostStats, KvmHost
from ..permissions import visible_hosts
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


def _resources(db: Session, host_id: int, demand: dict | None = None) -> dict:
    row = db.get(HostStats, host_id)
    if row is None or not row.stats:
        return {"collected_at": row.collected_at if row else None, "error": row.error if row else "",
                "stats": None, "assessment": None}
    stats = {k: v for k, v in row.stats.items() if not k.startswith("_")}
    return {"collected_at": row.collected_at, "error": row.error, "stats": stats,
            "assessment": assess(row.stats, demand)}


@router.get("")
def list_hosts(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    return [{**_out(h), "resources": _resources(db, h.id)} for h in visible_hosts(db, p)]


@router.get("/worker-key")
def worker_key(_: Principal = Depends(require_roles("admin"))):
    """The public key to add to a remote KVM host's authorized_keys."""
    return {"public_key": hostio.ensure_key()}


def _check_uri(uri: str) -> None:
    if uri.startswith("qemu:///"):
        if uri != "qemu:///system":
            raise FieldError("libvirt_uri", "Use qemu:///system for this machine.")
        return
    if not uri.startswith("qemu+ssh://"):
        raise FieldError("libvirt_uri", "Use qemu:///system for this machine, or qemu+ssh://user@host/system.")
    parts = urlsplit(uri)
    if not parts.hostname or not parts.username:
        raise FieldError("libvirt_uri", "A remote host needs the service user and host: qemu+ssh://user@host/system.")
    if parts.path not in ("", "/system") or not re.match(r"^[A-Za-z0-9._-]+$", parts.username):
        raise FieldError("libvirt_uri", "Use the form qemu+ssh://user@host/system (an optional :port after the host).")


@router.get("/{host_id}")
def get_host(host_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    h = next((h for h in visible_hosts(db, p) if h.id == host_id), None)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    return {**_out(h), "resources": _resources(db, h.id)}


@router.get("/{host_id}/resources")
def host_resources(host_id: int, vcpus: int = 0, memory_mb: int = 0, disk_gb: int = 0,
                   db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    """The host's resources and load; with vcpus/memory_mb/disk_gb, as if
    that much more were deployed (the deploy form's projection)."""
    if db.get(KvmHost, host_id) is None:
        raise HTTPException(status_code=404, detail="No such host")
    return _resources(db, host_id, {"vcpus": vcpus, "memory_mb": memory_mb, "disk_gb": disk_gb})


@router.post("", status_code=201)
def create_host(body: HostIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    limit = get_settings().max_kvm_hosts
    if db.scalar(select(func.count()).select_from(KvmHost)) >= limit:
        raise HTTPException(status_code=409, detail=f"At most {limit} KVM hosts (LABAPE_MAX_KVM_HOSTS)")
    _check_uri(body.libvirt_uri)
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
    _check_uri(body.libvirt_uri)
    for k, v in body.model_dump().items():
        setattr(h, k, v)
    db.commit()
    audit(db, p.username, "host.update", object_type="kvm_host", object_id=h.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(h)


@router.post("/{host_id}/check")
def check_host(host_id: int, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    """Can the worker reach the host, and does it have what jobs need?"""
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    out: dict = {"remote": hostio.is_remote(h.libvirt_uri), "libvirt": "", "problems": []}
    if out["remote"]:
        hostio.ensure_key()
        hostio.Host(h.libvirt_uri).prime()
    try:
        r = subprocess.run(["virsh", "--connect", hostio.effective_uri(h.libvirt_uri), "version"], capture_output=True,
                           text=True, timeout=30, env=hostio.tool_env(), check=False)
        if r.returncode == 0:
            out["libvirt"] = " ".join(line.strip() for line in r.stdout.splitlines() if "Running hypervisor" in line)
        else:
            out["problems"].append(f"libvirt: {(r.stderr or r.stdout).strip()[:300]}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["problems"].append(f"libvirt: {exc}")
    if out["remote"]:
        try:
            c = hostio.Host(h.libvirt_uri).python(hostio.CHECK, {"paths": [h.vm_storage_path, h.template_storage_path]})
            out["tools"], out["paths"] = c["tools"], c["paths"]
            missing = [t for t, ok in c["tools"].items() if not ok]
            if missing:
                out["problems"].append(f"missing on the host: {', '.join(missing)} "
                                       "(install-labape.py --profile kvm installs them)")
            for path, ok in c["paths"].items():
                if not ok:
                    out["problems"].append(f"{path} doesn't exist or isn't writable by the service user")
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            out["problems"].append(f"SSH: {exc}")
    out["ok"] = not out["problems"]
    audit(db, p.username, "host.check", object_type="kvm_host", object_id=h.id, outcome="ok" if out["ok"] else "failed",
          detail={"problems": out["problems"]}, source_ip=client_ip(request))
    return out
