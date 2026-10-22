"""Environments: create (deploys), list, view, redeploy, destroy, credentials.

Phase 10a takes the host groups directly (the tfvars-level form); the
environment-template editor replaces this in phase 10c.
"""
from __future__ import annotations

import datetime as dt
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import appsettings, env_accounts, leases, ostargets, quotas, remote, spread, timezones
from ..db import get_db
from ..catalog import CatalogError, expand, installer_mismatch, package_platforms, platforms
from ..errors import FieldError
from ..engine import planpreview
from ..engine.hoststats import assess, demand_of
from ..models import CatalogItem, Environment, Grant, HostStats, IpAllocation, Job, KvmHost, MetricSample, Template, utcnow
from ..naming import HOSTNAME_RE, instance_names
from ..netpolicy import PolicyError, allocate, resolve
from ..permissions import add_owner, can_see, is_owner, visible_ids
from ..security import Principal, audit, client_ip, current_principal, get_secret, require_roles

router = APIRouter(prefix="/api/environments", tags=["environments"])

ROLE_NAMES = Literal["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"]


class HostGroup(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,11}$")      # mixed case is fine; VM names are compared without case
    count: int = Field(default=1, ge=1, le=20)
    os: str = Field(pattern=r"^[a-z0-9_]+$")
    roles: list[ROLE_NAMES] = Field(min_length=1, max_length=1)      # one role per host group
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
    # Design §45 (P1b): this group on another KVM host; None = the environment's own host. The hosts
    # must carry the group's network (bridged to the same lab segment).
    kvm_host_id: int | None = None
    # E3 (question 168): desktop sessions on a Linux group's VMs: xrdp (RDP for every account) and/or
    # VNC (a display per local account).
    remote_desktop: list[Literal["xrdp", "vnc"]] = Field(default=[], max_length=2)
    # D184: a member host joins the environment's domain (when it has a domain controller); a standalone one
    # does not. Domain controllers are always part of their domain.
    domain_member: bool = True

    @model_validator(mode="after")
    def _standalone_dc(self) -> "HostGroup":
        if not self.domain_member and "domain_controller" in self.roles:
            raise ValueError("a domain controller can't be standalone")
        return self

    @model_validator(mode="after")
    def _disk_floor(self) -> "HostGroup":
        # Refused here, at creation, rather than by OpenTofu's precondition when the deploy plans.
        floor = ostargets.MIN_DISK_GB.get(self.os, 0)
        if self.disk_gb < floor:
            raise ValueError(f"disk_gb {self.disk_gb} is below the {floor} GB {ostargets.OSES[self.os][2]} needs")
        return self

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
    kvm_host_id: int | None = None           # None: the best fit (design §43)
    # Ignored since static addresses are allocated from the network
    # catalog (design §20.4); accepted so older clients still work.
    static_ip_offset_start: int | None = None
    # An IANA zone as the browser reports it (design §52); None: UTC.
    timezone: str | None = None
    host_groups: list[HostGroup] = Field(min_length=1, max_length=20)
    # D185: keep it as a draft (status "new") instead of deploying: no quota, no addresses, no job.
    draft: bool = False

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str | None) -> str | None:
        if v and not timezones.is_valid(v):
            raise ValueError("That time zone isn't one LABaPe offers; pick one from the list.")
        return v or None

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
        "lease": leases.out(db, e),
    }


def _check_networks(db: Session, p: Principal, kvm_host_id: int, groups: list[dict]) -> list[dict]:
    """Resolve each host group's network against the catalog and the host;
    returns the groups with the resolved network name filled in."""
    out = []
    for i, g in enumerate(groups):
        try:
            _, net = resolve(db, p, g.get("kvm_host_id") or kvm_host_id, g.get("network"),
                             g.get("addressing", "static"), g.get("roles", []), g["name"])
        except PolicyError as exc:
            field = "addressing" if exc.field == "addressing" else "network"
            raise FieldError(f"host_groups.{i}.{field}", str(exc)) from exc
        out.append({**g, "network": net.name, "addressing": g.get("addressing", "static")})
    return out


