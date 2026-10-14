"""VM snapshots (design §39, batch V1; review item 3): take, revert and delete, with libvirt.

Internal snapshots (`virsh snapshot-create-as`): stored inside the VM's own qcow2 disk, with the
memory of a running VM, so a revert returns it running exactly where it was. They need qcow2 disks
and BIOS firmware: libvirt can't take internal snapshots of a UEFI (pflash) VM, which is refused
with that reason. The disk file grows by what changes after a snapshot, which shows in the VM's
allocated disk and the host's free space.

libvirt is the truth for which snapshots exist; `vm_snapshots` keeps LABaPe's notes (who, why,
automatic). Settings → VM snapshots can take one of every existing VM before a deploy of an
environment that's already there (`auto-before-job-<id>`), keeping the newest few.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import Environment, KvmHost, Setting, VmSnapshot, utcnow
from ..naming import instance_names
from . import power

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
AUTO_PREFIX = "auto-before-job-"


class SnapshotError(RuntimeError):
    pass


def _virsh(run):
    return run or power.virsh


def _ok(r, what: str) -> str:
    if r.returncode != 0:
        raise SnapshotError(f"{what}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def env_vms(env: Environment) -> list[str]:
    return [n for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)]


def exists(uri: str, vm: str, run=None) -> bool:
    return _virsh(run)(uri, "domstate", vm).returncode == 0


def uefi(uri: str, vm: str, run=None) -> bool:
    xml = _ok(_virsh(run)(uri, "dumpxml", vm), f"reading {vm}")
    return "type='pflash'" in xml or 'type="pflash"' in xml


def listing(uri: str, vm: str, run=None) -> list[dict]:
    """The VM's snapshots from libvirt, oldest first: name, created, the state it was in, parent,
    and which one is current."""
    v = _virsh(run)
    names = [n.strip() for n in _ok(v(uri, "snapshot-list", vm, "--name", "--topological"),
                                    f"listing {vm}'s snapshots").splitlines() if n.strip()]
    cur = v(uri, "snapshot-current", vm, "--name")
    current = cur.stdout.strip() if cur.returncode == 0 else ""
    out = []
    for n in names:
        x = ET.fromstring(_ok(v(uri, "snapshot-dumpxml", vm, n), f"reading snapshot {n}"))
        t = x.findtext("creationTime")
        out.append({"name": n, "state": x.findtext("state") or "", "parent": x.findtext("parent/name") or "",
                    "created": dt.datetime.fromtimestamp(int(t), dt.timezone.utc).isoformat() if t else None,
                    "description": x.findtext("description") or "", "current": n == current})
    return out


def create(uri: str, vm: str, name: str, description: str = "", run=None) -> None:
    if not NAME.match(name):
        raise SnapshotError("a snapshot name is letters, digits, dots, dashes and underscores (up to 63)")
    if uefi(uri, vm, run):
        raise SnapshotError(f"{vm} boots with UEFI: libvirt can't take internal snapshots of it")
    args = ["snapshot-create-as", "--domain", vm, "--name", name, "--atomic"]
    if description:
        args += ["--description", description[:500]]
    _ok(_virsh(run)(uri, *args, timeout=1800), f"snapshot of {vm}")


def revert(uri: str, vm: str, name: str, run=None) -> None:
    _ok(_virsh(run)(uri, "snapshot-revert", "--domain", vm, "--snapshotname", name, timeout=1800),
        f"reverting {vm} to {name}")


def delete(uri: str, vm: str, name: str, run=None) -> None:
    _ok(_virsh(run)(uri, "snapshot-delete", "--domain", vm, "--snapshotname", name, timeout=1800),
        f"deleting {vm}'s snapshot {name}")


def _state_key(env_id: int, vm: str) -> str:
    return f"snapshots.vm.{env_id}.{vm}"


def read_state(db: Session, env_id: int, vm: str) -> dict:
    row = db.get(Setting, _state_key(env_id, vm))
    return dict(row.value) if row else {}


def sync(db: Session, env: Environment, host: KvmHost, vm: str, run=None) -> dict:
    """libvirt's list of the VM's snapshots into `vm_snapshots` (keeping LABaPe's notes), and whether
    the VM can have them at all, for the API, which can't reach libvirt (worker only)."""
    state = {"synced_at": utcnow().isoformat(), "supported": True, "reason": ""}
    rows = {s.name: s for s in db.scalars(select(VmSnapshot).where(VmSnapshot.environment_id == env.id,
                                                                    VmSnapshot.vm == vm))}
    if not exists(host.libvirt_uri, vm, run):
        state.update(supported=False, reason="the VM isn't on its KVM host")
        items = []
    elif uefi(host.libvirt_uri, vm, run):
        state.update(supported=False, reason="it boots with UEFI: libvirt can't take internal snapshots of it")
        items = []
    else:
        items = listing(host.libvirt_uri, vm, run)
    seen = set()
    for it in items:
        seen.add(it["name"])
        r = rows.get(it["name"])
        if r is None:                      # taken outside LABaPe (virsh): listed, without notes
            r = VmSnapshot(environment_id=env.id, vm=vm, name=it["name"], description=it["description"],
                           created_by="", auto=False)
            db.add(r)
        r.state, r.parent, r.current = it["state"], it["parent"], it["current"]
        if it["created"]:
            r.created_at = dt.datetime.fromisoformat(it["created"])
    for name, r in rows.items():
        if name not in seen:
            db.delete(r)
    key = _state_key(env.id, vm)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=state))
    else:
        row.value = state
    db.commit()
    return state


