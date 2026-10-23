"""Environment templates (design §68): save an environment's groups as a template, start a new environment from one.

A template keeps what describes the environment (each host group: count, OS, role, resources, network, catalog
choices, start order) and drops what belongs to one deployment (its name, the KVM host, VM names, addresses,
accounts and passwords). The few values a person fills in when starting from it are its *parameters*: a field of a
group (for example the number of hosts, the OS or the network) with a label and the template's value as default.
Starting from a template only fills the New environment form: the person still reviews it and deploys through
the normal create call, so every check the form has still applies.
"""
from __future__ import annotations

import copy
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import linked_library
from ..db import get_db
from ..errors import FieldError
from ..models import CatalogItem, EnvTemplate, Environment, HostNetwork, KvmHost, Network, Template
from ..security import Principal, audit, client_ip, current_principal, require_roles
from .environments import HostGroup, _get

router = APIRouter(prefix="/api/env-templates", tags=["environment templates"])

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{2,59}$")
DROPPED = {"names", "template_from"}                         # per deployment: VM names; the linked stack a copy came from
GROUP_FIELDS = [f for f in HostGroup.model_fields if f not in DROPPED]
PARAM_FIELDS = {"count": "Number of hosts in {g}", "os": "Operating system of {g}", "network": "Network of {g}",
                "cpu_count": "CPUs of each host in {g}", "memory_mb": "Memory (MB) of each host in {g}",
                "disk_gb": "Disk (GB) of each host in {g}"}
FieldName = Literal["count", "os", "network", "cpu_count", "memory_mb", "disk_gb"]


# --- the template's content ------------------------------------------------------------------------

def spec_of(e: Environment) -> dict:
    groups = [{k: v for k, v in g.items() if k in GROUP_FIELDS} for g in (e.spec or {}).get("host_groups", [])]
    out = {"host_groups": groups}
    if (e.spec or {}).get("timezone"):
        out["timezone"] = e.spec["timezone"]
    return out


class ParamIn(BaseModel):
    group: str
    field: FieldName
    label: str = Field(default="", max_length=80)


def make_params(spec: dict, params: list[ParamIn]) -> list[dict]:
    by_name = {g["name"]: g for g in spec["host_groups"]}
    out, seen = [], set()
    for p in params:
        g = by_name.get(p.group)
        if g is None:
            raise FieldError("params", f"There is no host group {p.group!r} in the template.")
        key = f"{p.group}.{p.field}"
        if key in seen:
            raise FieldError("params", f"{key} is listed twice.")
        seen.add(key)
        default = g.get(p.field)
        out.append({"key": key, "group": p.group, "field": p.field, "default": default,
                    "label": p.label.strip() or PARAM_FIELDS[p.field].format(g=p.group)})
    return out


def apply(spec: dict, params: list[dict], values: dict) -> list[dict]:
    """The template's host groups with the given parameter values (or the defaults) in place, each checked as the
    New environment form would."""
    groups = copy.deepcopy(spec["host_groups"])
    by_name = {g["name"]: g for g in groups}
    for p in params:
        raw = values.get(p["key"], p["default"])
        if raw in (None, ""):
            continue
        try:
            by_name[p["group"]][p["field"]] = int(raw) if p["field"] not in ("os", "network") else str(raw)
        except (ValueError, KeyError) as exc:
            raise FieldError(f"values.{p['key']}", f"{p['label']}: {raw!r} isn't a valid value.") from exc
    out = []
    for g in groups:
        try:
            out.append(HostGroup(**g).model_dump())
        except ValidationError as exc:
            err = exc.errors()[0]
            raise FieldError("values", f"Host group {g.get('name')}: {err['msg'].removeprefix('Value error, ')}"
                                       f" ({'.'.join(str(x) for x in err['loc'])}).") from exc
    return out


