#!/usr/bin/env python3
"""Check a plan's addressing against environment.yml's network catalog
(Claude_Docs/Planning_Web-Interface-Design.md §20), before anything is created.

For every VM in the plan's `hosts` output:
- its network must be in the catalog and allow its addressing mode;
- a static address must be inside the network, outside its reserved
  ranges and DHCP scope, and inside its static pool (when one is set);
- domain controllers must be static (members point their DNS at them);
- no two VMs may share an address.

VMs the plan creates are refused on a violation. VMs that already exist
only get a warning, so tightening the catalog never blocks redeploying
an environment that was valid when it was built.

Usage: check_ip_policy.py <plan.json> <environment.yml>
"""
import json
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labape_networks  # noqa: E402

_VM_MODULE_RE = re.compile(r'^module\.vm\["([^"]+)"\]\.')


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <plan.json> <environment.yml>", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        plan = json.load(f)
    with open(sys.argv[2], encoding="utf-8") as f:
        networks, _ = labape_networks.load(yaml.safe_load(f) or {})

    hosts = plan.get("planned_values", {}).get("outputs", {}).get("hosts", {}).get("value", {}) or {}
    creating = set()
    for change in plan.get("resource_changes", []):
        if change.get("type") != "null_resource" or change.get("name") not in ("vm_iso_direct", "vm_from_template"):
            continue
        if "create" in change.get("change", {}).get("actions", []):
            m = _VM_MODULE_RE.match(change.get("address", ""))
            if m:
                creating.add(m.group(1))

    errors, warnings, seen = [], [], {}
    for name in sorted(hosts):
        host = hosts[name]
        problems = []
        net_name = host.get("network") or ""
        mode = host.get("addressing_mode") or "static"
        net = networks.get(net_name)
        if net is None:
            problems.append(f"network {net_name!r} isn't in environment.yml's networks:")
        else:
            if mode not in net.addressing:
                problems.append(f"{net_name} doesn't allow {mode} addressing (allowed: {', '.join(net.addressing)})")
            if mode == "static":
                ip = host.get("ip_address")
                if not ip:
                    problems.append("static, but no address was assigned")
                else:
                    why = net.static_problem(ip)
                    if why:
                        problems.append(why)
                    if ip in seen:
                        problems.append(f"{ip} is also assigned to {seen[ip]}")
                    seen[ip] = name
        if mode != "static" and "domain_controller" in (host.get("roles") or []):
            problems.append("domain controllers need a static address")
        for p in problems:
            (errors if name in creating else warnings).append(f"{name}: {p}")

    for w in warnings:
        print(f"labape: warning (existing VM, left as is): {w}", file=sys.stderr)
    for e in errors:
        print(f"labape: address policy: {e}", file=sys.stderr)
    if errors:
        print(f"labape: {len(errors)} address-policy violation(s); nothing was created.", file=sys.stderr)
        return 1
    print(f"labape: address policy check passed ({len(hosts)} host(s)).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
