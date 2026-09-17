#!/usr/bin/env python3
"""Build the Ansible inventory + hosts.generated from `tofu output -json
hosts` (DESIGN.md §11, docs/networking.md §4).

Usage: generate-inventory.py <hosts.json> <environment.yml> <ssh-private-key-path> <out-dir>
Writes <out-dir>/generated (Ansible YAML inventory) and
<out-dir>/hosts.generated (the pasteable hosts-file snippet).
"""
import json
import os
import sys
from datetime import datetime, timezone

import yaml

# Every role this design defines (DESIGN.md §9), emitted even when
# empty so site.yml's `hosts: domain_controller` etc. always resolve
# to a real (possibly empty) group rather than an undefined one.
ALL_ROLE_GROUPS = [
    "domain_controller",
    "windows_server",
    "windows_workstation",
    "linux_server",
    "linux_workstation",
]


def main() -> int:
    if len(sys.argv) != 5:
        print(
            f"usage: {sys.argv[0]} <hosts.json> <environment.yml> <ssh-private-key-path> <out-dir>",
            file=sys.stderr,
        )
        return 2

    hosts_json_path, env_path, ssh_key_path, out_dir = sys.argv[1:5]

    with open(hosts_json_path, encoding="utf-8") as f:
        hosts = json.load(f)

    with open(env_path, encoding="utf-8") as f:
        env = yaml.safe_load(f) or {}
    domain_name = env.get("domain_name", "")

    inventory = {
        "all": {
            "hosts": {},
            "children": {g: {"hosts": {}} for g in ALL_ROLE_GROUPS},
        }
    }

    hosts_lines = [f"# LABaPe: generated {datetime.now(timezone.utc).isoformat()}"]

    for name, host in hosts.items():
        ip = host.get("ip_address")
        os_family = host.get("os_family")
        roles = host.get("roles", [])

        host_vars = {"ansible_host": ip}
        if os_family == "windows":
            # Not exercised until M3 (DESIGN.md §18) — kept here so the
            # inventory shape doesn't need revisiting when it lands.
            host_vars["ansible_connection"] = "winrm"
            host_vars["ansible_port"] = 5985
        else:
            host_vars["ansible_connection"] = "ssh"
            host_vars["ansible_user"] = "labape"  # docs/credentials.md §5
            host_vars["ansible_ssh_private_key_file"] = ssh_key_path

        inventory["all"]["hosts"][name] = host_vars

        for role in roles:
            inventory["all"]["children"].setdefault(role, {"hosts": {}})
            inventory["all"]["children"][role]["hosts"][name] = None

        if ip:
            fqdn = f"{name}.{domain_name}" if domain_name else name
            hosts_lines.append(f"{ip}\t{fqdn} {name}")

    os.makedirs(out_dir, exist_ok=True)

    with open(f"{out_dir}/generated", "w", encoding="utf-8") as f:
        yaml.safe_dump(inventory, f, default_flow_style=False, sort_keys=False)

    with open(f"{out_dir}/hosts.generated", "w", encoding="utf-8") as f:
        f.write("\n".join(hosts_lines) + "\n")

    print(f"labape: wrote {out_dir}/generated and {out_dir}/hosts.generated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
