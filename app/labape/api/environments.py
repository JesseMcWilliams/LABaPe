"""Environments: create (deploys), list, view, redeploy, destroy, credentials.

Phase 10a takes the host groups directly (the tfvars-level form); the
environment-template editor replaces this in phase 10c.
"""
from __future__ import annotations

import datetime as dt
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..db import get_db
from ..catalog import CatalogError, expand, installer_mismatch, platforms
from ..errors import FieldError
from ..engine.hoststats import assess, demand_of
from ..models import Environment, Grant, HostStats, IpAllocation, Job, KvmHost, MetricSample, Template, utcnow
from ..naming import HOSTNAME_RE, instance_names
from ..netpolicy import PolicyError, allocate, resolve
from ..permissions import add_owner, can_see, is_owner, visible_ids
from ..security import Principal, audit, client_ip, current_principal, get_secret, require_roles

router = APIRouter(prefix="/api/environments", tags=["environments"])

ROLE_NAMES = Literal["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"]


class HostGroup(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9]{0,11}$")
    count: int = Field(default=1, ge=1, le=20)
    os: str = Field(pattern=r"^[a-z0-9_]+$")
    roles: list[ROLE_NAMES] = Field(min_length=1)
    image_source: Literal["iso_direct", "packer_template"] | None = None
    template: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+$")
    cpu_count: int = Field(default=2, ge=1, le=32)
    memory_mb: int = Field(default=4096, ge=512, le=262144)
    disk_gb: int = Field(default=40, ge=10, le=2048)
    windows_core: bool = False
    windows_datacenter: bool = False
    # Catalog network (design §20); None means the KVM host's default network.
    network: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,62}$")
    addressing: Literal["static", "dhcp"] = "static"
    # Optional VM names, one per host in order; blanks are generated
    # (<group><n>). Unique across the KVM host (question 60).
    names: list[str] = Field(default=[], max_length=20)
    # Catalog choices added on top of the template (design §21.4).
    services: list[str] = Field(default=[], max_length=50)
    applications: list[str] = Field(default=[], max_length=100)
    packages: list[str] = Field(default=[], max_length=50)
    # Start order (design §28.3, questions 109-110): lower starts first; None = the default
    # (domain controllers 1, everything else 2). The next order waits for this group's guest
    # agents ("agent") or not ("none"), then start_delay_seconds.
    start_order: int | None = Field(default=None, ge=1, le=99)
    start_wait: Literal["agent", "none"] = "agent"
    start_delay_seconds: int = Field(default=0, ge=0, le=3600)

    @field_validator("names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        for n in v:
            if n.strip() and not HOSTNAME_RE.match(n.strip()):
                raise ValueError(f"{n!r} isn't a valid host name")
        return [n.strip() for n in v]


class EnvironmentIn(BaseModel):
    # Doubles as the OpenTofu workspace and VM tag; "test-" names belong to the CLI's test mode.
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,39}$")
    kvm_host_id: int
    # Ignored since static addresses are allocated from the network
    # catalog (design §20.4); accepted so older clients still work.
    static_ip_offset_start: int | None = None
    host_groups: list[HostGroup] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def _not_test(cls, v: str) -> str:
        # pydantic's regex engine has no look-ahead, so this can't live in the pattern.
        if v.startswith("test-"):
            raise ValueError('names starting with "test-" are reserved for the CLI test mode')
        return v


def _out(db: Session, e: Environment, owner: bool) -> dict:
    addresses = {a.vm_name: a.address for a in db.scalars(select(IpAllocation)
                                                          .where(IpAllocation.environment_id == e.id))}
    return {
        "id": e.id, "name": e.name, "status": e.status, "kvm_host": e.kvm_host.name if e.kvm_host else None,
        "spec": e.spec, "created_by": e.created_by, "created_at": e.created_at, "updated_at": e.updated_at,
        "is_owner": owner, "inventory": e.inventory if owner else "", "addresses": addresses,
    }


def _check_networks(db: Session, p: Principal, kvm_host_id: int, groups: list[dict]) -> list[dict]:
    """Resolve each host group's network against the catalog and the host;
    returns the groups with the resolved network name filled in."""
    out = []
    for i, g in enumerate(groups):
        try:
            _, net = resolve(db, p, kvm_host_id, g.get("network"), g.get("addressing", "static"),
                             g.get("roles", []), g["name"])
        except PolicyError as exc:
            field = "addressing" if exc.field == "addressing" else "network"
            raise FieldError(f"host_groups.{i}.{field}", str(exc)) from exc
        out.append({**g, "network": net.name, "addressing": g.get("addressing", "static")})
    return out


