"""KVM host resources and load (design §21.2, question 57).

The worker collects these every minute through libvirt (it has the
socket and the storage mounts; the API doesn't) and stores them in
host_stats. `assess` turns a host's stats, plus an optional new
environment's demand, into the warnings the UI shows and the one hard
limit the API enforces:

- warn above 4 vCPUs per logical CPU (allocated to the host's VMs);
- warn above 90% of RAM committed (the max memory of every defined VM);
- warn below 15% free on the VM storage;
- refuse a new environment that would commit more than 100% of RAM.

For the dashboard (design §22.1) it also records host CPU and disk
activity and each VM's state, CPU, memory, disk activity and free space
(from qemu-guest-agent where the guest runs it), as rates between two
samples, and keeps one sample a minute for 24 hours (metric_samples).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import HostStats, KvmHost, MetricSample, utcnow
from . import hostio

THRESHOLDS = {"vcpu_per_cpu_warn": 4.0, "ram_committed_warn": 0.90, "ram_committed_block": 1.00,
              "disk_free_warn": 0.15}


def _virsh(uri: str, *args: str) -> str:
    if hostio.is_remote(uri):
        hostio.Host(uri).prime()
    return subprocess.run(["virsh", "--connect", hostio.effective_uri(uri), *args], capture_output=True, text=True,
                          timeout=30, check=True, env=hostio.tool_env()).stdout


def _kib(text: str, key: str) -> int:
    m = re.search(rf"^{re.escape(key)}\s*:?\s*(\d+)\s*KiB", text, re.M)
    return int(m.group(1)) if m else 0


def _disk(path: str) -> dict:
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1)}
    except OSError as exc:
        return {"path": path, "total_gb": 0, "free_gb": 0, "error": exc.strerror or str(exc)}


# virsh domstats state.state: 1 running, 2 blocked, 3 paused, 4 shutting
# down, 5 shut off, 6 crashed, 7 suspended. Running and blocked are online.
STATES = {"0": "unknown", "1": "running", "2": "running", "3": "paused", "4": "shutting down", "5": "shut off",
          "6": "crashed", "7": "suspended"}
SAMPLE_HOURS = 24


def parse_domstats(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Domain:"):
            cur = out.setdefault(line.split("'")[1], {})
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur[k] = v
    return out


def parse_guestinfo(text: str) -> dict:
    """`virsh guestinfo --filesystem` -> total and free bytes, each device once."""
    kv = dict(re.findall(r"^(fs\.\d+\.[\w-]+)\s*:\s*(.*)$", text, re.M))
    seen, total, used = set(), 0, 0
    for i in range(int(re.search(r"^fs\.count\s*:\s*(\d+)", text, re.M).group(1)) if "fs.count" in text else 0):
        t, u = kv.get(f"fs.{i}.total-bytes"), kv.get(f"fs.{i}.used-bytes")
        dev = kv.get(f"fs.{i}.name", str(i))
        if t is None or u is None or dev in seen:
            continue
        seen.add(dev)
        total, used = total + int(t), used + int(u)
    return {"total_gb": round(total / 2**30, 1), "free_gb": round((total - used) / 2**30, 1)} if total else {}


def parse_guest_full(text: str) -> dict:
    """All of `virsh guestinfo` (design §28.6): OS, host name, time zone, logged-in users,
    filesystems, disks and interfaces with their addresses, as lists of dicts."""
    kv = dict(re.findall(r"^([\w.-]+)[ \t]*:[ \t]*(.*)$", text, re.M))     # [ \t]: an empty value keeps its own line

    def items(prefix: str) -> list[dict]:
        n = int(kv.get(f"{prefix}.count", "0") or 0)
        out = []
        for i in range(n):
            head = f"{prefix}.{i}."
            out.append({k[len(head):]: v for k, v in kv.items() if k.startswith(head)})
        return out

    ifaces = []
    for i, iface in enumerate(items("if")):
        n = int(iface.get("addr.count", "0") or 0)
        ifaces.append({"name": iface.get("name"), "hwaddr": iface.get("hwaddr"),
                       "addresses": [f"{iface.get(f'addr.{j}.addr')}/{iface.get(f'addr.{j}.prefix')}" for j in range(n)]})
    return {
        "os": {k[3:]: v for k, v in kv.items() if k.startswith("os.")},
        "hostname": kv.get("hostname", ""), "timezone": kv.get("timezone.name", ""),
        "users": [{"name": u.get("name"), "domain": u.get("domain", ""), "login_time": u.get("login-time")} for u in items("user")],
        "filesystems": [{"mount": f.get("mountpoint"), "name": f.get("name"), "type": f.get("fstype"),
                         "total": int(f["total-bytes"]) if f.get("total-bytes", "").isdigit() else None,
                         "used": int(f["used-bytes"]) if f.get("used-bytes", "").isdigit() else None} for f in items("fs")],
        "disks": [{"name": d.get("name"), "partition": d.get("partition"), "alias": d.get("alias"),
                   "guest_alias": d.get("guest_alias")} for d in items("disk")],
        "interfaces": ifaces,
    }


def _guest(uri: str, name: str) -> tuple[bool | None, dict, dict]:
    """(responding, filesystem totals, everything the agent reports). responding is None when
    the VM has no guest-agent channel, False when the agent doesn't answer."""
    try:
        r = subprocess.run(["virsh", "--connect", hostio.effective_uri(uri), "guestinfo", name],
                           capture_output=True, text=True, timeout=10, check=False, env=hostio.tool_env())
    except subprocess.TimeoutExpired:
        return False, {}, {}
    if r.returncode == 0:
        return True, parse_guestinfo(r.stdout), parse_guest_full(r.stdout)
    return (None if "not configured" in r.stderr else False), {}, {}


