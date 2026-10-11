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
    pass


def parse_range(text: str) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    text = str(text).strip()
    a, _, b = text.partition("-")
    first = ipaddress.IPv4Address(a.strip())
    last = ipaddress.IPv4Address(b.strip()) if b else first
    if last < first:
        raise PolicyError(f"range {text!r} ends before it starts")
    return first, last


def ranges(texts: Iterable[str]) -> list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]]:
    return [parse_range(t) for t in texts or []]


def _overlap(a, b) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def validate_network(fields: dict, others: Iterable[Network] = ()) -> None:
    """Raise PolicyError if a catalog entry is inconsistent."""
    try:
        cidr = ipaddress.IPv4Network(fields["cidr"], strict=False)
        gateway = ipaddress.IPv4Address(fields["gateway"]) if fields.get("gateway") else cidr.network_address + 1
        for d in fields.get("dns_servers") or []:
            ipaddress.IPv4Address(d)
        pools, dhcp, reserved = (ranges(fields.get(k)) for k in ("static_pools", "dhcp_ranges", "reserved"))
    except (ValueError, KeyError) as exc:
        raise PolicyError(str(exc)) from exc
    if gateway not in cidr:
        raise PolicyError(f"gateway {gateway} is outside {cidr}")
    bad = [m for m in fields.get("addressing") or [] if m not in MODES]
    if bad or not fields.get("addressing"):
        raise PolicyError("addressing must list static and/or dhcp")
    if "static" in fields["addressing"] and not pools:
        raise PolicyError("a network that allows static addressing needs at least one static pool")
    for label, rs in (("static pool", pools), ("DHCP range", dhcp), ("reserved range", reserved)):
        for a, b in rs:
            if a not in cidr or b not in cidr:
                raise PolicyError(f"{label} {a}-{b} is not inside {cidr}")
    for p in pools:
        for r in dhcp + reserved:
            if _overlap(p, r):
                raise PolicyError(f"static pool {p[0]}-{p[1]} overlaps {r[0]}-{r[1]}")
        if p[0] <= gateway <= p[1]:
            raise PolicyError(f"static pool {p[0]}-{p[1]} contains the gateway {gateway}")
    for o in others:
        if o.name != fields.get("name") and cidr.overlaps(ipaddress.IPv4Network(o.cidr, strict=False)):
            raise PolicyError(f"{cidr} overlaps network {o.name} ({o.cidr})")


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
            raise PolicyError(f"host group {group}: network {network} isn't attached to this KVM host")
    else:
        match = [(hn, n) for hn, n in attached if hn.is_default] or attached[:1]
    hn, net = match[0]
    if not net.enabled:
        raise PolicyError(f"host group {group}: network {net.name} is disabled")
    if not may_use(p, net):
        raise PolicyError(f"host group {group}: you aren't allowed to deploy on network {net.name}")
    if addressing not in (net.addressing or []):
        raise PolicyError(f"host group {group}: {net.name} doesn't allow {addressing} addressing")
    if addressing != "static" and "domain_controller" in roles:
        raise PolicyError(f"host group {group}: domain controllers need static addressing")
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
