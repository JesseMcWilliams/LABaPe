"""Network catalog rules and static address allocation
(Claude_Docs/Planning_Web-Interface-Design.md §20).

The engine enforces the same rules again after the plan
(scripts/lib/check_ip_policy.py, scripts/lib/labape_networks.py), so a
deploy can't slip past them even if the app's data were wrong.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import HostNetwork, IpAllocation, Network
from .security import Principal

MODES = ("static", "dhcp")


class PolicyError(ValueError):
    """A catalog or deploy rule was broken. `field` names the input at fault
    (e.g. "static_pools", "host_groups.1.addressing") so the UI can mark it."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def parse_range(text: str, field: str | None = None) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    text = str(text).strip()
    a, _, b = text.partition("-")
    try:
        first = ipaddress.IPv4Address(a.strip())
        last = ipaddress.IPv4Address(b.strip()) if b else first
    except ValueError:
        raise PolicyError(f"{text!r} isn't an address or a range like 172.21.50.1-172.21.50.99.", field) from None
    if last < first:
        raise PolicyError(f"{text!r} ends before it starts; put the lower address first.", field)
    return first, last


def ranges(texts: Iterable[str], field: str | None = None) -> list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]]:
    return [parse_range(t, field) for t in texts or []]


def _overlap(a, b) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _fmt(r) -> str:
    return str(r[0]) if r[0] == r[1] else f"{r[0]}-{r[1]}"


def validate_network(fields: dict, others: Iterable[Network] = ()) -> None:
    """Raise PolicyError (with the field at fault) if a catalog entry is inconsistent."""
    try:
        cidr = ipaddress.IPv4Network(str(fields.get("cidr", "")), strict=False)
    except ValueError:
        raise PolicyError(f"{fields.get('cidr')!r} isn't an IPv4 network; use a form like 172.21.48.0/22.",
                          "cidr") from None
    try:
        gateway = ipaddress.IPv4Address(fields["gateway"]) if fields.get("gateway") else cidr.network_address + 1
    except ValueError:
        raise PolicyError(f"{fields.get('gateway')!r} isn't an IPv4 address.", "gateway") from None
    if gateway not in cidr:
        raise PolicyError(f"The gateway {gateway} must be inside {cidr}.", "gateway")
    for d in fields.get("dns_servers") or []:
        try:
            ipaddress.IPv4Address(d)
        except ValueError:
            raise PolicyError(f"{d!r} isn't an IPv4 address.", "dns_servers") from None
    pools = ranges(fields.get("static_pools"), "static_pools")
    dhcp = ranges(fields.get("dhcp_ranges"), "dhcp_ranges")
    reserved = ranges(fields.get("reserved"), "reserved")
    modes = fields.get("addressing") or []
    if not modes or any(m not in MODES for m in modes):
        raise PolicyError("Choose static, DHCP or both.", "addressing")
    if "static" in modes and not pools:
        raise PolicyError("Static addressing needs at least one static pool: the range LABaPe may hand out, "
                          "outside the DHCP scope.", "static_pools")
    for field, rs in (("static_pools", pools), ("dhcp_ranges", dhcp), ("reserved", reserved)):
        for r in rs:
            if r[0] not in cidr or r[1] not in cidr:
                raise PolicyError(f"{_fmt(r)} is outside the network {cidr}.", field)
    for pool in pools:
        for r in dhcp:
            if _overlap(pool, r):
                raise PolicyError(f"The static pool {_fmt(pool)} overlaps the DHCP scope {_fmt(r)}; static "
                                  "addresses must be ones the DHCP server never hands out.", "static_pools")
        for r in reserved:
            if _overlap(pool, r):
                raise PolicyError(f"The static pool {_fmt(pool)} overlaps the reserved range {_fmt(r)}.",
                                  "static_pools")
        if pool[0] <= gateway <= pool[1]:
            raise PolicyError(f"The static pool {_fmt(pool)} contains the gateway {gateway}.", "static_pools")
    for o in others:
        if o.name != fields.get("name") and cidr.overlaps(ipaddress.IPv4Network(o.cidr, strict=False)):
            raise PolicyError(f"{cidr} overlaps the network {o.name} ({o.cidr}); networks can't share addresses.",
                              "cidr")