def _proc_cpu() -> tuple[int, int] | None:
    try:
        f = [int(x) for x in open("/proc/stat", encoding="ascii").readline().split()[1:9]]
    except (OSError, ValueError):
        return None
    return f[3] + f[4], sum(f)          # idle + iowait, total


def _proc_disk(path: str) -> tuple[int, int] | None:
    """Sectors read and written on the device holding `path`."""
    try:
        st = os.stat(path)
        want = (os.major(st.st_dev), os.minor(st.st_dev))
        for line in open("/proc/diskstats", encoding="ascii"):
            f = line.split()
            if (int(f[0]), int(f[1])) == want:
                return int(f[5]), int(f[9])
    except (OSError, ValueError, IndexError, AttributeError):   # AttributeError: no os.major off Linux
        pass
    return None


def _rate(now: float | int | None, before: float | int | None, seconds: float) -> float | None:
    if now is None or before is None or seconds <= 0 or now < before:
        return None
    return (now - before) / seconds


def collect(host: KvmHost, prev: dict | None = None) -> dict:
    uri = host.libvirt_uri
    prev_c = (prev or {}).get("_counters", {})
    t = time.time()
    elapsed = t - prev_c.get("t", t)
    node = _virsh(uri, "nodeinfo")
    mem = _virsh(uri, "nodememstats")
    cpus = int(re.search(r"^CPU\(s\):\s*(\d+)", node, re.M).group(1))
    cores = int(re.search(r"^Core\(s\) per socket:\s*(\d+)", node, re.M).group(1)) * \
        int(re.search(r"^CPU socket\(s\):\s*(\d+)", node, re.M).group(1)) * \
        int(re.search(r"^NUMA cell\(s\):\s*(\d+)", node, re.M).group(1) or 1)
    total_kib = _kib(mem, "total")
    avail_kib = _kib(mem, "free") + _kib(mem, "buffers") + _kib(mem, "cached")

    doms = parse_domstats(_virsh(uri, "domstats", "--raw", "--state", "--cpu-total", "--balloon", "--vcpu",
                                 "--block"))
    running = [n for n, d in doms.items() if d.get("state.state") in ("1", "2")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        guests = dict(zip(running, pool.map(lambda n: _guest(uri, n), running)))
    vms, vm_counters = [], {}
    for name, d in sorted(doms.items()):
        on = name in guests
        vcpus = int(d.get("vcpu.maximum") or d.get("vcpu.current") or 0)
        max_kib = int(d.get("balloon.maximum") or d.get("balloon.current") or 0)
        disks = [i for i in range(int(d.get("block.count", 0)))
                 if d.get(f"block.{i}.capacity") and not d.get(f"block.{i}.path", "").endswith(".iso")]
        rd = sum(int(d.get(f"block.{i}.rd.bytes", 0)) for i in disks)
        wr = sum(int(d.get(f"block.{i}.wr.bytes", 0)) for i in disks)
        cpu_ns = int(d.get("cpu.time", 0))
        before = prev_c.get("vms", {}).get(name, {})
        vm_counters[name] = {"cpu": cpu_ns, "rd": rd, "wr": wr}
        cpu_rate = _rate(cpu_ns, before.get("cpu"), elapsed)
        responding, fs, info = guests.get(name, (None, {}, {}))
        # Memory used inside the guest needs the balloon driver's stats; else unknown.
        avail, unused = d.get("balloon.available"), d.get("balloon.unused")
        vms.append({
            "name": name,
            "state": STATES.get(d.get("state.state", "0"), "unknown"),
            "online": on,
            "responding": responding if on else None,
            "vcpus": vcpus,
            "memory_mb": max_kib // 1024,
            "memory_used_mb": (int(avail) - int(unused)) // 1024 if on and avail and unused else None,
            "cpu_pct": round(min(cpu_rate / 1e9 / max(vcpus, 1) * 100, 100), 1) if on and cpu_rate is not None else None,
            "disk_read_bps": _rate(rd, before.get("rd"), elapsed) if on else None,
            "disk_write_bps": _rate(wr, before.get("wr"), elapsed) if on else None,
            "disk_capacity_gb": round(sum(int(d.get(f"block.{i}.capacity", 0)) for i in disks) / 2**30, 1),
            "disk_allocated_gb": round(sum(int(d.get(f"block.{i}.allocation", 0)) for i in disks) / 2**30, 1),
            "guest_disk": fs,           # {total_gb, free_gb} from the guest agent, or {}
            "guest_info": info,         # OS, users, filesystems, disks, interfaces (design §28.6), or {}
        })

    if hostio.is_remote(uri):
        # /proc and the storage paths are on the host: one probe over SSH.
        probe = hostio.Host(uri).python(hostio.PROBE, {"paths": {"vm": host.vm_storage_path,
                                                                  "templates": host.template_storage_path}})
        cpu_now, disk_now, load = probe["cpu"], probe["disk"], probe["load"]
        storage = probe["storage"]
    else:
        cpu_now, disk_now = _proc_cpu(), _proc_disk(host.vm_storage_path)
        try:
            load = [float(x) for x in open("/proc/loadavg", encoding="ascii").read().split()[:3]]
        except OSError:
            load = []
        storage = {"vm": _disk(host.vm_storage_path), "templates": _disk(host.template_storage_path)}
    cpu_pct = None
    if cpu_now and prev_c.get("cpu"):
        idle, total = cpu_now[0] - prev_c["cpu"][0], cpu_now[1] - prev_c["cpu"][1]
        cpu_pct = round((1 - idle / total) * 100, 1) if total > 0 else None
    disk_io = {"read_bps": None, "write_bps": None}
    if disk_now and prev_c.get("disk"):
        disk_io = {"read_bps": _rate(disk_now[0] * 512, prev_c["disk"][0] * 512, elapsed),
                   "write_bps": _rate(disk_now[1] * 512, prev_c["disk"][1] * 512, elapsed)}
    return {
        "cpus": cpus, "cores": min(cores, cpus),
        "memory_total_mb": total_kib // 1024, "memory_available_mb": avail_kib // 1024,
        "load": load, "cpu_pct": cpu_pct, "disk_io": disk_io,
        "vms_defined": len(vms), "vms_running": sum(v["online"] for v in vms),
        "vcpus_allocated": sum(v["vcpus"] for v in vms),
        "vcpus_running": sum(v["vcpus"] for v in vms if v["online"]),
        "memory_committed_mb": sum(v["memory_mb"] for v in vms),
        "memory_running_mb": sum(v["memory_mb"] for v in vms if v["online"]),
        "storage": storage,
        "vms": vms,
        "_counters": {"t": t, "cpu": cpu_now, "disk": disk_now, "vms": vm_counters},
    }


def host_sample(stats: dict) -> dict:
    vm = stats.get("storage", {}).get("vm", {})
    total = max(stats.get("memory_total_mb", 0), 1)
    return {"cpu_pct": stats.get("cpu_pct"),
            "memory_used_pct": round((1 - stats.get("memory_available_mb", 0) / total) * 100, 1),
            "disk_free_gb": vm.get("free_gb"), **stats.get("disk_io", {})}


def vm_sample(v: dict) -> dict:
    return {k: v.get(k) for k in ("online", "cpu_pct", "memory_used_mb", "disk_read_bps", "disk_write_bps")} | \
        {"disk_free_gb": v.get("guest_disk", {}).get("free_gb")}


def collect_all(db: Session) -> None:
    now = utcnow()
    for host in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))):
        row = db.get(HostStats, host.id) or HostStats(kvm_host_id=host.id)
        try:
            row.stats = collect(host, row.stats)
            row.error = ""
            db.add(MetricSample(at=now, kvm_host_id=host.id, vm="", data=host_sample(row.stats)))
            db.add_all(MetricSample(at=now, kvm_host_id=host.id, vm=v["name"], data=vm_sample(v))
                       for v in row.stats["vms"])
        except (subprocess.SubprocessError, OSError, AttributeError, ValueError, RuntimeError) as exc:
            row.error = str(exc)[:2000]
        row.collected_at = now
        db.merge(row)
        db.commit()
    db.execute(delete(MetricSample).where(MetricSample.at < now - dt.timedelta(hours=SAMPLE_HOURS)))
    db.commit()


