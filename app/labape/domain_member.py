"""Domain member or standalone, per host group (D184, question 184).

A host group in an environment that has a domain controller joins its domain unless the group says
`domain_member: false`. The standalone groups' VMs get the host variable `labape_domain_member: false`
before a deploy, and site.yml leaves them out of the join plays. A domain controller is always part of
its domain, whatever the spec says.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import Environment
from .naming import instance_names


def standalone_vms(env: Environment) -> list[str]:
    """The VMs of the groups marked standalone (domain controllers never are)."""
    return sorted(n for g in (env.spec or {}).get("host_groups", [])
                  if g.get("domain_member") is False and "domain_controller" not in (g.get("roles") or [])
                  for n in instance_names(g))


def write_host_vars(env: Environment, work: Path) -> int:
    """Merge `labape_domain_member: false` into each standalone VM's host_vars."""
    vms = standalone_vms(env)
    hv = work / "ansible" / "inventory" / "host_vars"
    if vms:
        hv.mkdir(parents=True, exist_ok=True)
    for vm in vms:
        f = hv / f"{vm}.yml"
        data = (yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else None) or {}
        data["labape_domain_member"] = False
        f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.chmod(f, 0o600)
    return len(vms)
