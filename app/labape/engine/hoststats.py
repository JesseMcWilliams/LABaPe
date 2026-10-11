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
"""
from __future__ import annotations

import re
import shutil
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HostStats, KvmHost, utcnow

THRESHOLDS = {"vcpu_per_cpu_warn": 4.0, "ram_committed_warn": 0.90, "ram_committed_block": 1.00,
              "disk_free_warn": 0.15}


def _virsh(uri: str, *args: str) -> str:
    return subprocess.run(["virsh", "--connect", uri, *args], capture_output=True, text=True, timeout=30,
                          check=True).stdout


def _kib(text: str, key: str) -> int:
    m = re.search(rf"^{re.escape(key)}\s*:?\s*(\d+)\s*KiB", text, re.M)
    return int(m.group(1)) if m else 0


def _disk(path: str) -> dict:
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1)}
    except OSError:
        return {"path": path, "total_gb": 0, "free_gb": 0}


def collect(host: KvmHost) -> dict:
    uri = host.libvirt_uri
    node = _virsh(uri, "nodeinfo")
    mem = _virsh(uri, "nodememstats")
    cpus = int(re.search(r"^CPU\(s\):\s*(\d+)", node, re.M).group(1))
    cores = int(re.search(r"^Core\(s\) per socket:\s*(\d+)", node, re.M).group(1)) * \
        int(re.search(r"^CPU socket\(s\):\s*(\d+)", node, re.M).group(1)) * \
        int(re.search(r"^NUMA cell\(s\):\s*(\d+)", node, re.M).group(1) or 1)
    total_kib = _kib(mem, "total")
    avail_kib = _kib(mem, "free") + _kib(mem, "buffers") + _kib(mem, "cached")
    vms = []
    for name in _virsh(uri, "list", "--all", "--name").split():
        info = _virsh(uri, "dominfo", name)
        vms.append({
            "name": name,
            "running": "running" in (re.search(r"^State:\s*(.+)$", info, re.M) or [None, ""])[1],
            "vcpus": int((re.search(r"^CPU\(s\):\s*(\d+)", info, re.M) or [None, 0])[1]),
            "memory_mb": _kib(info, "Max memory") // 1024,
        })
    try:
        load = [float(x) for x in open("/proc/loadavg", encoding="ascii").read().split()[:3]]
    except OSError:
        load = []
    return {
        "cpus": cpus, "cores": min(cores, cpus),
        "memory_total_mb": total_kib // 1024, "memory_available_mb": avail_kib // 1024,
        "load": load,
        "vms_defined": len(vms), "vms_running": sum(v["running"] for v in vms),
        "vcpus_allocated": sum(v["vcpus"] for v in vms),
        "vcpus_running": sum(v["vcpus"] for v in vms if v["running"]),
        "memory_committed_mb": sum(v["memory_mb"] for v in vms),
        "memory_running_mb": sum(v["memory_mb"] for v in vms if v["running"]),
        "storage": {"vm": _disk(host.vm_storage_path), "templates": _disk(host.template_storage_path)},
    }


def collect_all(db: Session) -> None:
    for host in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))):
        row = db.get(HostStats, host.id) or HostStats(kvm_host_id=host.id)
        try:
            row.stats = collect(host)
            row.error = ""
        except (subprocess.SubprocessError, OSError, AttributeError, ValueError) as exc:
            row.error = str(exc)[:2000]
        row.collected_at = utcnow()
        db.merge(row)
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