def check_against(db: Session, groups: list[dict], host: KvmHost | None) -> list[dict]:
    """What the chosen KVM host or the catalog lacks for these groups. Fills in an OS template when the group has none
    and the host has one, and points at a linked stack's copy when only that has it."""
    notes: list[dict] = []
    libs = None
    for g in groups:
        name = g["name"]
        for kind, key in (("service", "services"), ("application", "applications"), ("package", "packages")):
            for item in g.get(key) or []:
                st = db.scalar(select(CatalogItem.status).where(CatalogItem.kind == kind, CatalogItem.name == item))
                if st != "published":
                    notes.append({"group": name, "level": "warn", "message":
                                  f"The {kind} {item!r} is {'not in the catalog' if st is None else st + ', so it cannot be chosen'}."})
        if host is None:
            continue
        have = {t.name: t for t in db.scalars(select(Template).where(Template.kvm_host_id == host.id, Template.present.is_(True)))}
        if g.get("template"):
            if g["template"] not in have:
                if libs is None:
                    try:
                        libs = linked_library.all_libraries(db)
                    except Exception:  # noqa: BLE001 — a peer being down must not stop the form
                        libs = []
                peer = next((s for s in libs if s.get("ok") and any(t["name"] == g["template"] for t in s.get("templates", []))), None)
                if peer:
                    g["template_from"] = peer["link_id"]
                    notes.append({"group": name, "level": "info", "message":
                                  f"The template {g['template']} is on the linked stack {peer['name']}, not on {host.name}: "
                                  f"it is copied here when the environment deploys."})
                else:
                    notes.append({"group": name, "level": "warn", "message":
                                  f"{host.name} has no template named {g['template']}. Pick another in the form."})
        else:
            mine = sorted(t.name for t in have.values() if t.os_key == g["os"] and t.present)
            if len(mine) == 1:                                 # one candidate: use it; with several, the person chooses
                g["image_source"], g["template"] = "packer_template", mine[0]
            elif mine:
                notes.append({"group": name, "level": "info", "message":
                              f"{host.name} has {len(mine)} templates for {g['os']}: pick one in the form."})
            else:
                notes.append({"group": name, "level": "warn", "message":
                              f"{host.name} has no template for {g['os']}. Pick an image source in the form."})
        net = g.get("network")
        if net:
            carried = db.scalar(select(func.count()).select_from(HostNetwork).join(Network, Network.id == HostNetwork.network_id)
                                .where(HostNetwork.kvm_host_id == host.id, Network.name == net))
            if not carried:
                notes.append({"group": name, "level": "warn", "message": f"{host.name} does not carry the network {net!r}."})
    return notes


# --- starters shipped with LABaPe -------------------------------------------------------------------

def _g(name, os, role, count=1, **kw):
    return {"name": name, "count": count, "os": os, "roles": [role], "cpu_count": 2, "memory_mb": 4096, "disk_gb": 40,
            "windows_core": False, "windows_datacenter": False, "addressing": "static", "services": [],
            "applications": [], "packages": [], "start_wait": "agent", "start_delay_seconds": 0, **kw}


def _hosts(group, label="Number of hosts in {g}"):
    return {"key": f"{group}.count", "group": group, "field": "count", "label": label.format(g=group), "default": None}


BUILTINS = [
    {"name": "Domain with members", "description":
        "One domain controller with Windows and Linux member servers that join its domain: the usual starting point for "
        "anything that needs Active Directory.",
     "groups": [_g("dc", "windows_server_2022", "domain_controller"), _g("winsrv", "windows_server_2022", "windows_server", 1),
                _g("linsrv", "rocky9", "linux_server", 1)],
     "params": ["winsrv.count", "linsrv.count"]},
    {"name": "Three-tier Linux (standalone)", "description":
        "Web, application and database Linux hosts that are not joined to any domain.",
     "groups": [_g("web", "rocky9", "linux_server", 1, domain_member=False), _g("app", "rocky9", "linux_server", 1, domain_member=False),
                _g("db", "rocky9", "linux_server", 1, domain_member=False)],
     "params": ["web.count", "app.count", "db.count", "web.os"]},
    {"name": "Kerberos lab", "description":
        "A domain controller, a Linux member and a Windows member: for trying Kerberos sign-in between them.",
     "groups": [_g("dc", "windows_server_2022", "domain_controller"), _g("lin", "rocky9", "linux_server", 1),
                _g("win", "windows_server_2022", "windows_server", 1)],
     "params": []},
]


def ensure_builtins(db: Session) -> None:
    """The starters are code: keep their rows in step with it."""
    for b in BUILTINS:
        spec = {"host_groups": copy.deepcopy(b["groups"])}
        params = []
        for key in b["params"]:
            g, f = key.split(".")
            params.append(make_params(spec, [ParamIn(group=g, field=f)])[0])
        row = db.scalar(select(EnvTemplate).where(EnvTemplate.name == b["name"]))
        if row is None:
            db.add(EnvTemplate(name=b["name"], description=b["description"], builtin=True, shared=True, spec=spec, params=params))
        elif row.builtin and (row.spec != spec or row.params != params or row.description != b["description"]):
            row.spec, row.params, row.description = spec, params, b["description"]
    db.commit()


# --- the API -----------------------------------------------------------------------------------------

def _visible(db: Session, p: Principal):
    q = select(EnvTemplate).order_by(EnvTemplate.builtin.desc(), func.lower(EnvTemplate.name))
    if not p.is_admin:
        q = q.where((EnvTemplate.builtin.is_(True)) | (EnvTemplate.shared.is_(True)) | (EnvTemplate.owner == p.username))
    return q


