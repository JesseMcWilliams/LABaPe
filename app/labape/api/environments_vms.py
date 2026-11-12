"""The environment's VMs, accounts, lease, snapshots, power and move routes (split out of environments.py; same prefix, included after it)."""
from __future__ import annotations

import datetime as dt
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import env_accounts, leases, spread
from ..db import get_db
from ..errors import FieldError
from ..models import Environment, HostStats, Job, KvmHost, MetricSample, utcnow
from ..naming import instance_names
from ..permissions import is_owner
from ..security import Principal, audit, client_ip, current_principal
from .environments import _check_capacity, _check_names, _check_networks, _check_templates, _get, _group_hosts

router = APIRouter(prefix="/api/environments", tags=["environments"])




# --- password rotation for the environment's accounts (design §24, R4) ------------------

class RotationIn(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(default=168, ge=24, le=24 * 365)


def _rotation_policies(db: Session, e: Environment) -> list:
    from ..engine.rotation import ENV_ACCOUNTS, ensure_env_policies, env_policy_name
    from ..models import SecretPolicy
    ensure_env_policies(db)
    names = [env_policy_name(e.id, k) for k in ENV_ACCOUNTS]
    return list(db.scalars(select(SecretPolicy).where(SecretPolicy.name.in_(names)).order_by(SecretPolicy.name)))


def _rotation_out(pol) -> dict:
    return {f: getattr(pol, f) for f in ("id", "kind", "label", "enabled", "interval_hours", "status",
                                         "last_rotated_at", "next_due_at", "last_error", "notes")}


def _owner_env(db: Session, env_id: int, p: Principal) -> Environment:
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners and admins manage its passwords")
    return e


@router.get("/{env_id}/rotation")
def get_rotation(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    e = _owner_env(db, env_id, p)
    return {"policies": [_rotation_out(x) for x in _rotation_policies(db, e)]}


@router.put("/{env_id}/rotation/{kind}")
def set_rotation(env_id: int, kind: str, body: RotationIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    e = _owner_env(db, env_id, p)
    pol = next((x for x in _rotation_policies(db, e) if x.kind == kind), None)
    if pol is None:
        raise FieldError("kind", "This environment has no such account to rotate.", status_code=404)
    pol.enabled, pol.interval_hours = body.enabled, body.interval_hours
    if body.enabled and pol.last_rotated_at:
        pol.next_due_at = pol.last_rotated_at + dt.timedelta(hours=body.interval_hours)
    db.commit()
    audit(db, p.username, "secret.policy", object_type="secret_policy", object_id=pol.id,
          detail={"name": pol.name, **body.model_dump()}, source_ip=client_ip(request))
    return _rotation_out(pol)


@router.post("/{env_id}/rotation/{kind}/rotate")
def rotate_env_now(env_id: int, kind: str, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(current_principal)):
    e = _owner_env(db, env_id, p)
    if e.status != "deployed":
        raise FieldError("environment", "Deploy the environment first.", status_code=409)
    pol = next((x for x in _rotation_policies(db, e) if x.kind == kind), None)
    if pol is None:
        raise FieldError("kind", "This environment has no such account to rotate.", status_code=404)
    busy = db.scalar(select(Job).where(Job.type == "secret.rotate", Job.state.in_(("queued", "running"))))
    if busy and (busy.params or {}).get("policy_id") == pol.id:
        raise FieldError("kind", f"Job {busy.id} is already rotating it.", status_code=409)
    job = Job(type="secret.rotate", params={"policy_id": pol.id}, requested_by=p.username, environment_id=e.id)
    db.add(job)
    db.commit()
    audit(db, p.username, "secret.rotate.start", object_type="secret_policy", object_id=pol.id,
          detail={"name": pol.name, "job": job.id}, source_ip=client_ip(request))
    return {"job_id": job.id}


# --- VM power and start order (design §28.3, questions 108-111) --------------------------------

class PowerIn(BaseModel):
    action: Literal["start", "shutdown", "restart", "force_off"]
    vms: list[str] | None = Field(default=None, max_length=200)      # None: the whole environment
    force_after_timeout: bool = False                                  # shutdown/restart only (question 108)
    timeout_seconds: int = Field(default=300, ge=30, le=3600)


def _owner_or_403(db: Session, p: Principal, e: Environment, what: str) -> None:
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail=f"Only the environment's owners and admins can {what}")


@router.get("/{env_id}/vms")
def env_vms(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """Each VM's power state (from the host's latest stats, refreshed every few minutes), its
    group and start order."""
    from ..engine.power import default_order
    e = _get(db, env_id, p)
    rows = {r.kvm_host_id: r for r in db.scalars(select(HostStats).where(
        HostStats.kvm_host_id.in_(spread.host_ids(e) + [e.kvm_host_id])))}
    row = rows.get(e.kvm_host_id)
    names = {h.id: h.name for h in db.scalars(select(KvmHost).where(KvmHost.id.in_(spread.host_ids(e))))}
    out = []
    for g in (e.spec or {}).get("host_groups", []):
        hrow = rows.get(spread.group_host_id(e, g))          # each group's own host (design §45)
        stats = {v["name"]: v for v in ((hrow.stats if hrow else None) or {}).get("vms", [])}
        for n in instance_names(g):
            v = stats.get(n)
            out.append({"name": n, "group": g["name"], "os": g.get("os"),
                        "kvm_host_id": spread.group_host_id(e, g), "kvm_host": names.get(spread.group_host_id(e, g), ""),
                        "start_order": g.get("start_order") or default_order(g),
                        "state": v["state"] if v else "missing", "online": bool(v and v["online"]),
                        "responding": v.get("responding") if v else None})
    return {"collected_at": row.collected_at if row else None, "vms": out,
            "can_operate": is_owner(db, p, "environment", e.id) and e.status == "deployed"}


@router.post("/{env_id}/power")
def power(env_id: int, body: PowerIn, request: Request, db: Session = Depends(get_db),
          p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "start, stop or restart its VMs")
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="Only a deployed environment's VMs can be started or stopped")
    names = [n for g in (e.spec or {}).get("host_groups", []) for n in instance_names(g)]
    if body.vms is not None:
        unknown = [n for n in body.vms if n not in names]
        if unknown or not body.vms:
            raise FieldError("vms", f"Not VMs of this environment: {', '.join(unknown)}." if unknown
                             else "Choose at least one VM.")
    busy = db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    params = {"environment_id": e.id, **body.model_dump()}
    job = Job(type="environment.power", params=params, kvm_host_id=e.kvm_host_id, environment_id=e.id,
              requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, f"environment.power.{body.action}", object_type="environment", object_id=e.id,
          detail={"job": job.id, "vms": body.vms or "all", "force_after_timeout": body.force_after_timeout},
          source_ip=client_ip(request))
    return {"job_id": job.id}


# --- one VM's details, accounts and groups (design §28.6, questions 119-120) -------------------

def _vm_group(e: Environment, vm: str) -> dict:
    for g in (e.spec or {}).get("host_groups", []):
        if vm in instance_names(g):
            return g
    raise HTTPException(status_code=404, detail=f"{vm} isn't a VM of this environment")


def vm_series(db: Session, kvm_host_id: int, vm: str, hours: int = 24) -> dict:
    rows = db.scalars(select(MetricSample).where(MetricSample.kvm_host_id == kvm_host_id, MetricSample.vm == vm,
                                                 MetricSample.at >= utcnow() - dt.timedelta(hours=hours))
                      .order_by(MetricSample.at))
    keys = ("cpu_pct", "memory_used_mb", "disk_read_bps", "disk_write_bps", "disk_free_gb")
    out: dict = {"t": [], **{k: [] for k in keys}}
    for r in rows:
        out["t"].append(r.at.isoformat())
        for k in keys:
            out[k].append((r.data or {}).get(k))
    return out


def consoles(e: Environment, g: dict, h: KvmHost | None = None) -> dict:
    """Which consoles a VM offers (question 119): VNC for the ones with a desktop, the serial
    console for Linux; both go through the KVM host's LABaPe access account."""
    os_ = str(g.get("os", ""))
    # Windows always has a graphical console (Server Core's is sconfig); Linux only as a workstation.
    gui = os_.startswith("windows") or "linux_workstation" in (g.get("roles") or [])
    h = h or e.kvm_host
    ready = bool(h and h.access_status == "ready")
    return {"vnc": gui, "serial": not os_.startswith("windows"), "host_ready": ready,
            "host_id": h.id if h else None,
            "virt_viewer": f"virt-viewer --connect qemu+ssh://{h.ssh_user}@{h.ssh_address}/system" if ready else ""}


@router.get("/{env_id}/vms/{vm}")
def vm_details(env_id: int, vm: str, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """What the guest agent reports, the VM's state and size, 24 hours of load, and its
    connection options."""
    from ..engine.power import default_order
    from ..engine.vmaccess import _hosts, _inventory
    e = _get(db, env_id, p)
    g = _vm_group(e, vm)
    vh = db.get(KvmHost, spread.vm_host_id(e, vm))            # design §45
    row = db.scalar(select(HostStats).where(HostStats.kvm_host_id == vh.id))
    v = next((x for x in ((row.stats if row else None) or {}).get("vms", []) if x["name"] == vm), None)
    owner = is_owner(db, p, "environment", e.id)
    host = _hosts(_inventory(db, e)).get(vm) or {} if owner else {}
    c = consoles(e, g, vh)
    if c["virt_viewer"]:
        c["virt_viewer"] += f" {vm}"
    return {
        "name": vm, "environment": e.name, "environment_id": e.id, "group": g["name"], "os": g.get("os"),
        "roles": g.get("roles") or [], "start_order": g.get("start_order") or default_order(g),
        "address": str(host.get("ansible_host") or ""), "user": str(host.get("ansible_user") or "") if owner else "",
        "collected_at": row.collected_at if row else None,
        "state": v["state"] if v else "missing", "online": bool(v and v["online"]),
        "responding": v.get("responding") if v else None,
        "size": {k: v.get(k) for k in ("vcpus", "memory_mb", "memory_used_mb", "cpu_pct", "disk_capacity_gb",
                                       "disk_allocated_gb", "disk_read_bps", "disk_write_bps")} if v else {},
        "guest": (v or {}).get("guest_info") or {}, "guest_disk": (v or {}).get("guest_disk") or {},
        "series": vm_series(db, vh.id, vm), "kvm_host": vh.name,
        "can_operate": owner and e.status == "deployed", "consoles": c,
    }


@router.get("/{env_id}/vms/{vm}/accounts")
def vm_accounts(env_id: int, vm: str, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """Local accounts and groups, read live (owners and admins, question 120)."""
    from ..engine import vmadmin
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "see its VMs' accounts")
    _vm_group(e, vm)
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="The environment isn't deployed")
    try:
        return vmadmin.read_accounts(db, e, vm)
    except (vmadmin.VmAdminError, subprocess.TimeoutExpired, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't read {vm}'s accounts: {exc}") from exc


class VmManageIn(BaseModel):
    op: str = Field(pattern=r"^(user\.(add|delete|lock|unlock|groups|password)|group\.(add|delete|member_add|member_remove))$")
    name: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9 ._-]{0,63}$")
    comment: str = Field(default="", max_length=120, pattern=r"^[^:\n]*$")
    groups: list[str] = Field(default=[], max_length=50)
    member: str = Field(default="", max_length=64)
    ssh_key: str = Field(default="", max_length=2000)
    password: str = Field(default="", max_length=512, repr=False)


@router.post("/{env_id}/vms/{vm}/accounts")
def vm_manage_accounts(env_id: int, vm: str, body: VmManageIn, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(current_principal)):
    """Change one local account or group on the VM (owners and admins, question 120)."""
    from ..engine import vmadmin
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "change its VMs' accounts")
    _vm_group(e, vm)
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="The environment isn't deployed")
    detail = {"vm": vm, "name": body.name, "groups": body.groups, "member": body.member,
              "ssh_key_set": bool(body.ssh_key), "password_set": bool(body.password)}
    try:
        vmadmin.manage(db, e, vm, body.op, body.name, comment=body.comment, groups=body.groups, member=body.member,
                       ssh_key=body.ssh_key, password=body.password)
    except (vmadmin.VmAdminError, subprocess.TimeoutExpired, RuntimeError) as exc:
        msg = str(exc).replace(body.password, "***") if body.password else str(exc)
        audit(db, p.username, f"vm.{body.op}", object_type="environment", object_id=e.id, outcome="failed",
              detail={**detail, "error": msg[:300]}, source_ip=client_ip(request))
        raise FieldError("name", msg) from exc
    audit(db, p.username, f"vm.{body.op}", object_type="environment", object_id=e.id, detail=detail,
          source_ip=client_ip(request))
    return {"ok": True}


