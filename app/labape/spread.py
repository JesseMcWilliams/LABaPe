"""One environment over several KVM hosts (design §45, P1b; §15's per-group host).

A host group may name its own `kvm_host_id`; without one it goes on the environment's own
host (its "home"). Each host's part is its own OpenTofu workspace, run on that host: the home
keeps the environment's name, so environments made before P1b keep their state; the others are
`<name>--h<host id>`. Ansible runs once, on the home, over every part's VMs (the hosts share the
lab networks, so it reaches them all).
"""
from __future__ import annotations

from .naming import instance_names


def group_host_id(env, g: dict) -> int:
    return int(g.get("kvm_host_id") or env.kvm_host_id)


def host_ids(env) -> list[int]:
    """The home first, then the other hosts with groups, in group order. The home always has a part,
    even with no groups left there: its OpenTofu state then forgets VMs that moved away (§46), and
    a destroy deletes its workspace."""
    out = [env.kvm_host_id]
    for g in (env.spec or {}).get("host_groups", []):
        h = group_host_id(env, g)
        if h not in out:
            out.append(h)
    return out


def is_spread(env) -> bool:
    return len(host_ids(env)) > 1


def groups_on(env, host_id: int) -> list[dict]:
    return [g for g in (env.spec or {}).get("host_groups", []) if group_host_id(env, g) == host_id]


def vm_host_ids(env) -> dict[str, int]:
    return {n: group_host_id(env, g) for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}


def vm_host_id(env, vm: str) -> int:
    return vm_host_ids(env).get(vm, env.kvm_host_id)


def workspace(env, host_id: int) -> str:
    return env.name if host_id == env.kvm_host_id else f"{env.name}--h{host_id}"
