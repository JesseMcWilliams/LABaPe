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


def _devices(facts: dict) -> dict[str, tuple[dict, dict]]:
    """{device path: (its lsblk node, the whole disk it sits on)} for every disk and partition."""
    idx: dict[str, tuple[dict, dict]] = {}

    def walk(nodes, disk=None):
        for n in nodes:
            d = n if n.get("type") == "disk" else (disk or n)
            idx[n.get("path") or f"/dev/{n.get('name')}"] = (n, d)
            walk(n.get("children") or [], d)
    walk(facts.get("disks") or [])
    return idx


def _identity(idx: dict, path: str) -> dict:
    """Which physical drive a device is: the disk, its model and serial number (they stay the same when the
    kernel renames /dev/sdc to /dev/sdd after a reboot)."""
    node, disk = idx.get(path) or ({}, {})
    return {"disk": disk.get("path") or "", "model": (disk.get("model") or "").strip(),
            "serial": (disk.get("serial") or "").strip(), "disk_size": _int(disk.get("size")) or None}


def _uuid_key(v: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (v or "").lower())


def _md_missing(detail: str, level: str, members: list[dict]) -> tuple[list[dict], list[str]]:
    """Slots `mdadm --detail` lists as removed (no drive in them), and the members left holding the only
    copy of what that slot mirrored (RAID 1; RAID 10 with two near copies, where slots 2n and 2n+1 are a pair)."""
    missing = []
    for ln in detail.splitlines():
        m = re.match(r"^\s*-\s+\d+\s+\d+\s+(\d+)\s+removed\s*$", ln)
        if m:
            missing.append({"slot": int(m.group(1)), "state": "removed"})
    near2 = level == "raid10" and re.search(r"^\s*Layout\s*:\s*near=2\b", detail, re.M) is not None
    by_slot = {_int(m["slot"], -1): m["name"] for m in members if m["role"] == "active" and m["slot"] != ""}
    risk: list[str] = []
    for s in missing:
        if level == "raid1":
            risk += [m["name"] for m in members if m["role"] == "active"]
        elif near2 and by_slot.get(s["slot"] ^ 1):
            s["partner"] = by_slot[s["slot"] ^ 1]
            risk.append(s["partner"])
    return missing, sorted(set(risk))


def readd_check(smart: dict | None, serial: str, disk: str) -> dict:
    """Is a drive that was failed out of an array fit to go back? Its own SMART reading decides: a drive that
    passed, with no reallocated, pending or uncorrectable sectors, was probably dropped by a cable, a power
    glitch or a controller reset. Anything else (or no reading) needs an admin's typed confirmation."""
    rows = (smart or {}).get("disks") or []
    d = next((x for x in rows if serial and x.get("serial") == serial), None) or         next((x for x in rows if disk and x.get("device") == disk), None)
    if d is None:
        return {"ok": False, "reasons": ["no SMART reading for this drive yet"]}
    reasons = []
    if d.get("passed") is False:
        reasons.append("its SMART health check failed")
    for key, label in (("reallocated", "reallocated"), ("pending", "pending"), ("uncorrectable", "offline-uncorrectable")):
        if _int(d.get(key)) > 0:
            reasons.append(f"{_int(d.get(key))} {label} sector{'s' if _int(d.get(key)) != 1 else ''}")
    return {"ok": not reasons, "reasons": reasons}


