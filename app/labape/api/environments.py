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
    template_from: int | None = None         # a linked stack (its id) the template is copied from when this deploys (row 22)
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
    peer: dict[int, dict] = {}
    for i, g in enumerate(groups):
        if g.get("image_source") != "packer_template":
            continue
        if g.get("template_from"):               # a linked stack's template: checked against that stack's library (row 22)
            from .. import linked_library
            from ..models import StackLink
            link = db.get(StackLink, g["template_from"])
            if link is None:
                raise FieldError(f"host_groups.{i}.template", "That linked stack isn't linked any more.")
            if link.id not in peer:
                try:
                    peer[link.id] = {t["name"]: t for t in linked_library.peer_library(db, link)["templates"]}
                except linked_library.LibraryError as exc:
                    raise FieldError(f"host_groups.{i}.template", f"Can't check {link.name}'s templates: {exc}") from exc
            t = peer[link.id].get(g.get("template") or "")
            if t is None:
                raise FieldError(f"host_groups.{i}.template", f"{link.name} has no template {g.get('template')!r}.")
            if t.get("os_key") and t["os_key"] != g.get("os"):
                raise FieldError(f"host_groups.{i}.template", f"Template {t['name']} holds {t['os_key']}, not {g.get('os')}. "
                                                              "Choose a template for this OS.")
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
