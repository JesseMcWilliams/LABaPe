"""Moving a host group's VMs to another KVM host while they run (design §46, P2; question 152).

The worker drives libvirt's live migration between the two hosts (`virsh migrate --live
--copy-storage-all`, client-managed: it holds a connection to each): QEMU on the source streams
memory and the disks to QEMU on the destination, which takes over. The destination's disks are
created empty first (same virtual size), the NIC goes onto the destination's bridge for the
group's network, CD-ROMs are emptied (the seed ISOs only matter at first boot). Afterwards the VM
carries the destination part's workspace tag, the source's old disk files are removed, the group
names its new host, and a deploy reconciles OpenTofu: the destination's part adopts the running VM
(existing-domain.sh), the source's part drops its record of it (destroy-vm.sh finds no VM there).
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import spread
from ..models import Environment, HostNetwork, Job, KvmHost, Network
from ..naming import instance_names
from . import hostio

MIGRATE_TIMEOUT = 6 * 3600


class MoveError(RuntimeError):
    pass


def virsh(uri: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", "--connect", hostio.effective_uri(uri), *args], capture_output=True, text=True,
                          timeout=timeout, check=False, env=hostio.tool_env())


def _ok(r: subprocess.CompletedProcess, what: str) -> str:
    if r.returncode != 0:
        raise MoveError(f"{what}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def address(h: KvmHost) -> str:
    """Where the other host's QEMU reaches this one (migration and disk streams)."""
    a = h.ssh_address or urlsplit(h.libvirt_uri).hostname or ""
    if not a:
        raise MoveError(f"{h.name} has no address (set its SSH address under Admin → KVM hosts)")
    return a


def bridge_on(db: Session, h: KvmHost, network: str) -> str:
    hn = db.execute(select(HostNetwork).join(Network, HostNetwork.network_id == Network.id)
                    .where(HostNetwork.kvm_host_id == h.id, Network.name == network)).scalar_one_or_none()
    if hn is None:
        raise MoveError(f"{h.name} doesn't carry network {network}")
    return hn.bridge


def plan_xml(xml: str, src: KvmHost, dst: KvmHost, bridge: str) -> tuple[str, list[tuple[str, str]], list[str]]:
    """The VM's definition for the destination: its NIC on `bridge`, its disks in the destination's
    VM storage, CD-ROMs empty. Returns the XML, the (source, destination) disk paths and the
    source's CD-ROM files."""
    root = ET.fromstring(xml)
    disks, cdroms = [], []
    src_dir, dst_dir = src.vm_storage_path.rstrip("/"), dst.vm_storage_path.rstrip("/")
    for d in root.iter("disk"):
        s = d.find("source")
        path = s.get("file") if s is not None else None
        if d.get("device") == "cdrom":
            if s is not None:
                if path:
                    cdroms.append(path)
                d.remove(s)
            continue
        if d.get("device") != "disk" or not path:
            continue
        new = f"{dst_dir}/{path[len(src_dir) + 1:]}" if path.startswith(src_dir + "/") else f"{dst_dir}/{Path(path).name}"
        s.set("file", new)
        disks.append((path, new))
    for i in root.iter("interface"):
        s = i.find("source")
        if i.get("type") == "bridge" and s is not None:
            s.set("bridge", bridge)
    return ET.tostring(root, encoding="unicode"), disks, cdroms


def _qcow_size(h: hostio.Host, path: str) -> int:
    r = h.run(f"qemu-img info -U --output=json {shlex.quote(path)}", timeout=120)
    if r.returncode != 0:
        raise MoveError(f"reading {path}: {r.stderr.decode(errors='replace').strip()[-300:]}")
    return int(json.loads(r.stdout.decode())["virtual-size"])