def _check_names(db: Session, kvm_host_id: int, groups: list[dict], env_id: int | None = None) -> None:
    """VM names are host-wide (libvirt): refuse any name another environment
    on this KVM host already uses, or that repeats inside this one."""
    taken: dict[tuple[int, str], str] = {}       # (KVM host, VM name) -> environment (design §45)
    for other in db.scalars(select(Environment).where(Environment.status.notin_(("destroyed", "new")))):
        if other.id == env_id:
            continue
        for n, hid in spread.vm_host_ids(other).items():
            taken[(hid, n.lower())] = other.name
    seen: dict[str, int] = {}
    for i, g in enumerate(groups):
        hid = g.get("kvm_host_id") or kvm_host_id
        for j, n in enumerate(instance_names(g)):
            field = f"host_groups.{i}.names.{j}" if j < len(g.get("names") or []) and g["names"][j] else \
                f"host_groups.{i}.name"
            if (hid, n.lower()) in taken:
                raise FieldError(field, f"The VM name {n} is already used by environment {taken[(hid, n.lower())]} on "
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


def _group_hosts(db: Session, home: int, groups: list[dict]) -> dict[int, list[dict]]:
    """Design §45: each group's KVM host (enabled, not stack-only); a group on the environment's own
    host drops the field, so a one-host environment's spec stays as before. Returns host -> groups."""
    parts: dict[int, list[dict]] = {}
    for i, g in enumerate(groups):
        hid = g.get("kvm_host_id")
        if hid is not None and hid != home:
            h = db.get(KvmHost, hid)
            if h is None or not h.enabled or h.role == "stack":
                raise FieldError(f"host_groups.{i}.kvm_host_id", "Choose an enabled KVM host for this group.")
        else:
            g.pop("kvm_host_id", None)
            hid = home
        parts.setdefault(hid, []).append(g)
    return parts


def rank_hosts(db: Session, p: Principal, groups: list[dict]) -> list[dict]:
    """Best-fit placement (design §43; §15's "kvm_host: auto" for a whole environment): every enabled
    KVM host that passes the same checks as a chosen one (networks, templates, capacity), best first:
    least memory committed with this environment added, then vCPUs per CPU, then running jobs, and
    hosts that already hold the templates. Hosts that fail say why."""
    from ..engine.filedist import primary
    free = [g for g in groups if not g.get("kvm_host_id")]           # pinned groups stay put (design §45)
    groups = free or [{k: v for k, v in g.items() if k != "kvm_host_id"} for g in groups]
    demand = demand_of(groups)
    busy = dict(db.execute(select(Job.kvm_host_id, func.count()).where(Job.state.in_(("queued", "running")))
                           .group_by(Job.kvm_host_id)).all())
    prim = primary(db)
    wanted = {g.get("template") for g in groups if g.get("image_source") == "packer_template" and g.get("template")}
    out = []
    for h in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True)).order_by(KvmHost.name)):
        if h.role == "stack":
            continue
        item = {"id": h.id, "name": h.name, "ok": False, "score": None, "reasons": []}
        try:
            _check_networks(db, p, h.id, groups)
            _check_templates(db, h.id, groups)
            _check_capacity(db, h.id, groups)
        except (FieldError, HTTPException) as exc:
            item["reasons"].append(getattr(exc, "detail", None) if isinstance(exc, HTTPException) else str(exc))
            out.append(item)
            continue
        row = db.get(HostStats, h.id)
        fresh = bool(row and row.stats and row.collected_at and utcnow() - (
            row.collected_at if row.collected_at.tzinfo else row.collected_at.replace(tzinfo=dt.timezone.utc))
            < dt.timedelta(minutes=10))
        here = set(db.scalars(select(Template.name).where(Template.kvm_host_id == h.id, Template.present.is_(True))))
        missing = sorted(t for t in wanted if t not in here)
        score = 2.0 if not fresh else 0.0
        if fresh:
            a = assess(row.stats, demand)
            score += a["memory_committed_ratio"] + a["vcpu_ratio"] / 16
            item["reasons"] += [f["message"] for f in a["findings"]]
            item["reasons"].insert(0, f"{a['memory_committed_ratio']:.0%} of memory committed with it, "
                                      f"{a['vcpu_ratio']:.1f} vCPUs per CPU")
        else:
            item["reasons"].append("no recent load reading")
        if busy.get(h.id):
            score += 0.15 * busy[h.id]
            item["reasons"].append(f"{busy[h.id]} job(s) queued or running")
        if missing:
            score += 0.1 * len(missing)
            src = f" from {prim.name}" if prim is not None and prim.id != h.id else ""
            item["reasons"].append(f"copies template(s) {', '.join(missing)}{src} first")
        item.update(ok=True, score=round(score, 3))
        out.append(item)
    return sorted(out, key=lambda x: (not x["ok"], x["score"] if x["score"] is not None else 99, x["name"]))