# --- the environment's own accounts (design §42) ---------------------------------------------------

class AccountIn(BaseModel):
    name: str = Field(max_length=20)
    kind: Literal["domain", "local"]
    hosts: list[str] = Field(default_factory=list, max_length=5)
    groups: list[str] = Field(default_factory=list, max_length=20)
    display_name: str = Field(default="", max_length=64)
    ou: str = Field(default="", max_length=255)
    sudo_nopasswd: bool = False            # local accounts on Linux hosts (2026-10-20 round)
    vms: list[str] = Field(default_factory=list, max_length=100)   # D186: only these VMs of the roles; none = all of them


class AccountsIn(BaseModel):
    accounts: list[AccountIn] = Field(default_factory=list, max_length=50)


@router.get("/{env_id}/accounts")
def env_account_list(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    role_vms: dict[str, list[str]] = {}
    for g in (e.spec or {}).get("host_groups", []):
        for r in g.get("roles") or []:
            role_vms.setdefault(r, []).extend(instance_names(g))
    return {"accounts": env_accounts.accounts(e), "roles": sorted(env_accounts.env_roles(e) & set(env_accounts.ROLES)),
            "role_vms": role_vms, "can_edit": is_owner(db, p, "environment", e.id)}


@router.put("/{env_id}/accounts")
def env_account_save(env_id: int, body: AccountsIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(current_principal)):
    """Replace the list (owners): applied by the next deploy, which creates them on the VMs."""
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "change its accounts")
    try:
        items = env_accounts.set_accounts(db, e, [a.model_dump() for a in body.accounts])
    except env_accounts.AccountError as exc:
        raise FieldError("accounts", str(exc)) from exc
    audit(db, p.username, "environment.accounts", object_type="environment", object_id=e.id,
          detail={"accounts": [{k: v for k, v in a.items() if k != "groups"} for a in items]},
          source_ip=client_ip(request))
    return {"accounts": items}