def _md(facts: dict, smart: dict | None = None) -> list[dict]:
    idx = _devices(facts)
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
                            "errors": _int(m.get("errors")), "slot": (m.get("slot") or "").strip(),
                            **_identity(idx, f"/dev/{dev}")})
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
        missing, at_risk = _md_missing(detail, level, members)
        # Drives that carry this array's superblock but are not in it: the ones that were failed out.
        key, names = _uuid_key(uuid), {m["name"] for m in members}
        former = [{"name": p, **_identity(idx, p), "size": _int(n.get("size")) or None}
                  for p, (n, _d) in sorted(idx.items())
                  if n.get("fstype") == "linux_raid_member" and key and _uuid_key(n.get("uuid")) == key and p not in names]
        for f in former:
            f["assessment"] = readd_check(smart, f["serial"], f["disk"])
        mounts, fs_size, fs_used = _mounts(facts, [f"/dev/{name}"])
        size = _int(d.get("size")) * 512 or None
        actions = ["check", "stop-sync", "resync", "add-spare"] if level not in ("raid0", "linear") else ["check"]
        if level not in ("raid0", "linear"):
            actions.insert(3, "fail-remove")
            if former and degraded > 0:
                actions.append("re-add")
        pools.append({"id": f"md:{name}", "type": "mdadm", "name": name, "level": level, "state": state, "size": size,
                      "used": fs_used, "free": (fs_size - fs_used) if fs_size is not None and fs_used is not None else None,
                      "mounts": mounts, "uuid": uuid, "members": members,
                      "sync": {"action": action, "percent": pct, "speed": _int(d.get("sync_speed")) or None},
                      "errors": sum(m["errors"] for m in members),
                      "raid_disks": _int(d.get("raid_disks")), "degraded": degraded, "actions": actions,
                      "missing": missing, "at_risk": at_risk, "former_members": former})
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


def build(facts: dict | None, smart: dict | None = None, plans: bool = True) -> list[dict]:
    """Every pool the host's facts show, mdadm first. The SMART readings judge a drive that could go back (re-add)."""
    facts = facts or {}
    pools = [*_md(facts, smart), *_zfs(facts), *_lvm(facts)]
    for p in pools if plans else []:
        p["replace"] = replace_plan(p, facts)
    return pools


def part_name(disk: str, number: int) -> str:
    """/dev/sdb + 1 -> /dev/sdb1, /dev/nvme0n1 + 1 -> /dev/nvme0n1p1."""
    return f"{disk}{'p' if disk[-1].isdigit() else ''}{number}"


def replace_plan(pool: dict, facts: dict) -> dict | None:
    """What a drive replacement (plan R1) would do for a degraded md array: the drive to pull, the surviving drive whose
    partition table is copied, the partition that joins the array, and the free whole disks big enough to take it."""
    if pool["type"] != "mdadm" or not pool.get("degraded") or pool["level"] in ("raid0", "linear"):
        return None
    active = [m for m in pool["members"] if m["role"] == "active" and m.get("disk")]
    if not active:
        return None
    idx = _devices(facts)
    tpl = active[0]
    former = (pool.get("former_members") or [None])[0]
    ref = former["name"] if former else tpl["name"]
    m = re.search(r"(\d+)$", ref)
    if not m or not tpl["disk"] or ref == tpl["disk"] or idx.get(tpl["name"], ({}, {}))[0].get("type") != "part":
        return None                                    # members that are whole disks have no table to copy
    number, need = int(m.group(1)), tpl.get("disk_size") or 0
    cands = []
    for d in unused_disks(facts):
        node = idx.get(d["path"], ({}, {}))[0]
        if node.get("type") == "disk" and d["size"] >= need:
            cands.append({**d, "serial": (node.get("serial") or "").strip()})
    return {"template": tpl["disk"], "template_serial": tpl.get("serial", ""), "partition": number, "need": need,
            "pull": [{"disk": f["disk"], "model": f["model"], "serial": f["serial"], "size": f.get("size")} for f in pool.get("former_members") or []],
            "at_risk": pool.get("at_risk") or [], "candidates": cands}