class PlacementIn(BaseModel):
    host_groups: list[HostGroup] = Field(min_length=1, max_length=20)


@router.post("/placement")
def placement(body: PlacementIn, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Where an environment like this would go: the hosts, best first, and why (nothing is created)."""
    return {"hosts": rank_hosts(db, p, [g.model_dump(exclude_none=True) for g in body.host_groups])}


def _check_catalog(db: Session, groups: list[dict]) -> None:
    """Chosen services, applications and packages must be published and
    support the group's OS family."""
    for i, g in enumerate(groups):
        family = "windows" if g.get("os", "").startswith("windows") else "linux"
        try:
            chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []), family)
        except CatalogError as exc:
            raise FieldError(f"host_groups.{i}.{exc.field}", str(exc)) from exc
        for pkg in g.get("packages", []):
            item = db.scalar(select(CatalogItem).where(CatalogItem.kind == "package", CatalogItem.name == pkg))
            if item is not None and family not in package_platforms(db, item.definition):
                raise FieldError(f"host_groups.{i}.packages", f"Package {pkg} has nothing to install on {family}; "
                                 f"remove it from this {g.get('os')} group.")
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
    from ..engine.filedist import primary
    p = primary(db)
    libraries: dict[int, dict] = {}

    def library(hid: int) -> dict:
        if hid not in libraries:
            known = {t.name: t for t in db.scalars(select(Template).where(Template.kvm_host_id == hid,
                                                                          Template.present.is_(True)))}
            # The primary file host's templates too: the deploy copies them over (design §29.4).
            if p is not None and p.id != hid:
                for t in db.scalars(select(Template).where(Template.kvm_host_id == p.id, Template.present.is_(True))):
                    known.setdefault(t.name, t)
            libraries[hid] = known
        return libraries[hid]
    for i, g in enumerate(groups):
        if g.get("image_source") != "packer_template":
            continue
        known = library(g.get("kvm_host_id") or kvm_host_id)
        if not known:
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
        hn, net = resolve(db, p, spread.group_host_id(e, g), g["network"], "static", g.get("roles", []), g["name"])
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
    if job_type not in ("environment.refresh_addresses", planpreview.JOB_TYPE):    # read-only jobs
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


