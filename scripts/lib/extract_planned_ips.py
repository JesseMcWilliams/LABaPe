#!/usr/bin/env python3
"""Extract planned static IPs from a `tofu show -json` plan, for the
pre-flight check (docs/networking.md §3) to test before anything is
actually created.

The vm module's ip_address output for a static-mode host is a pure
function of local input (cidrhost() on the environment's network_cidr)
with no dependency on real infrastructure, so it's fully known at plan
time — this doesn't need to wait for apply.

Usage: extract_planned_ips.py <plan.json>
Prints one IP per line. DHCP-mode hosts have a null ip_address
(DESIGN.md §17.4) and are silently skipped, not an error here.
"""
import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <plan.json>", file=sys.stderr)
        return 2

    with open(sys.argv[1], encoding="utf-8") as f:
        plan = json.load(f)

    hosts = (
        plan.get("planned_values", {})
        .get("outputs", {})
        .get("hosts", {})
        .get("value", {})
        or {}
    )

    for host in hosts.values():
        ip = host.get("ip_address")
        if ip:
            print(ip)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