def assess(stats: dict, demand: dict | None = None) -> dict:
    """Ratios and findings for a host, with `demand` ({vcpus, memory_mb,
    disk_gb}) added as if a new environment were deployed."""
    d = demand or {"vcpus": 0, "memory_mb": 0, "disk_gb": 0}
    t = THRESHOLDS
    cpus = max(stats.get("cpus", 0), 1)
    mem_total = max(stats.get("memory_total_mb", 0), 1)
    vm_disk = stats.get("storage", {}).get("vm", {})
    disk_total = max(vm_disk.get("total_gb", 0), 1)
    vcpu_ratio = (stats.get("vcpus_allocated", 0) + d["vcpus"]) / cpus
    mem_ratio = (stats.get("memory_committed_mb", 0) + d["memory_mb"]) / mem_total
    disk_free_ratio = (vm_disk.get("free_gb", 0) - d["disk_gb"]) / disk_total
    findings = []
    if vcpu_ratio > t["vcpu_per_cpu_warn"]:
        findings.append({"level": "warning", "resource": "cpu",
                         "message": f"{vcpu_ratio:.1f} vCPUs per logical CPU allocated (warn above "
                                    f"{t['vcpu_per_cpu_warn']:.0f}); VMs will compete for CPU."})
    if mem_ratio > t["ram_committed_block"]:
        findings.append({"level": "block", "resource": "memory",
                         "message": f"{mem_ratio:.0%} of RAM would be committed; more than 100% isn't allowed. "
                                    "Use less memory, or destroy environments on this host first."})
    elif mem_ratio > t["ram_committed_warn"]:
        findings.append({"level": "warning", "resource": "memory",
                         "message": f"{mem_ratio:.0%} of RAM committed (warn above {t['ram_committed_warn']:.0%})."})
    if disk_free_ratio < t["disk_free_warn"]:
        findings.append({"level": "warning", "resource": "disk",
                         "message": f"{max(disk_free_ratio, 0):.0%} of the VM storage would be free (warn below "
                                    f"{t['disk_free_warn']:.0%}); disks grow as VMs write."})
    return {"vcpu_ratio": round(vcpu_ratio, 2), "memory_committed_ratio": round(mem_ratio, 3),
            "disk_free_ratio": round(disk_free_ratio, 3), "findings": findings, "thresholds": t}


def demand_of(groups: list[dict]) -> dict:
    return {
        "vcpus": sum(g.get("count", 1) * g.get("cpu_count", 2) for g in groups),
        "memory_mb": sum(g.get("count", 1) * g.get("memory_mb", 4096) for g in groups),
        "disk_gb": sum(g.get("count", 1) * g.get("disk_gb", 40) for g in groups),
    }