def _prepare(db: Session, p: Principal, body: EnvironmentIn, env_id: int | None = None) -> tuple[KvmHost, list[dict]]:
    """Check a new (or edited draft's) environment and return its KVM host and groups. A draft gets the checks
    that need no capacity: its name, groups, catalog and networks; the rest waits for the deploy."""
    if body.kvm_host_id is None and body.draft:
        body.kvm_host_id = db.scalar(select(KvmHost.id).where(KvmHost.enabled.is_(True)).order_by(KvmHost.id))
    if body.kvm_host_id is None:                 # best fit (design §43)
        ranked = rank_hosts(db, p, [g.model_dump(exclude_none=True) for g in body.host_groups])
        best = next((h for h in ranked if h["ok"]), None)
        if best is None:
            raise FieldError("kvm_host_id", "No KVM host can take this environment: " +
                             "; ".join(f"{h['name']}: {h['reasons'][0] if h['reasons'] else 'not suitable'}" for h in ranked))
        body.kvm_host_id = best["id"]
    host = db.get(KvmHost, body.kvm_host_id)
    if host is None or not host.enabled:
        raise FieldError("kvm_host_id", "Choose an enabled KVM host.")
    if db.scalar(select(Environment).where(Environment.name == body.name, Environment.id != (env_id or 0))):
        raise FieldError("name", f"An environment named {body.name} already exists. Choose another name.",
                         status_code=409)
    seen: dict[str, int] = {}
    for i, g in enumerate(body.host_groups):
        if g.name.lower() in seen:
            raise FieldError(f"host_groups.{i}.name", f"Group name {g.name} is also used by host group "
                                                      f"{seen[g.name.lower()] + 1} (names are compared without case). "
                                                      "Group names must be unique.")
        seen[g.name.lower()] = i
        if g.image_source == "packer_template" and not g.template:
            raise FieldError(f"host_groups.{i}.template", "Building from a template needs a template; choose one.")
        if len(g.names) > g.count:
            raise FieldError(f"host_groups.{i}.names", f"{len(g.names)} names were given for {g.count} hosts.")
    raw = [g.model_dump(exclude_none=True) for g in body.host_groups]
    if not body.draft:
        _deploy_checks(db, p, host.id, raw)
    _check_catalog(db, raw)
    return host, _check_networks(db, p, host.id, raw)


def _deploy_checks(db: Session, p: Principal, host_id: int, raw: list[dict], exclude_env: int | None = None) -> None:
    """What a deploy needs and a draft can leave for later: quota, capacity, VM names, templates."""
    parts = _group_hosts(db, host_id, raw)
    try:
        quotas.check(db, p, raw, exclude_env=exclude_env)                     # design §41
    except quotas.QuotaError as exc:
        raise FieldError("host_groups", str(exc), status_code=403) from exc
    for hid, gs in parts.items():
        _check_capacity(db, hid, gs)
    _check_names(db, host_id, raw, exclude_env)
    _check_templates(db, host_id, raw)


@router.post("", status_code=201)
def create_env(body: EnvironmentIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("deployer", "template_editor"))):
    host, groups = _prepare(db, p, body)
    # The Linux admin account comes from Settings > VM accounts, fixed per environment.
    spec = {"host_groups": groups, "linux_admin_user": appsettings.get(db, "vm_accounts").linux_admin_user,
            "timezone": body.timezone or timezones.DEFAULT}
    e = Environment(name=body.name, kvm_host_id=host.id, spec=spec, created_by=p.username,
                    expires_at=leases.default_expiry(db))
    db.add(e)
    db.commit()
    add_owner(db, "environment", e.id, p.username)
    if body.draft:                               # D185: kept, not deployed (status "new")
        audit(db, p.username, "environment.draft", object_type="environment", object_id=e.id,
              detail={"spec": e.spec}, source_ip=client_ip(request))
        return {"environment": _out(db, e, True), "job_id": None}
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


@router.get("/timezones")
def list_timezones(_: Principal = Depends(current_principal)):
    """The zones the new-environment form offers (design §52)."""
    return {"timezones": timezones.names(), "default": timezones.DEFAULT}