def replace_script(pool: dict, device: str) -> str:
    """Copy the surviving drive's partition table to the new drive (new disk and partition ids), then add the partition
    to the array; the array rebuilds onto it."""
    plan = pool.get("replace")
    if not plan:
        raise hostadmin.AdminError("this pool has no replacement to do")
    _check(pool["name"], device, True)
    if device not in {c["path"] for c in plan["candidates"]}:
        raise hostadmin.AdminError("that drive isn't free, or it is smaller than the drive it replaces")
    tpl, part = shlex.quote(plan["template"]), shlex.quote(part_name(device, plan["partition"]))
    new, md = shlex.quote(device), shlex.quote(f"/dev/{pool['name']}")
    return (f"set -eo pipefail; "
            f"if findmnt -rn -S {new} >/dev/null 2>&1; then echo 'that drive is mounted' >&2; exit 1; fi; "
            f"test -b {new} || {{ echo 'not a block device' >&2; exit 1; }}; "
            f"[ \"$(blockdev --getsize64 {new})\" -ge \"$(blockdev --getsize64 {tpl})\" ] || {{ echo 'the new drive is smaller' >&2; exit 1; }}; "
            f"if [ -n \"$(wipefs -n {new})\" ]; then echo 'the new drive has signatures on it' >&2; exit 1; fi; "
            f"sfdisk -d {tpl} | grep -v '^label-id:' | sed -E 's/, *uuid=[^,]+//' | sfdisk {new}; "
            f"udevadm settle; partprobe {new} || true; udevadm settle; "
            f"test -b {part} || {{ echo 'the new partition did not appear' >&2; exit 1; }}; "
            f"mdadm {md} --add {part}")


def run_replace(ssh, pool: dict, device: str) -> str:
    r = ssh.run(f"sudo -n bash -c {shlex.quote(replace_script(pool, device))}", timeout=180)
    text = (r.stdout + r.stderr).decode(errors="replace").strip()
    if r.returncode != 0:
        raise hostadmin.AdminError(text[-400:] or f"exit {r.returncode}")
    return text[-400:]


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
    taken = pool_members(build(facts, plans=False))
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
            out = [f"{f['name']}" + (f" (serial {f['serial']})" if f.get("serial") else "") for f in p.get("former_members") or []]
            extra += f"; out of the array: {', '.join(out)}" if out else ""
            extra += f"; the only copy of its data is on {', '.join(p['at_risk'])}" if p.get("at_risk") else ""
            w.append({"level": "error", "message": f"{kind} {p['name']} is degraded{extra}"})
        elif p["state"] == "rebuilding":
            pct = p["sync"].get("percent")
            w.append({"level": "warning", "message": f"{kind} {p['name']} is rebuilding" + (f" ({pct:.0f}% done)" if pct is not None else "")})
        for f in p.get("former_members") or []:
            # Names the whole disk, so the Drives and volumes page flags the row (and links to its details).
            ident = f"{f['disk'] or f['name']}" + (f", serial {f['serial']}" if f.get("serial") else "")
            w.append({"level": "warning", "disk": f["disk"] or f["name"],
                      "message": f"{ident}, is not in {kind} {p['name']} but still carries its label: it was failed out; replace it or add it back"})
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


def action_script(pool: dict, op: str, device: str | None, full: bool = False) -> str:
    """The shell script that does `op` on `pool`; raises AdminError for anything not allowed."""
    name, kind = pool["name"], pool["type"]
    if op not in pool.get("actions", []):
        raise hostadmin.AdminError(f"{op} isn't available for this {kind} pool")
    needs = op in ("fail-remove", "add-spare", "re-add", "offline", "online")
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
            # --re-add copies only what changed (the array's write-intent bitmap); full wipes the old label and
            # rebuilds the whole drive, for when mdadm refuses the quick way.
            "re-add": f"set -e; mdadm --zero-superblock {dev}; mdadm {md} --add {dev}" if full else f"mdadm {md} --re-add {dev}",
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


def run_action(ssh, pool: dict, op: str, device: str | None = None, full: bool = False) -> str:
    """Run the action as root over SSH and return its output; a mounted drive is refused for add-spare and re-add."""
    script = action_script(pool, op, device, full)
    if op in ("add-spare", "re-add"):
        script = (f"if findmnt -rn -S {shlex.quote(device)} >/dev/null 2>&1; then echo 'that drive is mounted' >&2; exit 1; fi; "
                  f"test -b {shlex.quote(device)} || {{ echo 'not a block device' >&2; exit 1; }}; {script}")
    r = ssh.run(f"sudo -n bash -c {shlex.quote(script)}", timeout=120)
    text = (r.stdout + r.stderr).decode(errors="replace").strip()
    if r.returncode != 0:
        raise hostadmin.AdminError(text[-400:] or f"exit {r.returncode}")
    return text[-400:]
