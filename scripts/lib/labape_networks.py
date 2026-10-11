"""environment.yml's network catalog (Claude_Docs/Planning_Web-Interface-Design.md §20).

Shared by render_environment_tfvars.py, check_ip_policy.py and
discover_dhcp_ips.py. Two shapes are accepted:

    networks:                         # the catalog
      lab-vlan48:
        cidr: 172.21.48.0/22
        gateway: 172.21.48.1          # optional, default .1
        dns_servers: [172.21.48.1]    # optional, default [gateway]
        bridge: br1                   # optional, default the profile's bridge_device
        addressing: [static, dhcp]    # optional, default [static]
        static_pools: ["172.21.50.1-172.21.51.254"]   # optional; absent = anywhere not reserved
        dhcp_ranges: ["172.21.48.16-172.21.49.254"]   # the DHCP server's scope (never assigned statically)
        reserved: ["172.21.48.1-172.21.48.15"]        # optional, never assigned
    default_network: lab-vlan48       # optional, default the first name

and the older single network, read as a one-entry catalog named "default":

    network:
      network_address: 192.168.1.0
      subnet_mask: 255.255.255.0
      gateway: 192.168.1.1
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

LEGACY_NAME = "default"
MODES = ("static", "dhcp")


class CatalogError(ValueError):
    pass


def parse_range(text: str) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    """'a-b' or a single address -> (first, last)."""
    text = str(text).strip()
    if "-" in text:
        a, b = (s.strip() for s in text.split("-", 1))
    else:
        a = b = text
    first, last = ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)
    if last < first:
        raise CatalogError(f"range {text!r} ends before it starts")
    return first, last


@dataclass
class Network:
    name: str
    cidr: ipaddress.IPv4Network
    gateway: ipaddress.IPv4Address
    dns_servers: list[str]
    bridge: str = ""
    addressing: tuple[str, ...] = ("static",)
    static_pools: list[tuple] = field(default_factory=list)
    dhcp_ranges: list[tuple] = field(default_factory=list)
    reserved: list[tuple] = field(default_factory=list)

    @staticmethod
    def _in(ranges, ip) -> bool:
        return any(a <= ip <= b for a, b in ranges)

    def static_problem(self, address: str) -> str | None:
        """Why `address` may not be a static address here, or None."""
        ip = ipaddress.IPv4Address(address)
        if ip not in self.cidr:
            return f"{ip} is outside {self.name} ({self.cidr})"
        if ip in (self.cidr.network_address, self.cidr.broadcast_address) or ip == self.gateway:
            return f"{ip} is {self.name}'s network, broadcast or gateway address"
        if self._in(self.reserved, ip):
            return f"{ip} is in {self.name}'s reserved ranges"
        if self._in(self.dhcp_ranges, ip):
            return f"{ip} is inside {self.name}'s DHCP scope"
        if self.static_pools and not self._in(self.static_pools, ip):
            pools = ", ".join(f"{a}-{b}" for a, b in self.static_pools)
            return f"{ip} is outside {self.name}'s static pool ({pools})"
        return None

    def tfvars(self) -> dict:
        return {"cidr": str(self.cidr), "gateway": str(self.gateway), "dns_servers": self.dns_servers,
                "bridge": self.bridge}


def _network(name: str, raw: dict) -> Network:
    if "cidr" in raw:
        cidr = ipaddress.IPv4Network(str(raw["cidr"]), strict=False)
    elif raw.get("network_address") and raw.get("subnet_mask"):
        cidr = ipaddress.IPv4Network(f"{raw['network_address']}/{raw['subnet_mask']}", strict=False)
    else:
        raise CatalogError(f"network {name!r} needs cidr (or network_address and subnet_mask)")
    gateway = ipaddress.IPv4Address(raw["gateway"]) if raw.get("gateway") else cidr.network_address + 1
    if gateway not in cidr:
        raise CatalogError(f"network {name!r}: gateway {gateway} is outside {cidr}")
    modes = tuple(raw.get("addressing") or ("static",))
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise CatalogError(f"network {name!r}: unknown addressing {bad}; use static and/or dhcp")
    net = Network(
        name=name, cidr=cidr, gateway=gateway,
        dns_servers=[str(ipaddress.IPv4Address(d)) for d in (raw.get("dns_servers") or [])] or [str(gateway)],
        bridge=str(raw.get("bridge") or ""), addressing=modes,
        static_pools=[parse_range(r) for r in raw.get("static_pools") or []],
        dhcp_ranges=[parse_range(r) for r in raw.get("dhcp_ranges") or []],
        reserved=[parse_range(r) for r in raw.get("reserved") or []],
    )
    for label, ranges in (("static_pools", net.static_pools), ("dhcp_ranges", net.dhcp_ranges),
                          ("reserved", net.reserved)):
        for a, b in ranges:
            if a not in cidr or b not in cidr:
                raise CatalogError(f"network {name!r}: {label} {a}-{b} is not inside {cidr}")
    for a, b in net.static_pools:
        for c, d in net.dhcp_ranges + net.reserved:
            if a <= d and c <= b:
                raise CatalogError(f"network {name!r}: static pool {a}-{b} overlaps {c}-{d}")
    return net


def load(env: dict) -> tuple[dict[str, Network], str]:
    """Return (networks by name, default network name)."""
    if env.get("networks"):
        nets = {name: _network(name, raw or {}) for name, raw in env["networks"].items()}
        names = list(nets)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if nets[a].cidr.overlaps(nets[b].cidr):
                    raise CatalogError(f"networks {a!r} and {b!r} overlap ({nets[a].cidr}, {nets[b].cidr})")
        default = env.get("default_network") or sorted(nets)[0]
        if default not in nets:
            raise CatalogError(f"default_network {default!r} isn't in networks:")
        return nets, default
    if env.get("network"):
        return {LEGACY_NAME: _network(LEGACY_NAME, env["network"])}, LEGACY_NAME
    raise CatalogError("environment.yml needs a networks: catalog (or the older network: block)")


def management_source(env: dict) -> str:
    return str(env.get("management_source") or (env.get("network") or {}).get("management_source") or "")


def network_mode(env: dict) -> str:
    return str(env.get("network_mode") or (env.get("network") or {}).get("mode") or "bridged")