@router.get("/{env_id}")
def get_env(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    return _out(db, e, is_owner(db, p, "environment", e.id))


@router.put("/{env_id}/draft")
def save_draft(env_id: int, body: EnvironmentIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Replace a draft's name, host and groups (D185). Only an owner, and only while it has never been deployed."""
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can change it")
    if e.status != "new":
        raise HTTPException(status_code=409, detail="Only a draft (never deployed) can be edited here")
    body.draft = True
    host, groups = _prepare(db, p, body, env_id=e.id)
    e.name, e.kvm_host_id = body.name, host.id
    e.spec = {**(e.spec or {}), "host_groups": groups, "timezone": body.timezone or timezones.DEFAULT}
    db.commit()
    audit(db, p.username, "environment.draft", object_type="environment", object_id=e.id,
          detail={"spec": e.spec}, source_ip=client_ip(request))
    return _out(db, e, True)


@router.post("/{env_id}/deploy")
def redeploy(env_id: int, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can deploy it")
    if e.status == "destroyed":
        # Back from destroyed, it counts against the quota again (design §41).
        try:
            quotas.check(db, p, (e.spec or {}).get("host_groups", []), exclude_env=e.id)
        except quotas.QuotaError as exc:
            raise FieldError("host_groups", str(exc), status_code=403) from exc
    if e.status == "new":                        # a draft meets the checks a new environment would
        _deploy_checks(db, p, e.kvm_host_id, (e.spec or {}).get("host_groups", []), exclude_env=e.id)
    if e.status in ("destroyed", "new"):
        # A fresh deploy starts a fresh lease (design §40): an old, ended one would shut it down at once.
        e.expires_at, e.lease_state = leases.default_expiry(db), {}
    job = _queue(db, e, "environment.deploy", p)
    audit(db, p.username, "environment.deploy", object_type="environment", object_id=e.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}


@router.delete("/{env_id}")
def delete_record(env_id: int, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Delete a destroyed (or never deployed) environment's record, freeing its name (2026-10-20 round)."""
    from ..engine import envdelete
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can delete it")
    why = envdelete.blockers(db, e)
    if why:
        raise HTTPException(status_code=409, detail="Can't delete it: " + "; ".join(why) + ".")
    counts = envdelete.delete(db, e)
    db.commit()
    audit(db, p.username, "environment.delete", object_type="environment", object_id=env_id, detail=counts,
          source_ip=client_ip(request))
    return {"deleted": env_id, **counts}


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


@router.post("/{env_id}/plan")
def plan_redeploy(env_id: int, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Preview a redeploy (design §56.1): OpenTofu plans which VMs it would replace; nothing is applied."""
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can preview its redeploy")
    if e.status not in ("deployed", "failed"):
        raise HTTPException(status_code=409, detail="Only a deployed (or failed) environment's redeploy can be previewed")
    job = _queue(db, e, planpreview.JOB_TYPE, p)
    audit(db, p.username, "environment.plan", object_type="environment", object_id=e.id,
          detail={"job": job.id}, source_ip=client_ip(request))
    return {"job_id": job.id}


@router.get("/{env_id}/plan")
def latest_plan(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """The newest redeploy preview: {job_id, state, finished_at, plan, stale} or {} when none ran."""
    return planpreview.latest(db, _get(db, env_id, p)) or {}


@router.get("/{env_id}/credentials")
def credentials(env_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(current_principal)):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Credentials are visible to owners and admins only")
    audit(db, p.username, "environment.credentials.view", object_type="environment", object_id=e.id,
          source_ip=client_ip(request))
    text = get_secret(db, f"environment/{e.id}/credentials") or ""
    return {"credentials": text, "sections": sheet_hints(parse_credentials(text))}


def sheet_hints(sections: list[dict]) -> list[dict]:
    """The user name to type, beside what the sheet already says (the sheet's own text is unchanged): a Windows
    VM's local Administrator is `.` plus a backslash plus the name (or COMPUTER, a backslash, then the name), because a
    bare Administrator is completed with the client's own domain; each per-VM password has its user name above it
    and is masked."""
    bs = chr(92)
    for sec in sections:
        title = sec["title"]
        if title.startswith("Windows ("):
            for it in sec["items"]:
                if it["label"] == "Local Administrator" and it["value"] == "Administrator":
                    it["value"] = f".{bs}Administrator"
        elif title.startswith("Windows Administrator, per VM"):
            items = []
            for it in sec["items"]:
                items.append({"label": f"{it['label']} user name", "value": f"{remote.computer_name(it['label'])}{bs}Administrator",
                              "secret": False})
                items.append({**it, "secret": True})
            sec["items"] = items
    return sections


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
