"""`labape bootstrap <file.json>`: create or update KVM hosts, networks,
host network attachments and role bindings from a file, idempotently.

Used by deploy/install-labape.py (the Web.Bootstrap step) so a fresh
install comes up with its host and networks registered, without signing
in. Validation is the same as the API's (netpolicy.validate_network).

    {
      "networks": [{"name": "lab-vlan48", "cidr": "172.21.48.0/22", "gateway": "172.21.48.1",
                    "dns_servers": ["172.21.48.1"], "addressing": ["static", "dhcp"],
                    "static_pools": ["172.21.50.1-172.21.51.254"], "dhcp_ranges": [...],
                    "reserved": [...], "allowed_roles": [], "allowed_groups": []}],
      "hosts": [{"name": "kvm1", "libvirt_uri": "qemu:///system", "vm_storage_path": "...",
                 "template_storage_path": "...", "concurrency_limit": 2,
                 "networks": [{"network": "lab-vlan48", "bridge": "br1", "is_default": true}]}],
      "role_bindings": [{"principal_type": "group", "principal": "lab-admins", "role": "admin"}]
    }
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROLES
from .models import HostNetwork, KvmHost, Network, RoleBinding
from .netpolicy import validate_network

NETWORK_FIELDS = ("description", "cidr", "gateway", "dns_servers", "vlan", "addressing", "static_pools",
                  "dhcp_ranges", "reserved", "allowed_roles", "allowed_groups", "enabled")
HOST_FIELDS = ("libvirt_uri", "vm_storage_path", "template_storage_path", "bridge", "concurrency_limit", "enabled")


def apply(db: Session, data: dict) -> list[str]:
    """Apply the file; returns one line per change made."""
    changes: list[str] = []
    nets = {n.name: n for n in db.scalars(select(Network))}
    for raw in data.get("networks", []):
        fields = {"addressing": ["static"], **raw}
        validate_network(fields, [n for n in nets.values() if n.name != fields["name"]])
        bad = [r for r in fields.get("allowed_roles", []) if r not in ROLES]
        if bad:
            raise ValueError(f"network {fields['name']}: unknown roles {bad}")
        net = nets.get(fields["name"])
        if net is None:
            net = Network(name=fields["name"])
            db.add(net)
            nets[net.name] = net
            changes.append(f"network {net.name}: created")
        for k in NETWORK_FIELDS:
            if k in fields and getattr(net, k) != fields[k]:
                setattr(net, k, fields[k])
                changes.append(f"network {net.name}: {k} set")
    db.flush()

    hosts = {h.name: h for h in db.scalars(select(KvmHost))}
    for raw in data.get("hosts", []):
        host = hosts.get(raw["name"])
        if host is None:
            host = KvmHost(name=raw["name"])
            db.add(host)
            hosts[host.name] = host
            changes.append(f"host {host.name}: created")
        for k in HOST_FIELDS:
            if k in raw and getattr(host, k) != raw[k]:
                setattr(host, k, raw[k])
                changes.append(f"host {host.name}: {k} set")
        db.flush()
        if "networks" in raw:
            if sum(bool(a.get("is_default")) for a in raw["networks"]) > 1:
                raise ValueError(f"host {host.name}: only one default network")
            existing = {hn.network_id: hn for hn in db.scalars(select(HostNetwork)
                                                               .where(HostNetwork.kvm_host_id == host.id))}
            wanted = set()
            for a in raw["networks"]:
                net = nets.get(a["network"])
                if net is None:
                    raise ValueError(f"host {host.name}: unknown network {a['network']}")
                wanted.add(net.id)
                hn = existing.get(net.id)
                if hn is None:
                    hn = HostNetwork(kvm_host_id=host.id, network_id=net.id, bridge=a["bridge"])
                    db.add(hn)
                    changes.append(f"host {host.name}: network {net.name} attached on {a['bridge']}")
                for k, default in (("bridge", None), ("static_pool", []), ("is_default", False)):
                    v = a.get(k, default)
                    if v is not None and getattr(hn, k) != v:
                        setattr(hn, k, v)
            for net_id, hn in existing.items():
                if net_id not in wanted:
                    db.delete(hn)
                    changes.append(f"host {host.name}: network id {net_id} detached")

    current = {(b.principal_type, b.principal, b.role) for b in db.scalars(select(RoleBinding))}
    for b in data.get("role_bindings", []):
        key = (b["principal_type"], b["principal"], b["role"])
        if b["role"] not in ROLES or b["principal_type"] not in ("user", "group"):
            raise ValueError(f"role binding {key}: unknown role or principal type")
        if key not in current:
            db.add(RoleBinding(principal_type=key[0], principal=key[1], role=key[2]))
            current.add(key)
            changes.append(f"role binding {key[0]} {key[1]} -> {key[2]}: added")
    db.commit()
    return changes


def apply_file(db: Session, path: str | Path) -> list[str]:
    return apply(db, json.loads(Path(path).read_text(encoding="utf-8")))