def _check_names(db: Session, kvm_host_id: int, groups: list[dict], env_id: int | None = None) -> None:
    """VM names are host-wide (libvirt): refuse any name another environment
    on this KVM host already uses, or that repeats inside this one."""
    taken: dict[str, str] = {}
    others = db.scalars(select(Environment).where(Environment.kvm_host_id == kvm_host_id,
                                                  Environment.status != "destroyed"))
    for other in others:
        if other.id == env_id:
            continue
        for og in (other.spec or {}).get("host_groups", []):
            for n in instance_names(og):
                taken[n.lower()] = other.name
    seen: dict[str, int] = {}
    for i, g in enumerate(groups):
        for j, n in enumerate(instance_names(g)):
            field = f"host_groups.{i}.names.{j}" if j < len(g.get("names") or []) and g["names"][j] else \
                f"host_groups.{i}.name"
            if n.lower() in taken:
                raise FieldError(field, f"The VM name {n} is already used by environment {taken[n.lower()]} on "
                                        "this KVM host. VM names must be unique across the host; choose another "
                                        "name (or group name).")
            if n.lower() in seen:
                raise FieldError(field, f"The VM name {n} appears twice in this environment.")
            seen[n.lower()] = i


def _check_capacity(db: Session, kvm_host_id: int, groups: list[dict]) -> None:
    """Refuse an environment that would commit more than the host's RAM
    (question 57); the softer limits are warnings in the form. Skipped when
    the worker hasn't reported the host recently."""
    row = db.get(HostStats, kvm_host_id)
    if row is None or not row.stats or row.collected_at is None:
        return
    at = row.collected_at if row.collected_at.tzinfo else row.collected_at.replace(tzinfo=dt.timezone.utc)
    if utcnow() - at > dt.timedelta(minutes=10):
        return
    for f in assess(row.stats, demand_of(groups))["findings"]:
        if f["level"] == "block":
            raise FieldError("kvm_host_id", f["message"])


def _check_catalog(db: Session, groups: list[dict]) -> None:
    """Chosen services, applications and packages must be published and
    support the group's OS family."""
    for i, g in enumerate(groups):
        family = "windows" if g.get("os", "").startswith("windows") else "linux"
        try:
            chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []))
        except CatalogError as exc:
            raise FieldError(f"host_groups.{i}.{exc.field}", str(exc)) from exc
        for kind in ("services", "applications"):
            for name, definition in chosen[kind].items():
                if family not in platforms(kind[:-1], definition):
                    raise FieldError(f"host_groups.{i}.{kind}", f"{kind[:-1].capitalize()} {name} has nothing to "
                                     f"install on {family}; remove it from this {g.get('os')} group.")
                why = installer_mismatch(db, definition, g.get("os", "")) if kind == "applications" else None
                if why:
                    raise FieldError(f"host_groups.{i}.{kind}", f"Application {name} can't go on {g.get('os')}: "
                                     f"{why}. Remove it from this group, or widen the installer's targets.")


def _check_templates(db: Session, kvm_host_id: int, groups: list[dict]) -> None:
    """A template must exist on the host and match the group's OS (when the
    host's template library has been synced)."""
    known = {t.name: t for t in db.scalars(select(Template).where(Template.kvm_host_id == kvm_host_id,
                                                                  Template.present.is_(True)))}
    # The primary file host's templates too: the deploy copies them over (design §29.4).
    from ..engine.filedist import primary
    p = primary(db)
    if p is not None and p.id != kvm_host_id:
        for t in db.scalars(select(Template).where(Template.kvm_host_id == p.id, Template.present.is_(True))):
            known.setdefault(t.name, t)
    if not known:
        return
    for i, g in enumerate(groups):
        if g.get("image_source") != "packer_template":
            continue
        t = known.get(g.get("template") or "")
        if t is None:
            raise FieldError(f"host_groups.{i}.template", f"There's no template {g.get('template')!r} on this "
                                                          "KVM host. Choose one from the list.")
        if t.os_key and t.os_key != g.get("os"):
            raise FieldError(f"host_groups.{i}.template", f"Template {t.name} holds {t.os_key}, not {g.get('os')}. "
                                                          "Choose a template for this OS.")


