#!/usr/bin/env python3
"""Find the addresses DHCP-addressed VMs leased, by their MAC, and write
them into hosts.json before inventory generation
(Claude_Docs/Planning_Web-Interface-Design.md §20.5).

For each DHCP VM, until found or the timeout:
1. `virsh domifaddr --source agent`, if the guest runs qemu-guest-agent;
2. the KVM host's ARP table: /proc/net/arp when this runs on that host
   (the CLI), else `virsh domifaddr --source arp` (libvirt reads the
   host's table, so this works from the web UI's worker container too;
   it has been seen to come back empty now and then, hence the retries);
3. when neither knows yet, ping-sweep the network's DHCP scope (or the
   whole network) so the host learns the neighbors, then look again.
An ARP answer is confirmed before it's used: probe the address (ICMP,
whose reply doesn't matter -- Windows' firewall drops it -- but which
makes the kernel re-resolve it), then the entry must still map that MAC
to that address, so a stale entry from an earlier VM isn't taken for the
new one.

Usage: discover_dhcp_ips.py <hosts.json> <environment.yml> [--timeout SECONDS]
libvirt URI from LIBVIRT_URI or TF_VAR_libvirt_uri (default qemu:///system).
"""
import ipaddress
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labape_networks  # noqa: E402

URI = os.environ.get("LIBVIRT_URI") or os.environ.get("TF_VAR_libvirt_uri") or "qemu:///system"
POLL_SECONDS = 20
SWEEP_EVERY = 3          # polls between sweeps
MAX_SWEEP = 4096         # addresses; larger networks need dhcp_ranges


def domifaddr(vm: str, source: str, mac: str) -> str | None:
    try:
        out = subprocess.run(["virsh", "--connect", URI, "domifaddr", vm, "--source", source],
                             capture_output=True, text=True, timeout=30).stdout
    except subprocess.TimeoutExpired:
        return None
    for line in out.splitlines():
        parts = line.split()
        # " vnet3   52:54:00:aa:bb:cc   ipv4   172.21.48.37/22"
        if len(parts) >= 4 and parts[2] == "ipv4" and parts[1].lower() == mac.lower():
            ip = parts[3].split("/")[0]
            if not ip.startswith(("127.", "169.254.")):
                return ip
    return None


def local_arp(mac: str) -> str | None:
    """This network namespace's ARP table; only the KVM host's own when the
    CLI runs there (a container has its own, empty of the VMs)."""
    try:
        with open("/proc/net/arp", encoding="ascii") as fh:
            next(fh, None)
            for line in fh:
                f = line.split()
                # IP address, HW type, Flags, HW address, Mask, Device; flag 0x2 = complete
                if len(f) >= 6 and f[3].lower() == mac.lower() and int(f[2], 16) & 0x2:
                    return f[0]
    except OSError:
        pass
    return None


def arp_lookup(vm: str, mac: str) -> str | None:
    return local_arp(mac) or domifaddr(vm, "arp", mac)


def ping(ip: str) -> bool:
    return subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def sweep_targets(net: labape_networks.Network) -> list[str]:
    ranges = net.dhcp_ranges or [(net.cidr.network_address + 1, net.cidr.broadcast_address - 1)]
    out = []
    for a, b in ranges:
        n = int(b) - int(a) + 1
        out += [str(ipaddress.IPv4Address(int(a) + i)) for i in range(min(n, MAX_SWEEP - len(out)))]
    return out


def sweep(targets: list[str]) -> None:
    with ThreadPoolExecutor(max_workers=128) as pool:
        list(pool.map(ping, targets))


def main() -> int:
    args = sys.argv[1:]
    timeout = 2700
    if "--timeout" in args:
        i = args.index("--timeout")
        timeout = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 2:
        print(f"usage: {sys.argv[0]} <hosts.json> <environment.yml> [--timeout SECONDS]", file=sys.stderr)
        return 2
    hosts_path, env_path = args
    with open(hosts_path, encoding="utf-8") as f:
        hosts = json.load(f)
    with open(env_path, encoding="utf-8") as f:
        networks, _ = labape_networks.load(yaml.safe_load(f) or {})

    pending = {name: h for name, h in hosts.items()
               if (h.get("addressing_mode") == "dhcp") and not h.get("ip_address")}
    if not pending:
        return 0
    print(f"labape: finding DHCP addresses for {', '.join(sorted(pending))} (up to {timeout // 60} min)...",
          file=sys.stderr)

    deadline = time.monotonic() + timeout
    polls = 0
    while pending and time.monotonic() < deadline:
        polls += 1
        for name in list(pending):
            mac = pending[name].get("mac_address") or ""
            ip = domifaddr(name, "agent", mac)
            how = "guest agent"
            if not ip:
                ip = arp_lookup(name, mac)
                how = "ARP"
                if ip:
                    ping(ip)  # the reply doesn't matter; the re-resolution does
                    if arp_lookup(name, mac) != ip:
                        ip = None
            if ip:
                hosts[name]["ip_address"] = ip
                print(f"labape: {name} ({mac}) has {ip} (via {how}).", file=sys.stderr)
                del pending[name]
        if not pending:
            break
        if polls % SWEEP_EVERY == 1:
            # The sweep takes a while itself; look again right after it.
            for net_name in {pending[n].get("network") for n in pending}:
                net = networks.get(net_name)
                if net is not None:
                    sweep(sweep_targets(net))
        else:
            time.sleep(POLL_SECONDS)

    with open(hosts_path, "w", encoding="utf-8") as f:
        json.dump(hosts, f, indent=2)
    if pending:
        print(f"labape: no DHCP address found for {', '.join(sorted(pending))} within {timeout // 60} min. "
              "Check the VM's console and the DHCP server.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
