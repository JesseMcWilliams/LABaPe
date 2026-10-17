#!/usr/bin/env python3
"""What a redeploy would do to each VM, from `tofu show -json` of a plan (design §56.1).

Usage: plan_summary.py <tfplan.json>   (writes JSON to stdout)

A VM is the module's `null_resource` (vm_iso_direct or vm_from_template); its triggers decide
whether it is reinstalled. Only trigger *names* are reported, never values: they include hashes
of rendered answer files.
"""
import json
import sys

VM_RESOURCES = {"vm_iso_direct", "vm_from_template"}


def _action(actions: list[str]) -> str:
    if "delete" in actions and "create" in actions:
        return "replace"
    if actions == ["create"]:
        return "create"
    if actions == ["delete"]:
        return "delete"
    if actions == ["update"]:
        return "update"
    return "none"                    # no-op, read


def _changed(change: dict) -> list[str]:
    """The trigger names whose value changes (or isn't known until apply)."""
    before = (change.get("before") or {}).get("triggers") or {}
    after = (change.get("after") or {}).get("triggers") or {}
    unknown = (change.get("after_unknown") or {}).get("triggers") or {}
    if unknown is True:
        return sorted(set(before) | set(after))
    keys = set(before) | set(after) | (set(unknown) if isinstance(unknown, dict) else set())
    return sorted(k for k in keys
                  if (isinstance(unknown, dict) and unknown.get(k) is True) or before.get(k) != after.get(k))


def summarize(plan: dict) -> dict:
    vms, other = [], 0
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        action = _action(change.get("actions") or [])
        if rc.get("type") == "null_resource" and rc.get("name") in VM_RESOURCES:
            trig = (change.get("after") or {}).get("triggers") or (change.get("before") or {}).get("triggers") or {}
            mod = rc.get("module_address", "")              # module.vm["<key>"]
            name = trig.get("name") or (mod.split('"')[1] if mod.count('"') >= 2 else rc.get("address", ""))
            vms.append({"name": str(name), "action": action,
                        "changed": _changed(change) if action == "replace" else []})
        elif action != "none":
            other += 1
    vms.sort(key=lambda v: v["name"])
    return {"vms": vms, "other_changes": other}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        json.dump(summarize(json.load(fh)), sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