# --- the lease (design §40) -------------------------------------------------------------------------

class LeaseIn(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    until: dt.datetime | None = None                     # admins
    clear: bool = False                                  # admins: no expiry


@router.post("/{env_id}/lease")
def set_lease(env_id: int, body: LeaseIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(current_principal)):
    """Extend (owners) or set or remove (admins) the environment's lease."""
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "extend its lease")
    if e.status in ("destroying", "destroyed"):
        raise HTTPException(status_code=409, detail="The environment is being destroyed or gone")
    before = e.expires_at
    try:
        leases.set_lease(db, e, days=body.days, until=body.until, clear=body.clear, admin=p.is_admin)
    except leases.LeaseError as exc:
        raise FieldError("days", str(exc)) from exc
    audit(db, p.username, "environment.lease", object_type="environment", object_id=e.id,
          detail={"from": before.isoformat() if before else None,
                  "to": e.expires_at.isoformat() if e.expires_at else None}, source_ip=client_ip(request))
    return leases.out(db, e)


# --- VM snapshots (design §39) --------------------------------------------------------------------

class SnapshotIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
    description: str = Field(default="", max_length=500)


@router.get("/{env_id}/vms/{vm}/snapshots")
def vm_snapshots(env_id: int, vm: str, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """The VM's snapshots as the worker last read them from libvirt (after each snapshot job, or a
    Refresh), with LABaPe's notes. `synced_at` is null until the first read."""
    from ..engine import snapshots
    from ..models import VmSnapshot
    e = _get(db, env_id, p)
    _vm_group(e, vm)
    st = snapshots.read_state(db, e.id, vm)
    rows = db.scalars(select(VmSnapshot).where(VmSnapshot.environment_id == e.id, VmSnapshot.vm == vm)
                      .order_by(VmSnapshot.created_at))
    items = [{"name": s.name, "created": s.created_at, "state": s.state, "parent": s.parent, "current": s.current,
              "description": s.description, "created_by": s.created_by, "auto": s.auto} for s in rows]
    return {"snapshots": items, "synced_at": st.get("synced_at"), "supported": st.get("supported", True),
            "reason": st.get("reason", ""),
            "can_operate": is_owner(db, p, "environment", e.id) and e.status == "deployed"}


def _snapshot_job(db: Session, p: Principal, e: Environment, vm: str, params: dict, request: Request) -> dict:
    _owner_or_403(db, p, e, "take, revert or delete its VMs' snapshots")
    _vm_group(e, vm)
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="Only a deployed environment's VMs have snapshots")
    busy = db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    job = Job(type="vm.snapshot", params={"vm": vm, **params}, kvm_host_id=spread.vm_host_id(e, vm), environment_id=e.id,
              requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, f"vm.snapshot.{params['action']}", object_type="environment", object_id=e.id,
          detail={"job": job.id, "vm": vm, "name": params["name"]}, source_ip=client_ip(request))
    return {"job_id": job.id}


