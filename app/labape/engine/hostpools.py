"""Drive pools of a host (design §53.4, batch D1): mdadm arrays, ZFS pools and LVM volume groups.

The host's facts script (hostadmin.FACTS) gathers raw text and sysfs values only; the parsing is
here, where it can be tested without a host. `build(facts)` returns one dict per pool:

    {id, type ("mdadm"|"zfs"|"lvm"), name, level, state, size, used, free, mounts, uuid, members,
     sync: {action, percent, speed}, errors, actions}

`state` is healthy, scrubbing, rebuilding, degraded, failed or unknown. `actions` lists what the page
may offer for the pool (LVM is read-only for now). `run_action` does the safe actions over SSH.
"""
from __future__ import annotations

import re
import shlex

from . import hostadmin

SAFE_DEVICE = re.compile(r"^/dev/[A-Za-z0-9._:/+-]{1,120}$")
POOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

ZFS_STATES = ("ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "UNAVAIL", "REMOVED", "AVAIL", "INUSE", "SUSPENDED")
MD_ACTIONS = ("check", "stop-sync", "resync", "fail-remove", "add-spare")
ZFS_ACTIONS = ("scrub", "stop-sync", "offline", "online", "add-spare")


def _int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _mounts(facts: dict, devices: list[str]) -> tuple[list[str], int | None, int | None]:
    """Where a block device is mounted, and the first filesystem's size and used bytes."""
    fs = [f for f in facts.get("filesystems") or [] if f.get("device") in devices]
    mounts = [f["mount"] for f in fs]
    if not mounts:
        def walk(nodes):
            for n in nodes:
                if n.get("path") in devices or f"/dev/{n.get('name')}" in devices:
                    yield n
                yield from walk(n.get("children") or [])
        mounts = [m for n in walk(facts.get("disks") or []) for m in (n.get("mountpoints") or []) if m]
    return mounts, (fs[0]["size"] if fs else None), (fs[0]["used"] if fs else None)


def _md(facts: dict) -> list[dict]:
    pools = []
    for name, d in sorted((facts.get("md") or {}).items()):
        action = (d.get("sync_action") or "idle").strip()
        degraded = _int(d.get("degraded"))
        array_state = (d.get("array_state") or "").strip()
        completed = (d.get("sync_completed") or "").strip()
        pct = None
        if "/" in completed:
            a, b = completed.split("/", 1)
            if _int(b):
                pct = round(100 * _int(a) / _int(b), 1)
        members = []
        for dev, m in sorted((d.get("members") or {}).items()):
            st = (m.get("state") or "").strip()
            flags = [s.strip() for s in st.split(",") if s.strip()]
            role = "faulty" if "faulty" in flags else "spare" if "spare" in flags else "active"
            members.append({"name": f"/dev/{dev}", "role": role, "state": st or "unknown",
                            "errors": _int(m.get("errors")), "slot": (m.get("slot") or "").strip()})
        if array_state in ("inactive", "broken") or (not members and array_state not in ("clean", "active", "active-idle")):
            state = "failed"
        elif action in ("recover", "resync", "reshape"):
            state = "rebuilding"
        elif degraded > 0:
            state = "degraded"
        elif action in ("check", "repair"):
            state = "scrubbing"
        else:
            state = "healthy"
        detail = d.get("detail") or ""
        uuid = (re.search(r"^\s*UUID\s*:\s*(\S+)", detail, re.M) or [None, ""])[1]
        level = (d.get("level") or "").strip()
        mounts, fs_size, fs_used = _mounts(facts, [f"/dev/{name}"])
        size = _int(d.get("size")) * 512 or None
        actions = ["check", "stop-sync", "resync", "add-spare"] if level not in ("raid0", "linear") else ["check"]
        if level not in ("raid0", "linear"):
            actions.insert(3, "fail-remove")
        pools.append({"id": f"md:{name}", "type": "mdadm", "name": name, "level": level, "state": state, "size": size,
                      "used": fs_used, "free": (fs_size - fs_used) if fs_size is not None and fs_used is not None else None,
                      "mounts": mounts, "uuid": uuid, "members": members,
                      "sync": {"action": action, "percent": pct, "speed": _int(d.get("sync_speed")) or None},
                      "errors": sum(m["errors"] for m in members),
                      "raid_disks": _int(d.get("raid_disks")), "degraded": degraded, "actions": actions})
    return pools


def parse_zpool_status(text: str) -> dict:
    """{state, scan, scan_percent, scanning, resilvering, members: [{name, state, read, write, cksum, depth}], errors}."""
    out = {"state": "", "scan": "", "scan_percent": None, "scanning": False, "resilvering": False, "members": [], "errors": ""}
    m = re.search(r"^\s*state:\s*(\S+)", text, re.M)
    out["state"] = m.group(1) if m else ""
    m = re.search(r"^\s*scan:\s*(.*?)(?=^\s*\w+:|\Z)", text, re.M | re.S)
    scan = " ".join(m.group(1).split()) if m else ""
    out["scan"] = scan
    out["scanning"] = "scrub in progress" in scan
    out["resilvering"] = "resilver in progress" in scan
    p = re.search(r"([\d.]+)% done", scan)
    out["scan_percent"] = float(p.group(1)) if p and (out["scanning"] or out["resilvering"]) else None
    in_cfg, base = False, None
    for line in text.splitlines():
        if re.match(r"^\s*NAME\s+STATE", line):
            in_cfg = True
            continue
        if not in_cfg:
            continue
        if not line.strip() or line.lstrip().startswith("errors:"):
            if line.lstrip().startswith("errors:"):
                out["errors"] = line.split(":", 1)[1].strip()
            if not line.strip():
                continue
            break
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ZFS_STATES:
            indent = len(line) - len(line.lstrip(" \t"))
            base = indent if base is None else base
            out["members"].append({"name": parts[0], "state": parts[1], "read": _int(parts[2]) if len(parts) > 2 else 0,
                                   "write": _int(parts[3]) if len(parts) > 3 else 0,
                                   "cksum": _int(parts[4]) if len(parts) > 4 else 0, "depth": max((indent - base) // 2, 0)})
    return out


def _zfs(facts: dict) -> list[dict]:
    pools = []
    for z in facts.get("zfs") or []:
        name = z.get("name", "")
        st = parse_zpool_status((facts.get("zpool_status") or {}).get(name, ""))
        health = z.get("health") or st["state"]
        if st["resilvering"]:
            state = "rebuilding"
        elif health == "DEGRADED":
            state = "degraded"
        elif health in ("FAULTED", "UNAVAIL", "SUSPENDED", "REMOVED", "OFFLINE"):
            state = "failed"
        elif st["scanning"]:
            state = "scrubbing"
        elif health == "ONLINE":
            state = "healthy"
        else:
            state = "unknown"
        size, alloc, free = _int(z.get("size")), _int(z.get("alloc")), _int(z.get("free"))
        members = [{"name": m["name"], "role": "pool" if i == 0 else ("group" if m["depth"] <= 1 and not m["name"].startswith("/") else "drive"),
                    "state": m["state"], "errors": m["read"] + m["write"] + m["cksum"], "depth": m["depth"]}
                   for i, m in enumerate(st["members"])]
        pools.append({"id": f"zfs:{name}", "type": "zfs", "name": name, "level": "", "state": state, "size": size or None,
                      "used": alloc, "free": free, "mounts": [], "uuid": "", "members": members,
                      "sync": {"action": "scrub" if st["scanning"] else "resilver" if st["resilvering"] else "idle",
                               "percent": st["scan_percent"], "speed": None, "text": st["scan"]},
                      "errors": sum(m["errors"] for m in members), "data_errors": st["errors"], "actions": list(ZFS_ACTIONS)})
    return pools


def _lvm(facts: dict) -> list[dict]:
    lvm = facts.get("lvm") or {}
    pools = []
    for vg in lvm.get("vgs") or []:
        name = vg.get("vg_name", "")
        attr = vg.get("vg_attr") or ""
        pvs = [p for p in lvm.get("pvs") or [] if p.get("vg_name") == name]
        members = [{"name": p.get("pv_name", ""), "role": "physical volume",
                    "state": "missing" if len((p.get("pv_attr") or "")) > 2 and p["pv_attr"][2] == "m" else "ok",
                    "errors": 0} for p in pvs]
        lvs = [l for l in lvm.get("lvs") or [] if l.get("vg_name") == name]
        partial = (len(attr) > 3 and attr[3] == "p") or any(m["state"] == "missing" for m in members)
        size, free = _int(vg.get("vg_size")), _int(vg.get("vg_free"))
        pools.append({"id": f"lvm:{name}", "type": "lvm", "name": name, "level": "", "state": "degraded" if partial else "healthy",
                      "size": size or None, "used": size - free if size else None, "free": free, "mounts": [], "uuid": vg.get("vg_uuid", ""),
                      "members": members, "sync": {"action": "idle", "percent": None, "speed": None}, "errors": 0,
                      "logical_volumes": [{"name": l.get("lv_name", ""), "size": _int(l.get("lv_size"))} for l in lvs],
                      "actions": []})
    return pools


def build(facts: dict | None) -> list[dict]:
    """Every pool the host's facts show, mdadm first."""
    facts = facts or {}
    return [*_md(facts), *_zfs(facts), *_lvm(facts)]


def pool_members(pools: list[dict]) -> set[str]:
    """Device names already in a pool (full /dev paths and short names)."""
    out = set()
    for p in pools:
        for m in p["members"]:
            n = m["name"]
            out.add(n)
            out.add(n.removeprefix("/dev/"))
    return out


def unused_disks(facts: dict | None) -> list[dict]:
    """Whole disks and partitions that nothing uses: not mounted, no filesystem, no children, not in a pool."""
    facts = facts or {}
    taken = pool_members(build(facts))
    out = []

    def busy(n: dict) -> bool:
        return bool(n.get("children") or [m for m in n.get("mountpoints") or [] if m] or n.get("fstype")
                    or n.get("path") in taken or n.get("name") in taken)

    def walk(nodes, siblings_busy: bool = False):
        for n in nodes:
            # A partition is free only when nothing else on its disk is in use: a ZFS whole-disk member leaves an
            # empty 8 MB partition 9 beside its data partition, which is part of that pool, not a spare.
            if n.get("type") in ("disk", "part") and not busy(n) and not (n.get("type") == "part" and siblings_busy):
                out.append({"path": n.get("path") or f"/dev/{n.get('name')}", "size": _int(n.get("size")),
                            "model": n.get("model") or ""})
            kids = n.get("children") or []
            walk(kids, any(busy(k) for k in kids))
    walk(facts.get("disks") or [])
    return out


def warnings(facts: dict | None) -> list[dict]:
    """Dashboard warnings and monitoring alerts for degraded, failed and rebuilding pools."""
    w = []
    for p in build(facts):
        kind = {"mdadm": "RAID array", "zfs": "ZFS pool", "lvm": "Volume group"}[p["type"]]
        if p["state"] == "failed":
            w.append({"level": "error", "message": f"{kind} {p['name']} has failed"})
        elif p["state"] == "degraded":
            extra = f" ({p['degraded']} of {p['raid_disks']} drives missing)" if p.get("degraded") and p.get("raid_disks") else ""
            w.append({"level": "error", "message": f"{kind} {p['name']} is degraded{extra}"})
        elif p["state"] == "rebuilding":
            pct = p["sync"].get("percent")
            w.append({"level": "warning", "message": f"{kind} {p['name']} is rebuilding" + (f" ({pct:.0f}% done)" if pct is not None else "")})
        faulty = [m["name"] for m in p["members"] if m.get("role") == "faulty"]
        if faulty and p["state"] not in ("failed", "degraded", "rebuilding"):
            w.append({"level": "warning", "message": f"{kind} {p['name']} has a faulty drive: {', '.join(faulty)}"})
        if p["errors"] and p["type"] == "zfs":
            w.append({"level": "warning", "message": f"{kind} {p['name']} counts {p['errors']} read, write or checksum errors"})
    return w


# --- the safe actions (each one command on the host, arguments checked here) -----------------------

def _check(name: str, device: str | None, needs_device: bool) -> None:
    if not POOL_NAME.match(name or ""):
        raise hostadmin.AdminError("not a pool name")
    if needs_device and not SAFE_DEVICE.match(device or ""):
        raise hostadmin.AdminError("give the drive as a /dev path")


def action_script(pool: dict, op: str, device: str | None) -> str:
    """The shell script that does `op` on `pool`; raises AdminError for anything not allowed."""
    name, kind = pool["name"], pool["type"]
    if op not in pool.get("actions", []):
        raise hostadmin.AdminError(f"{op} isn't available for this {kind} pool")
    needs = op in ("fail-remove", "add-spare", "offline", "online")
    _check(name, device, needs)
    dev = shlex.quote(device) if device else ""
    pn = shlex.quote(name)
    if kind == "mdadm":
        md = shlex.quote(f"/dev/{name}")
        sysfs = shlex.quote(f"/sys/block/{name}/md/sync_action")
        return {
            "check": f"echo check > {sysfs}",
            "stop-sync": f"echo idle > {sysfs}",
            "resync": f"echo repair > {sysfs}",
            "fail-remove": f"set -e; mdadm {md} --fail {dev}; mdadm {md} --remove {dev}",
            "add-spare": f"mdadm {md} --add {dev}",
        }[op]
    if kind == "zfs":
        return {
            "scrub": f"zpool scrub {pn}",
            "stop-sync": f"zpool scrub -s {pn}",
            "offline": f"zpool offline {pn} {dev}",
            "online": f"zpool online {pn} {dev}",
            "add-spare": f"zpool add {pn} spare {dev}",
        }[op]
    raise hostadmin.AdminError(f"{kind} pools are read-only here")


def run_action(ssh, pool: dict, op: str, device: str | None = None) -> str:
    """Run the action as root over SSH and return its output; a mounted or pooled drive is refused for add-spare."""
    script = action_script(pool, op, device)
    if op == "add-spare":
        script = (f"if findmnt -rn -S {shlex.quote(device)} >/dev/null 2>&1; then echo 'that drive is mounted' >&2; exit 1; fi; "
                  f"test -b {shlex.quote(device)} || {{ echo 'not a block device' >&2; exit 1; }}; {script}")
    r = ssh.run(f"sudo -n bash -c {shlex.quote(script)}", timeout=120)
    text = (r.stdout + r.stderr).decode(errors="replace").strip()
    if r.returncode != 0:
        raise hostadmin.AdminError(text[-400:] or f"exit {r.returncode}")
    return text[-400:]