def _allocate(db: Session, p: Principal, e: Environment) -> None:
    """Static addresses for every static VM (kept across redeploys)."""
    groups = _check_networks(db, p, e.kvm_host_id, e.spec.get("host_groups", []))
    for g in groups:
        if g["addressing"] != "static":
            continue
        hn, net = resolve(db, p, e.kvm_host_id, g["network"], "static", g.get("roles", []), g["name"])
        try:
            allocate(db, e.id, hn, net, instance_names(g))
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    e.spec = {**e.spec, "host_groups": groups}


def _queue(db: Session, e: Environment, job_type: str, p: Principal) -> Job:
    busy = db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already {busy.state} for this environment")
    if job_type == "environment.deploy":
        try:
            _allocate(db, p, e)
        except HTTPException:
            db.rollback()
            raise
    job = Job(type=job_type, params={"environment_id": e.id}, kvm_host_id=e.kvm_host_id, environment_id=e.id,
              requested_by=p.username)
    db.add(job)
    if job_type != "environment.refresh_addresses":
        e.status = "deploying" if job_type == "environment.deploy" else "destroying"
    db.commit()
    return job


@router.get("")
def list_envs(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ids = visible_ids(db, p, "environment")
    q = select(Environment).order_by(Environment.name)
    if ids is not None:
        q = q.where(Environment.id.in_(ids or {-1}))
    return [_out(db, e, is_owner(db, p, "environment", e.id)) for e in db.scalars(q)]


@router.post("", status_code=201)
def create_env(body: EnvironmentIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("deployer", "template_editor"))):
    host = db.get(KvmHost, body.kvm_host_id)
    if host is None or not host.enabled:
        raise FieldError("kvm_host_id", "Choose an enabled KVM host.")
    if db.scalar(select(Environment).where(Environment.name == body.name)):
        raise FieldError("name", f"An environment named {body.name} already exists. Choose another name.",
                         status_code=409)
    seen: dict[str, int] = {}
    for i, g in enumerate(body.host_groups):
        if g.name in seen:
            raise FieldError(f"host_groups.{i}.name", f"Group name {g.name} is also used by host group "
                                                      f"{seen[g.name] + 1}. Group names must be unique.")
        seen[g.name] = i
        if g.image_source == "packer_template" and not g.template:
            raise FieldError(f"host_groups.{i}.template", "Building from a template needs a template; choose one.")
        if len(g.names) > g.count:
            raise FieldError(f"host_groups.{i}.names", f"{len(g.names)} names were given for {g.count} hosts.")
    raw = [g.model_dump(exclude_none=True) for g in body.host_groups]
    _check_capacity(db, host.id, raw)
    _check_names(db, host.id, raw)
    _check_templates(db, host.id, raw)
    _check_catalog(db, raw)
    groups = _check_networks(db, p, host.id, raw)
    # The Linux admin account comes from Settings > VM accounts, fixed per environment.
    spec = {"host_groups": groups, "linux_admin_user": appsettings.get(db, "vm_accounts").linux_admin_user}
    e = Environment(name=body.name, kvm_host_id=host.id, spec=spec, created_by=p.username)
    db.add(e)
    db.commit()
    add_owner(db, "environment", e.id, p.username)
    try:
        job = _queue(db, e, "environment.deploy", p)
    except HTTPException:
        # e.g. the pool ran out: don't leave a half-created environment behind.
        for gr in db.scalars(select(Grant).where(Grant.object_type == "environment", Grant.object_id == e.id)):
            db.delete(gr)
        db.delete(e)
        db.commit()
        raise
    audit(db, p.username, "environment.create", object_type="environment", object_id=e.id,
          detail={"spec": e.spec, "job": job.id}, source_ip=client_ip(request))
    return {"environment": _out(db, e, True), "job_id": job.id}


def _get(db: Session, env_id: int, p: Principal) -> Environment:
    e = db.get(Environment, env_id)
    if e is None or not can_see(db, p, "environment", env_id):
        raise HTTPException(status_code=404, detail="No such environment")
    return e