@router.post("/{env_id}/vms/{vm}/snapshots")
def vm_snapshot_create(env_id: int, vm: str, body: SnapshotIn, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _snapshot_job(db, p, e, vm, {"action": "create", "name": body.name, "description": body.description},
                         request)


@router.post("/{env_id}/vms/{vm}/snapshots/refresh")
def vm_snapshot_refresh(env_id: int, vm: str, request: Request, db: Session = Depends(get_db),
                        p: Principal = Depends(current_principal)):
    """Read the VM's snapshots from libvirt again (a quick job)."""
    e = _get(db, env_id, p)
    return _snapshot_job(db, p, e, vm, {"action": "sync", "name": ""}, request)


@router.post("/{env_id}/vms/{vm}/snapshots/{name}/revert")
def vm_snapshot_revert(env_id: int, vm: str, name: str, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _snapshot_job(db, p, e, vm, {"action": "revert", "name": name}, request)


@router.delete("/{env_id}/vms/{vm}/snapshots/{name}")
def vm_snapshot_delete(env_id: int, vm: str, name: str, request: Request, db: Session = Depends(get_db),
                       p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _snapshot_job(db, p, e, vm, {"action": "delete", "name": name}, request)


class StartOrderGroup(BaseModel):
    name: str
    start_order: int = Field(ge=1, le=99)
    start_wait: Literal["agent", "none"] = "agent"
    start_delay_seconds: int = Field(default=0, ge=0, le=3600)


class StartOrderIn(BaseModel):
    groups: list[StartOrderGroup] = Field(min_length=1, max_length=20)


@router.put("/{env_id}/start-order")
def set_start_order(env_id: int, body: StartOrderIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(current_principal)):
    """Change the host groups' start order, wait rule and delay (no redeploy needed)."""
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "change its start order")
    spec = dict(e.spec or {})
    groups = [dict(g) for g in spec.get("host_groups", [])]
    by_name = {g["name"]: g for g in groups}
    for i, item in enumerate(body.groups):
        g = by_name.get(item.name)
        if g is None:
            raise FieldError(f"groups.{i}.name", f"There's no host group {item.name} in this environment.")
        g.update(start_order=item.start_order, start_wait=item.start_wait,
                 start_delay_seconds=item.start_delay_seconds)
    spec["host_groups"] = groups
    e.spec = spec
    db.commit()
    audit(db, p.username, "environment.start_order", object_type="environment", object_id=e.id,
          detail={"groups": [g.model_dump() for g in body.groups]}, source_ip=client_ip(request))
    return {"host_groups": [{k: g.get(k) for k in ("name", "start_order", "start_wait", "start_delay_seconds")}
                            for g in groups]}


class DesktopGroup(BaseModel):
    name: str
    remote_desktop: list[Literal["xrdp", "vnc"]] = Field(default=[], max_length=2)


class DesktopsIn(BaseModel):
    groups: list[DesktopGroup] = Field(min_length=1, max_length=20)


@router.put("/{env_id}/desktops")
def set_desktops(env_id: int, body: DesktopsIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    """E3 (question 168): xrdp and/or VNC on Linux host groups; the next Redeploy sets them up."""
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "change its desktop sessions")
    spec = dict(e.spec or {})
    groups = [dict(g) for g in spec.get("host_groups", [])]
    by_name = {g["name"]: g for g in groups}
    for i, item in enumerate(body.groups):
        g = by_name.get(item.name)
        if g is None:
            raise FieldError(f"groups.{i}.name", f"There's no host group {item.name} in this environment.")
        if str(g.get("os", "")).startswith("windows") and item.remote_desktop:
            raise FieldError(f"groups.{i}.remote_desktop", f"{item.name} is Windows: it has RDP already.")
        g["remote_desktop"] = list(dict.fromkeys(item.remote_desktop))
    spec["host_groups"] = groups
    e.spec = spec
    db.commit()
    audit(db, p.username, "environment.desktops", object_type="environment", object_id=e.id,
          detail={"groups": [g.model_dump() for g in body.groups]}, source_ip=client_ip(request))
    return {"host_groups": [{"name": g["name"], "remote_desktop": g.get("remote_desktop", [])} for g in groups]}


class MoveIn(BaseModel):
    group: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,11}$")
    kvm_host_id: int


@router.post("/{env_id}/move")
def move_group(env_id: int, body: MoveIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(current_principal)):
    """Move a host group's running VMs to another KVM host (design §46, P2): live migration with
    the disks copied, then a deploy reconciles. The checks a new group on that host gets apply."""
    e = _get(db, env_id, p)
    _owner_or_403(db, p, e, "move its VMs")
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="Only a deployed environment's VMs can move")
    g = next((x for x in (e.spec or {}).get("host_groups", []) if x["name"] == body.group), None)
    if g is None:
        raise FieldError("group", f"{e.name} has no host group {body.group}.")
    if spread.group_host_id(e, g) == body.kvm_host_id:
        raise FieldError("kvm_host_id", f"Group {body.group} is on that host already.")
    _group_hosts(db, e.kvm_host_id, [{"kvm_host_id": body.kvm_host_id}])       # enabled, not stack-only
    moved = {**{k: v for k, v in g.items() if k != "kvm_host_id"}, "kvm_host_id": body.kvm_host_id}
    try:
        _check_networks(db, p, e.kvm_host_id, [moved])
    except FieldError as exc:
        raise FieldError("kvm_host_id", str(exc)) from exc
    _check_names(db, e.kvm_host_id, [moved], env_id=e.id)
    _check_capacity(db, body.kvm_host_id, [moved])
    if (g.get("image_source") or "iso_direct") == "packer_template":
        _check_templates(db, e.kvm_host_id, [moved])
    busy = db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    job = Job(type="environment.move", params={"group": body.group, "to_host_id": body.kvm_host_id},
              kvm_host_id=body.kvm_host_id, environment_id=e.id, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "environment.move", object_type="environment", object_id=e.id,
          detail={"job": job.id, "group": body.group, "to_host_id": body.kvm_host_id}, source_ip=client_ip(request))
    return {"job_id": job.id}
