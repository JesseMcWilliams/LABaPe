#!/usr/bin/env python3
"""Convert tofu/environment.yml into tofu/backends/<backend>/environment.auto.tfvars.json.

DESIGN.md §6.3: environment.yml is the one human-edited file (shared
conceptually across OpenTofu and Ansible), but OpenTofu doesn't read
YAML directly — this is the conversion step that bridges the two,
including normalizing network_address/subnet_mask into the CIDR string
tofu/backends/libvirt/main.tf expects.

Usage: render_environment_tfvars.py <environment.yml> <backend-dir>
"""
import ipaddress
import json
import sys

import yaml


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <environment.yml> <backend-dir>", file=sys.stderr)
        return 2

    env_path, backend_dir = sys.argv[1], sys.argv[2]

    with open(env_path, encoding="utf-8") as f:
        env = yaml.safe_load(f)

    network = env.get("network", {})
    address = network.get("network_address")
    mask = network.get("subnet_mask")
    if not address or not mask:
        print(f"labape: {env_path}'s network.network_address/subnet_mask are required", file=sys.stderr)
        return 1

    network_obj = ipaddress.ip_network(f"{address}/{mask}", strict=False)

    tfvars = {
        "network_mode": network.get("mode", "bridged"),
        "network_cidr": str(network_obj),
        "gateway": network.get("gateway", ""),
        "management_source": network.get("management_source", ""),
        "image_source_default": env.get("image_source_default", "iso_direct"),
        "os_iso_paths": env.get("os_iso_paths", {}),
    }

    out_path = f"{backend_dir}/environment.auto.tfvars.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tfvars, f, indent=2)
        f.write("\n")

    print(f"labape: wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
