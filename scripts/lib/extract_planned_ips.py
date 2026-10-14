#!/usr/bin/env python3
"""Extract planned static IPs from a `tofu show -json` plan, for the
pre-flight check (Claude_Docs/Reference_Networking.md §3) to test before anything is
actually created.

The vm module's ip_address output for a static-mode host is a pure
function of local input (cidrhost() on the environment's network_cidr)
with no dependency on real infrastructure, so it's fully known at plan
time — this doesn't need to wait for apply.

Usage: extract_planned_ips.py <plan.json>
Prints one IP per line. DHCP-mode hosts have a null ip_address
(Claude_Docs/Design_System-Overview.md §17.4) and are silently skipped, not an error here.
"""
import json
import os
import re
import subprocess
import sys

_VM_MODULE_RE = re.compile(r'^module\.vm\["([^"]+)"\]\.')


def _ours_running(name: str, workspace: str) -> bool:
    """A VM already running on this host with this workspace's tag: the create adopts it
    (existing-domain.sh), e.g. a VM moved here from another host (design §46). Its address
    answers because it is that VM."""
    uri = os.environ.get("LIBVIRT_URI") or os.environ.get("TF_VAR_libvirt_uri") or "qemu:///system"
    try:
        state = subprocess.run(["virsh", "--connect", uri, "domstate", name], capture_output=True, text=True, timeout=30)
        desc = subprocess.run(["virsh", "--connect", uri, "desc", name], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return state.returncode == 0 and state.stdout.strip() == "running" and desc.stdout.strip() == f"labape-workspace={workspace}"


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

    # Only check hosts landing on a brand-new address: a pure "create"
    # action. An already-existing, unchanged VM (a no-op) legitimately
    # responds on its own IP and isn't a collision — and neither is a
    # "replace" (actions == ["delete", "create"], e.g. the kickstart
    # content changed): the address currently responds because the
    # *old* instance of that same tofu-managed host hasn't been
    # destroyed yet, not because something foreign took it. Checking
    # only exact ["create"] excludes both of those alongside the
    # already-existing no-op case.
    creating = set()
    for change in plan.get("resource_changes", []):
        # vm_iso_direct (install from ISO) or vm_from_template (clone).
        if change.get("type") != "null_resource" or change.get("name") not in ("vm_iso_direct", "vm_from_template"):
            continue
        if change.get("change", {}).get("actions", []) != ["create"]:
            continue
        m = _VM_MODULE_RE.match(change.get("address", ""))
        if m:
            creating.add(m.group(1))

    workspace = os.environ.get("LABAPE_WORKSPACE", "")
    for name, host in hosts.items():
        if name not in creating or (workspace and _ours_running(name, workspace)):
            continue
        ip = host.get("ip_address")
        if ip:
            print(ip)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