def _out(t: EnvTemplate, p: Principal, full: bool = False) -> dict:
    groups = t.spec.get("host_groups", [])
    out = {"id": t.id, "name": t.name, "description": t.description, "owner": t.owner, "shared": t.shared, "builtin": t.builtin,
           "can_edit": (not t.builtin) and (p.is_admin or t.owner == p.username),
           "summary": [{"name": g["name"], "count": g.get("count", 1), "os": g["os"], "role": (g.get("roles") or [""])[0]} for g in groups],
           "params": t.params, "updated_at": t.updated_at}
    if full:
        out["spec"] = t.spec
    return out


def _get_visible(db: Session, tid: int, p: Principal) -> EnvTemplate:
    t = db.get(EnvTemplate, tid)
    if t is None or not (p.is_admin or t.builtin or t.shared or t.owner == p.username):
        raise HTTPException(status_code=404, detail="No such template")
    return t


def _name_free(db: Session, name: str, ignore: int | None = None) -> None:
    if not NAME_RE.match(name):
        raise FieldError("name", "A template name is 3 to 60 letters, digits, spaces, dots, dashes or underscores.")
    clash = db.scalar(select(EnvTemplate.id).where(func.lower(EnvTemplate.name) == name.lower(), EnvTemplate.id != (ignore or 0)))
    if clash:
        raise FieldError("name", "A template with that name already exists.")


class TemplateIn(BaseModel):
    name: str
    description: str = Field(default="", max_length=500)
    shared: bool = False
    from_environment_id: int | None = None
    params: list[ParamIn] = Field(default_factory=list, max_length=60)


@router.get("")
def list_templates(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ensure_builtins(db)
    return [_out(t, p) for t in db.scalars(_visible(db, p))]


@router.get("/{tid}")
def get_template(tid: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ensure_builtins(db)
    return _out(_get_visible(db, tid, p), p, full=True)


@router.post("", status_code=201)
def create_template(body: TemplateIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("deployer", "template_editor"))):
    if body.from_environment_id is None:
        raise FieldError("from_environment_id", "Say which environment to save as a template.")
    env = _get(db, body.from_environment_id, p)
    name = body.name.strip()
    _name_free(db, name)
    spec = spec_of(env)
    t = EnvTemplate(name=name, description=body.description.strip(), owner=p.username, shared=body.shared, spec=spec,
                    params=make_params(spec, body.params))
    db.add(t)
    db.commit()
    audit(db, p.username, "env_template.create", object_type="env_template", object_id=t.id,
          detail={"name": name, "from": env.name, "shared": t.shared}, source_ip=client_ip(request))
    return _out(t, p, full=True)


@router.put("/{tid}")
def update_template(tid: int, body: TemplateIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("deployer", "template_editor"))):
    t = _get_visible(db, tid, p)
    if t.builtin or not (p.is_admin or t.owner == p.username):
        raise HTTPException(status_code=403, detail="Only the template's owner or an admin can change it, and the starters are fixed")
    name = body.name.strip()
    _name_free(db, name, ignore=t.id)
    t.name, t.description, t.shared = name, body.description.strip(), body.shared
    t.params = make_params(t.spec, body.params)
    db.commit()
    audit(db, p.username, "env_template.update", object_type="env_template", object_id=t.id,
          detail={"name": name, "shared": t.shared}, source_ip=client_ip(request))
    return _out(t, p, full=True)


@router.delete("/{tid}")
def delete_template(tid: int, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("deployer", "template_editor"))):
    t = _get_visible(db, tid, p)
    if t.builtin or not (p.is_admin or t.owner == p.username):
        raise HTTPException(status_code=403, detail="Only the template's owner or an admin can delete it, and the starters stay")
    audit(db, p.username, "env_template.delete", object_type="env_template", object_id=t.id,
          detail={"name": t.name}, source_ip=client_ip(request))
    db.delete(t)
    db.commit()
    return {"ok": True}


class InstantiateIn(BaseModel):
    values: dict[str, str | int] = Field(default_factory=dict)
    kvm_host_id: int | None = None


@router.post("/{tid}/instantiate")
def instantiate(tid: int, body: InstantiateIn, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """The host groups the New environment form starts from, with the chosen values in place and what is missing on the host."""
    t = _get_visible(db, tid, p)
    groups = apply(t.spec, t.params, body.values)
    host = db.get(KvmHost, body.kvm_host_id) if body.kvm_host_id else None
    notes = check_against(db, groups, host)
    return {"host_groups": groups, "timezone": t.spec.get("timezone"), "warnings": notes, "template": t.name}
