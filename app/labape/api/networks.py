"""Network catalog (admin) and per-host network attachments (design §20)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ROLES
from ..db import get_db
from ..models import HostNetwork, IpAllocation, KvmHost, Network
from ..netpolicy import PolicyError, may_use, ranges, usable_networks, validate_network
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(tags=["networks"])


class NetworkIn(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    description: str = ""
    cidr: str
    gateway: str = ""
    dns_servers: list[str] = []
    vlan: int | None = Field(default=None, ge=1, le=4094)
    addressing: list[Literal["static", "dhcp"]] = Field(default=["static"], min_length=1)
    static_pools: list[str] = []
    dhcp_ranges: list[str] = []
    reserved: list[str] = []
    allowed_roles: list[str] = []
    allowed_groups: list[str] = []
    enabled: bool = True


FIELDS = ("id", "name", "description", "cidr", "gateway", "dns_servers", "vlan", "addressing", "static_pools",
          "dhcp_ranges", "reserved", "allowed_roles", "allowed_groups", "enabled")


def _out(db: Session, n: Network) -> dict:
    used = db.scalar(select(func.count()).select_from(IpAllocation).where(IpAllocation.network_id == n.id))
    return {**{f: getattr(n, f) for f in FIELDS}, "allocated": used}


def _check(db: Session, body: NetworkIn) -> None:
    bad = [r for r in body.allowed_roles if r not in ROLES]
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown roles: {bad}")
    try:
        validate_network(body.model_dump(), db.scalars(select(Network)))
    except PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/networks")
def list_networks(kvm_host_id: int | None = None, db: Session = Depends(get_db),
                  p: Principal = Depends(current_principal)):
    """Admins see the whole catalog; others see what they may deploy on.
    With kvm_host_id: what that host carries (and the user may use)."""
    if kvm_host_id is not None:
        return [{**_out(db, n), "bridge": hn.bridge, "is_default": hn.is_default}
                for hn, n in usable_networks(db, p, kvm_host_id)]
    nets = db.scalars(select(Network).order_by(Network.name))
    return [_out(db, n) for n in nets if p.is_admin or (n.enabled and may_use(p, n))]


@router.post("/api/networks", status_code=201)
def create_network(body: NetworkIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    if db.scalar(select(Network).where(Network.name == body.name)):
        raise HTTPException(status_code=409, detail="A network with that name exists")
    _check(db, body)
    n = Network(**body.model_dump())
    db.add(n)
    db.commit()
    audit(db, p.username, "network.create", object_type="network", object_id=n.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(db, n)


@router.put("/api/networks/{network_id}")
def update_network(network_id: int, body: NetworkIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    n = db.get(Network, network_id)
    if n is None:
        raise HTTPException(status_code=404, detail="No such network")
    if body.name != n.name and db.scalar(select(func.count()).select_from(IpAllocation)
                                         .where(IpAllocation.network_id == n.id)):
        raise HTTPException(status_code=409, detail="Can't rename a network with allocated addresses")
    _check(db, body)
    for k, v in body.model_dump().items():
        setattr(n, k, v)
    db.commit()
    audit(db, p.username, "network.update", object_type="network", object_id=n.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(db, n)


@router.delete("/api/networks/{network_id}")
def delete_network(network_id: int, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    n = db.get(Network, network_id)
    if n is None:
        raise HTTPException(status_code=404, detail="No such network")
    if db.scalar(select(func.count()).select_from(IpAllocation).where(IpAllocation.network_id == n.id)):
        raise HTTPException(status_code=409, detail="Addresses are still allocated on this network; disable it instead")
    for hn in db.scalars(select(HostNetwork).where(HostNetwork.network_id == n.id)):
        db.delete(hn)
    db.delete(n)
    db.commit()
    audit(db, p.username, "network.delete", object_type="network", object_id=network_id, source_ip=client_ip(request))
    return {"ok": True}


class AttachmentIn(BaseModel):
    network: str
    bridge: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")
    static_pool: list[str] = []
    is_default: bool = False


@router.get("/api/hosts/{host_id}/networks")
def host_networks(host_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    rows = db.execute(select(HostNetwork, Network).join(Network, HostNetwork.network_id == Network.id)
                      .where(HostNetwork.kvm_host_id == host_id).order_by(Network.name)).all()
    return [{"network": n.name, "bridge": hn.bridge, "static_pool": hn.static_pool, "is_default": hn.is_default}
            for hn, n in rows]


@router.put("/api/hosts/{host_id}/networks")
def set_host_networks(host_id: int, body: list[AttachmentIn], request: Request, db: Session = Depends(get_db),
                      p: Principal = Depends(require_roles("admin"))):
    """Replace the host's attachments. At most one default."""
    if db.get(KvmHost, host_id) is None:
        raise HTTPException(status_code=404, detail="No such host")
    if sum(a.is_default for a in body) > 1:
        raise HTTPException(status_code=422, detail="Only one default network per host")
    if len({a.network for a in body}) != len(body):
        raise HTTPException(status_code=422, detail="A network can be attached once per host")
    nets = {n.name: n for n in db.scalars(select(Network))}
    for a in body:
        net = nets.get(a.network)
        if net is None:
            raise HTTPException(status_code=422, detail=f"Unknown network {a.network}")
        try:
            slice_ = ranges(a.static_pool)
            pools = ranges(net.static_pools)
        except (ValueError, PolicyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for s in slice_:
            if not any(lo <= s[0] and s[1] <= hi for lo, hi in pools):
                raise HTTPException(status_code=422,
                                    detail=f"{a.network}: host slice {s[0]}-{s[1]} isn't inside a static pool")
    for hn in db.scalars(select(HostNetwork).where(HostNetwork.kvm_host_id == host_id)):
        db.delete(hn)
    db.flush()
    for a in body:
        db.add(HostNetwork(kvm_host_id=host_id, network_id=nets[a.network].id, bridge=a.bridge,
                           static_pool=a.static_pool, is_default=a.is_default))
    db.commit()
    audit(db, p.username, "host.networks", object_type="kvm_host", object_id=host_id,
          detail={"attachments": [a.model_dump() for a in body]}, source_ip=client_ip(request))
    return host_networks(host_id, db, p)
