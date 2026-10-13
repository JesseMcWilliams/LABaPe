#!/usr/bin/env python3
"""Start the environment's VMs that are shut off, after `tofu apply` and before
DHCP discovery and Ansible.

A redeploy only changes what differs, so a VM that was off (a host reboot
leaves VMs off; libvirt autostart isn't set) stayed off, and the deploy then
waited 45 minutes for a DHCP lease that could never come. Domain controllers
start first, as in the web interface's default start order.

Usage: start_stopped_vms.py <hosts.json>
libvirt URI from LIBVIRT_URI or TF_VAR_libvirt_uri (default qemu:///system).
"""
import json
import os
import subprocess
import sys

URI = os.environ.get("LIBVIRT_URI") or os.environ.get("TF_VAR_libvirt_uri") or "qemu:///system"


def virsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", "--connect", URI, *args], capture_output=True, text=True, timeout=120, check=False)


def order(host: dict) -> int:
    return 1 if "domain_controller" in (host.get("roles") or []) else 2


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <hosts.json>", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        hosts = json.load(f)
    for name in sorted(hosts, key=lambda n: (order(hosts[n]), n)):
        state = virsh("domstate", name)
        if state.returncode != 0:
            print(f"labape: {name}: can't read its state: {state.stderr.strip()}", file=sys.stderr)
            return 1
        if state.stdout.strip() in ("shut off", "crashed"):
            r = virsh("start", name)
            if r.returncode != 0:
                print(f"labape: {name} was {state.stdout.strip()} and didn't start: {r.stderr.strip()}", file=sys.stderr)
                return 1
            print(f"labape: {name} was {state.stdout.strip()}; started it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