def move_vm(src: KvmHost, dst: KvmHost, vm: str, bridge: str, dst_tag: str, log, run=virsh) -> None:
    """One running VM from `src` to `dst`; raises MoveError (the VM stays on the source)."""
    state = _ok(run(src.libvirt_uri, "domstate", vm), f"{vm} on {src.name}").strip()
    if state != "running":
        raise MoveError(f"{vm} is {state}: start it first (only running VMs move; design §46)")
    if run(src.libvirt_uri, "snapshot-list", vm, "--name").stdout.strip():
        raise MoveError(f"{vm} has snapshots, which libvirt can't move: delete them first")
    if run(dst.libvirt_uri, "domstate", vm).returncode == 0:
        raise MoveError(f"a VM named {vm} already exists on {dst.name}")
    xml = _ok(run(src.libvirt_uri, "dumpxml", "--migratable", vm), f"reading {vm}")
    new_xml, disks, cdroms = plan_xml(xml, src, dst, bridge)
    for target in [t.get("dev") for d in ET.fromstring(xml).iter("disk") if d.get("device") == "cdrom"
                   and d.find("source") is not None for t in [d.find("target")] if t is not None]:
        _ok(run(src.libvirt_uri, "change-media", vm, target, "--eject", "--live", "--config", "--force"),
            f"emptying {vm}'s CD-ROM {target}")
    sh, dh = hostio.Host(src.libvirt_uri), hostio.Host(dst.libvirt_uri)
    sh.prime()
    dh.prime()
    made = []
    try:
        for s_path, d_path in disks:
            size = _qcow_size(sh, s_path)
            r = dh.run(f"test ! -e {shlex.quote(d_path)} && mkdir -p {shlex.quote(str(Path(d_path).parent))} && "
                       f"qemu-img create -q -f qcow2 {shlex.quote(d_path)} {size}", timeout=120)
            if r.returncode != 0:
                raise MoveError(f"creating {d_path} on {dst.name}: "
                                f"{r.stderr.decode(errors='replace').strip()[-300:] or 'it exists already'}")
            made.append(d_path)
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as fh:
            fh.write(new_xml)
            xml_file = fh.name
        to = address(dst)
        log(f"labape: moving {vm} from {src.name} to {dst.name} (live, {len(disks)} disk(s) copied)\n")
        r = run(src.libvirt_uri, "migrate", "--live", "--persistent", "--undefinesource", "--copy-storage-all",
                "--xml", xml_file, "--persistent-xml", xml_file, "--migrateuri", f"tcp://{to}",
                "--disks-uri", f"tcp://{to}", "--verbose", vm, hostio.effective_uri(dst.libvirt_uri),
                timeout=MIGRATE_TIMEOUT)
        Path(xml_file).unlink(missing_ok=True)
        _ok(r, f"moving {vm}")
    except Exception:
        for d_path in made:                       # the VM still runs on the source
            dh.run(f"rm -f {shlex.quote(d_path)}", timeout=60)
        raise
    _ok(run(dst.libvirt_uri, "desc", vm, "--live", "--config", dst_tag), f"tagging {vm} on {dst.name}")
    old = [p for p, _ in disks] + [p for p in cdroms if p.endswith(("-seed.iso", "-autounattend.iso"))]
    r = sh.run("rm -f " + " ".join(shlex.quote(p) for p in old), timeout=120)
    if r.returncode != 0:
        log(f"labape: warning: couldn't remove {vm}'s old files on {src.name}: "
            f"{r.stderr.decode(errors='replace').strip()[-200:]}\n")
    log(f"labape: {vm} now runs on {dst.name}\n")


def run_job(db: Session, job: Job, run=virsh) -> int:
    """Job `environment.move`. params: group, to_host_id. Moves the group's VMs one by one, then
    names the group's new host and queues a deploy to reconcile OpenTofu and run Ansible."""
    from .runner import _append, beat
    p = job.params or {}
    env = db.get(Environment, job.environment_id)
    groups = (env.spec or {}).get("host_groups", []) if env else []
    g = next((x for x in groups if x["name"] == p.get("group")), None)
    dst = db.get(KvmHost, p.get("to_host_id"))
    if env is None or g is None or dst is None:
        _append(job, "labape: the environment, its group or the destination host is gone\n")
        return 1
    src = db.get(KvmHost, spread.group_host_id(env, g))

    def log(msg: str) -> None:
        _append(job, msg)
        beat(job.id)
    moved = []
    try:
        bridge = bridge_on(db, dst, g["network"])
        tag = f"labape-workspace={spread.workspace(env, dst.id)}"
        for vm in instance_names(g):
            if run(dst.libvirt_uri, "desc", vm).stdout.strip() == tag and \
                    run(dst.libvirt_uri, "domstate", vm).stdout.strip() == "running":
                log(f"labape: {vm} already runs on {dst.name}\n")       # a re-run after a partial move
                moved.append(vm)
                continue
            move_vm(src, dst, vm, bridge, tag, log, run)
            moved.append(vm)
    except (MoveError, subprocess.TimeoutExpired) as exc:
        log(f"labape: failed: {exc}\n")
        if moved:
            log(f"labape: moved so far: {', '.join(moved)}; the rest stay on {src.name}. Move the group again "
                "once the cause is fixed (moved VMs are skipped).\n")
        return 1
    new_groups = []
    for x in groups:
        if x["name"] == g["name"]:
            x = {k: v for k, v in x.items() if k != "kvm_host_id"}
            if dst.id != env.kvm_host_id:
                x["kvm_host_id"] = dst.id
        new_groups.append(x)
    env.spec = {**env.spec, "host_groups": new_groups}
    env.status = "deploying"
    deploy = Job(type="environment.deploy", params={"environment_id": env.id, "after_move": job.id},
                 kvm_host_id=env.kvm_host_id, environment_id=env.id, requested_by=job.requested_by)
    db.add(deploy)
    db.commit()
    log(f"labape: group {g['name']} is on {dst.name} now; deploy job {deploy.id} reconciles OpenTofu and runs Ansible\n")
    return 0