def may_use(p: Principal, net: Network) -> bool:
    if p.is_admin:
        return True
    roles, groups = net.allowed_roles or [], net.allowed_groups or []
    if not roles and not groups:
        return True
    return bool(set(roles) & p.roles) or bool(set(groups) & set(p.groups))


def usable_networks(db: Session, p: Principal, kvm_host_id: int) -> list[tuple[HostNetwork, Network]]:
    rows = db.execute(select(HostNetwork, Network).join(Network, HostNetwork.network_id == Network.id)
                      .where(HostNetwork.kvm_host_id == kvm_host_id)).all()
    return [(hn, n) for hn, n in rows if n.enabled and may_use(p, n)]


def resolve(db: Session, p: Principal, kvm_host_id: int, network: str | None, addressing: str,
            roles: list[str], group: str) -> tuple[HostNetwork, Network]:
    """Pick and check the network for one host group."""
    attached = db.execute(select(HostNetwork, Network).join(Network, HostNetwork.network_id == Network.id)
                          .where(HostNetwork.kvm_host_id == kvm_host_id)).all()
    if not attached:
        raise PolicyError("the KVM host has no networks attached (an admin attaches them under Hosts)")
    if network:
        match = [(hn, n) for hn, n in attached if n.name == network]
        if not match:
            raise PolicyError(f"Network {network} isn't attached to this KVM host. Choose one of its networks.", "network")
    else:
        match = [(hn, n) for hn, n in attached if hn.is_default] or attached[:1]
    hn, net = match[0]
    if not net.enabled:
        raise PolicyError(f"Network {net.name} is disabled; choose another network.", "network")
    if not may_use(p, net):
        raise PolicyError(f"You aren't allowed to deploy on network {net.name}; it's limited to certain roles "
                          "or groups. Choose another network, or ask an admin.", "network")
    if addressing not in (net.addressing or []):
        raise PolicyError(f"Network {net.name} doesn't allow {addressing} addressing; it allows "
                          f"{' and '.join(net.addressing or [])}.", "addressing")
    if addressing != "static" and "domain_controller" in roles:
        raise PolicyError("Domain controllers need static addressing: domain members use them for DNS.", "addressing")
    return hn, net


def _candidates(net: Network, hn: HostNetwork) -> Iterator[str]:
    cidr = ipaddress.IPv4Network(net.cidr, strict=False)
    gateway = ipaddress.IPv4Address(net.gateway) if net.gateway else cidr.network_address + 1
    pools = ranges(hn.static_pool) if hn.static_pool else ranges(net.static_pools)
    blocked = ranges(net.dhcp_ranges) + ranges(net.reserved)
    for a, b in pools:
        for i in range(int(a), int(b) + 1):
            ip = ipaddress.IPv4Address(i)
            if ip in (cidr.network_address, cidr.broadcast_address, gateway):
                continue
            if any(x <= ip <= y for x, y in blocked):
                continue
            yield str(ip)


def allocate(db: Session, env_id: int, hn: HostNetwork, net: Network, vm_names: list[str]) -> list[str]:
    """Static addresses for `vm_names`, reusing this environment's existing
    allocations. Locks the network row so concurrent deploys can't collide."""
    if db.bind.dialect.name == "postgresql":
        db.execute(select(Network.id).where(Network.id == net.id).with_for_update())
    taken = {a.address: a for a in db.scalars(select(IpAllocation).where(IpAllocation.network_id == net.id))}
    mine = {a.vm_name: a.address for a in taken.values() if a.environment_id == env_id}
    out, free = [], (ip for ip in _candidates(net, hn) if ip not in taken)
    for vm in vm_names:
        if vm in mine:
            out.append(mine[vm])
            continue
        ip = next(free, None)
        if ip is None:
            raise PolicyError(f"network {net.name} has no free static addresses left for {vm}")
        db.add(IpAllocation(network_id=net.id, address=ip, environment_id=env_id, vm_name=vm))
        out.append(ip)
    return out


def release(db: Session, env_id: int) -> int:
    rows = list(db.scalars(select(IpAllocation).where(IpAllocation.environment_id == env_id)))
    for r in rows:
        db.delete(r)
    return len(rows)