def run_job(db: Session, job, run=None) -> int:
    """The `vm.snapshot` job. params: action (create, revert, delete, sync), vm, name, description.
    Every action ends by reading the VM's snapshots back from libvirt (`sync`)."""
    from .runner import _append
    p = job.params or {}
    env = db.get(Environment, job.environment_id)
    host = db.get(KvmHost, job.kvm_host_id)
    if env is None or host is None:
        _append(job, "labape: the environment or its KVM host is gone\n")
        return 1
    action, vm, name = p.get("action"), p.get("vm"), p.get("name")
    _append(job, f"labape: {action} " + (f"snapshot {name} of " if name else "snapshots of ") + f"{vm} on {host.name}\n")
    try:
        if action == "create":
            create(host.libvirt_uri, vm, name, p.get("description", ""), run)
            db.add(VmSnapshot(environment_id=env.id, vm=vm, name=name, description=p.get("description", ""),
                              created_by=job.requested_by, created_at=utcnow(), auto=False))
        elif action == "revert":
            revert(host.libvirt_uri, vm, name, run)
        elif action == "delete":
            delete(host.libvirt_uri, vm, name, run)
        elif action != "sync":
            _append(job, f"labape: unknown action {action}\n")
            return 1
        db.commit()
    except SnapshotError as exc:
        _append(job, f"labape: failed: {exc}\n")
        try:
            sync(db, env, host, vm, run)
        except SnapshotError:
            pass
        return 1
    try:
        st = sync(db, env, host, vm, run)
        n = db.query(VmSnapshot).filter_by(environment_id=env.id, vm=vm).count()
        _append(job, f"labape: {vm} has {n} snapshot(s)" + ("" if st["supported"] else f" ({st['reason']})") + "\n")
    except SnapshotError as exc:
        _append(job, f"labape: couldn't read the snapshots back: {exc}\n")
    _append(job, "labape: done\n")
    return 0


def before_deploy(db: Session, job, env: Environment, host: KvmHost, log, run=None) -> None:
    """Settings → VM snapshots: a snapshot of every VM the environment already has, before a deploy
    changes it, then only the newest `keep_auto` automatic ones per VM. UEFI VMs are skipped (noted);
    any other failure stops the deploy (raises)."""
    cfg = appsettings.get(db, "vm_snapshots")
    if not cfg.auto_before_deploy:
        return
    name = f"{AUTO_PREFIX}{job.id}"
    for vm in env_vms(env):
        if not exists(host.libvirt_uri, vm, run):
            continue
        if uefi(host.libvirt_uri, vm, run):
            log(f"labape: {vm} boots with UEFI: no automatic snapshot (libvirt can't)\n")
            continue
        log(f"labape: snapshot {name} of {vm} before the deploy\n")
        create(host.libvirt_uri, vm, name, f"Automatic, before job {job.id}", run)
        db.add(VmSnapshot(environment_id=env.id, vm=vm, name=name, description=f"Automatic, before job {job.id}",
                          created_by="system", created_at=utcnow(), auto=True))
        db.commit()
        autos = list(db.scalars(select(VmSnapshot).where(VmSnapshot.environment_id == env.id, VmSnapshot.vm == vm,
                                                         VmSnapshot.auto.is_(True))
                                .order_by(VmSnapshot.created_at.desc(), VmSnapshot.id.desc())))
        for old in autos[cfg.keep_auto:]:
            try:
                delete(host.libvirt_uri, vm, old.name, run)
                log(f"labape: removed the older automatic snapshot {old.name} of {vm}\n")
            except SnapshotError as exc:
                log(f"labape: couldn't remove {old.name} of {vm}: {exc}\n")
                continue
            db.delete(old)
            db.commit()
        sync(db, env, host, vm, run)