@router.get("/{env_id}")
def get_env(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _out(db, e, is_owner(db, p, "environment", e.id))


@router.post("/{env_id}/deploy")
def redeploy(env_id: int, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can deploy it")
    job = _queue(db, e, "environment.deploy", p)
    audit(db, p.username, "environment.deploy", object_type="environment", object_id=e.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}


@router.post("/{env_id}/destroy")
def destroy(env_id: int, request: Request, db: Session = Depends(get_db),
            p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can destroy it")
    job = _queue(db, e, "environment.destroy", p)
    audit(db, p.username, "environment.destroy", object_type="environment", object_id=e.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}


@router.post("/{env_id}/refresh-addresses")
def refresh_addresses(env_id: int, request: Request, db: Session = Depends(get_db),
                      p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Re-find DHCP VMs' addresses and regenerate the inventory (design §20.5)."""
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can refresh it")
    if e.status != "deployed":
        raise HTTPException(status_code=409, detail="Only a deployed environment's addresses can be refreshed")
    job = _queue(db, e, "environment.refresh_addresses", p)
    audit(db, p.username, "environment.refresh_addresses", object_type="environment", object_id=e.id,
          detail={"job": job.id}, source_ip=client_ip(request))
    return {"job_id": job.id}


@router.get("/{env_id}/credentials")
def credentials(env_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Credentials are visible to owners and admins only")
    audit(db, p.username, "environment.credentials.view", object_type="environment", object_id=e.id,
          source_ip=client_ip(request))
    text = get_secret(db, f"environment/{e.id}/credentials") or ""
    return {"credentials": text, "sections": parse_credentials(text)}


def parse_credentials(text: str) -> list[dict]:
    """credentials.generated (scripts/generate-inventory.py) as sections of
    label/value items; values whose label mentions a password are secret,
    so the UI masks them. Other lines are notes."""
    sections: list[dict] = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "items": [], "notes": []}
            sections.append(current)
        elif current is None or not line.strip() or line.startswith("#"):
            continue
        elif ": " in line and len(line.split(": ", 1)[0]) <= 40:
            label, value = line.split(": ", 1)
            current["items"].append({"label": label.strip(), "value": value.strip(),
                                     "secret": "password" in label.lower()})
        else:
            current["notes"].append(line.strip())
    return sections


@router.post("/{env_id}/credentials/reveal")
def credential_revealed(env_id: int, body: dict, request: Request, db: Session = Depends(get_db),
                        p: Principal = Depends(current_principal)):
    """The UI reports each Show / Show large / Copy of a secret, for the audit log."""
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Credentials are visible to owners and admins only")
    action = body.get("action") if body.get("action") in ("show", "show_large", "copy") else "show"
    audit(db, p.username, f"environment.credentials.{action}", object_type="environment", object_id=e.id,
          detail={"section": str(body.get("section", ""))[:80], "label": str(body.get("label", ""))[:80]},
          source_ip=client_ip(request))
    return {"ok": True}


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
    row = db.scalar(select(HostStats).where(HostStats.kvm_host_id == e.kvm_host_id))
    stats = {v["name"]: v for v in ((row.stats if row else None) or {}).get("vms", [])}
    out = []
    for g in (e.spec or {}).get("host_groups", []):
        for n in instance_names(g):
            v = stats.get(n)
            out.append({"name": n, "group": g["name"], "os": g.get("os"),
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


def consoles(e: Environment, g: dict) -> dict:
    """Which consoles a VM offers (question 119): VNC for the ones with a desktop, the serial
    console for Linux; both go through the KVM host's LABaPe access account."""
    os_ = str(g.get("os", ""))
    # Windows always has a graphical console (Server Core's is sconfig); Linux only as a workstation.
    gui = os_.startswith("windows") or "linux_workstation" in (g.get("roles") or [])
    h = e.kvm_host
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
    row = db.scalar(select(HostStats).where(HostStats.kvm_host_id == e.kvm_host_id))
    v = next((x for x in ((row.stats if row else None) or {}).get("vms", []) if x["name"] == vm), None)
    owner = is_owner(db, p, "environment", e.id)
    host = _hosts(_inventory(db, e)).get(vm) or {} if owner else {}
    c = consoles(e, g)
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
        "series": vm_series(db, e.kvm_host_id, vm),
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
