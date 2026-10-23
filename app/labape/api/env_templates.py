"""Environment templates (design §68): save an environment's groups as a template, start a new environment from one.

A template keeps what describes the environment (each host group: count, OS, role, resources, network, catalog
choices, start order) and drops what belongs to one deployment (its name, the KVM host, VM names, addresses,
accounts and passwords). The few values a person fills in when starting from it are its *parameters*: a field of a
group (for example the number of hosts, the OS or the network) with a label and the template's value as default.
Starting from a template only fills the New environment form: the person still reviews it and deploys through
the normal create call, so every check the form has still applies.

A template may also be made of other templates (sub-assemblies, design §69): each included template brings its host
groups with a prefix on their names, its parameters (prefixed) and any values the including template fixes; the same
part can be included twice under two prefixes. A template can be exported as YAML and imported on another stack.
"""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import linked_library
from ..db import get_db
from ..errors import FieldError
from ..models import CatalogItem, EnvTemplate, EnvTemplateVersion, Environment, HostNetwork, KvmHost, Network, Template
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
            row = EnvTemplate(name=b["name"], description=b["description"], builtin=True, shared=True, spec=spec, params=params, version=1)
            db.add(row)
            db.flush()
            snapshot(db, row, "LABaPe")
        elif row.builtin and (row.spec != spec or row.params != params or row.description != b["description"]):
            row.spec, row.params, row.description = spec, params, b["description"]
            bump_if_changed(db, row, "LABaPe")
    db.commit()


# --- the API -----------------------------------------------------------------------------------------

def _visible(db: Session, p: Principal):
    q = select(EnvTemplate).order_by(EnvTemplate.builtin.desc(), func.lower(EnvTemplate.name))
    if not p.is_admin:
        q = q.where((EnvTemplate.builtin.is_(True)) | (EnvTemplate.shared.is_(True)) | (EnvTemplate.owner == p.username))
    return q


MAX_DEPTH = 5                                                  # how deep templates may include templates
FORMAT = "labape-environment-templates/1"


class IncludeIn(BaseModel):
    template_id: int
    prefix: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,5}$")
    label: str = Field(default="", max_length=60)
    overrides: dict[str, dict[str, str | int]] = Field(default_factory=dict)      # the part's group -> field -> value
    version: int | None = Field(default=None, ge=1)             # None: follow the part's latest; a number: stay on that version


def snapshot(db: Session, t: EnvTemplate, who: str) -> None:
    """Keep what the template is now as its current version (design §71)."""
    db.add(EnvTemplateVersion(template_id=t.id, version=t.version, spec=copy.deepcopy(t.spec), params=copy.deepcopy(t.params),
                              includes=copy.deepcopy(t.includes), created_by=who))


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def bump_if_changed(db: Session, t: EnvTemplate, who: str) -> None:
    """A change to the groups, parameters or parts is a new version; a new name, description or sharing is not."""
    last = db.scalar(select(EnvTemplateVersion).where(EnvTemplateVersion.template_id == t.id, EnvTemplateVersion.version == t.version))
    if last is None or not (_same(last.spec, t.spec) and _same(last.params, t.params) and _same(last.includes, t.includes)):
        t.version += 1
        snapshot(db, t, who)


def _content(db: Session, child: EnvTemplate, version: int | None):
    """The template itself, or what it was at a pinned version."""
    if version is None or version == child.version:
        return child
    snap = db.scalar(select(EnvTemplateVersion).where(EnvTemplateVersion.template_id == child.id, EnvTemplateVersion.version == version))
    if snap is None:
        raise FieldError("includes", f"{child.name} has no version {version}.")
    return SimpleNamespace(id=child.id, name=child.name, spec=snap.spec, params=snap.params, includes=snap.includes, version=version)


def compose(db: Session, t: EnvTemplate, chain: tuple[str, ...] = ()) -> tuple[list[dict], list[dict]]:
    """The host groups and parameters of a template with everything it includes. Refuses a circle, a nesting deeper
    than MAX_DEPTH, a part that is gone, a name that comes out too long or twice."""
    if t.name.lower() in (c.lower() for c in chain):
        raise FieldError("includes", f"The templates include each other in a circle: {' > '.join((*chain, t.name))}.")
    if len(chain) >= MAX_DEPTH:
        raise FieldError("includes", f"Templates can be nested {MAX_DEPTH} deep at most.")
    groups = copy.deepcopy((t.spec or {}).get("host_groups", []))
    params = copy.deepcopy(t.params or [])
    for inc in t.includes or []:
        child = db.get(EnvTemplate, inc["template_id"])
        if child is None:
            raise FieldError("includes", f"The included template {inc.get('name') or inc['template_id']!r} no longer exists.")
        cg, cp = compose(db, _content(db, child, inc.get("version")), (*chain, t.name))
        prefix = inc["prefix"]
        over = inc.get("overrides") or {}
        for g in cg:
            for field, value in (over.get(g["name"]) or {}).items():
                if field not in PARAM_FIELDS:
                    raise FieldError("includes", f"{field} can't be set when including {child.name}: choose from {', '.join(PARAM_FIELDS)}.")
                g[field] = value if field in ("os", "network") else int(value)
        for q in cp:
            ov = (over.get(q["group"]) or {}).get(q["field"])
            if ov is not None:
                q["default"] = ov
        rename = {g["name"]: prefix + g["name"] for g in cg}
        too_long = next((n for n in rename.values() if len(n) > 12), None)
        if too_long:
            raise FieldError("includes", f"With the prefix {prefix!r}, the host group {too_long!r} would be longer than 12 letters.")
        for g in cg:
            g["name"] = rename[g["name"]]
        for q in cp:
            q.update(group=rename[q["group"]], key=f"{rename[q['group']]}.{q['field']}",
                     label=f"{inc.get('label') or prefix}: {q['label']}")
        groups += cg
        params += cp
    names = [g["name"].lower() for g in groups]
    dup = next((n for n in names if names.count(n) > 1), None)
    if dup:
        raise FieldError("includes", f"The host group name {dup!r} comes out twice: give the included templates different prefixes.")
    return groups, params


def _includes(db: Session, p: Principal, items: list[IncludeIn]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        child = _get_visible(db, it.template_id, p)
        if it.prefix.lower() in seen:
            raise FieldError("includes", f"The prefix {it.prefix!r} is used twice: each included template needs its own.")
        seen.add(it.prefix.lower())
        if it.version is not None and it.version > child.version:
            raise FieldError("includes", f"{child.name} has no version {it.version} (the latest is {child.version}).")
        out.append({"template_id": child.id, "name": child.name, "prefix": it.prefix, "label": it.label.strip(),
                    "overrides": it.overrides, "version": it.version})
    return out


def _used_by(db: Session) -> dict[int, list[str]]:
    uses: dict[int, list[str]] = {}
    for t in db.scalars(select(EnvTemplate)):
        for inc in t.includes or []:
            uses.setdefault(inc["template_id"], []).append(t.name)
    return uses


def _include_out(db: Session, inc: dict) -> dict:
    """A part as stored, with where its template is now: pinned parts say whether a newer version exists."""
    child = db.get(EnvTemplate, inc["template_id"])
    latest = child.version if child is not None else None
    pin = inc.get("version")
    return {**inc, "version": pin, "latest": latest, "behind": bool(pin is not None and latest is not None and pin < latest)}


def _out(db: Session, t: EnvTemplate, p: Principal, full: bool = False, uses: dict | None = None) -> dict:
    uses = _used_by(db) if uses is None else uses
    try:
        groups, params = compose(db, t)
        broken = ""
    except FieldError as exc:
        groups, params, broken = t.spec.get("host_groups", []), t.params, str(exc.detail)
    out = {"id": t.id, "name": t.name, "description": t.description, "owner": t.owner, "shared": t.shared, "builtin": t.builtin,
           "can_edit": (not t.builtin) and (p.is_admin or t.owner == p.username),
           "summary": [{"name": g["name"], "count": g.get("count", 1), "os": g["os"], "role": (g.get("roles") or [""])[0]} for g in groups],
           "params": params, "own_params": t.params, "includes": [_include_out(db, i) for i in t.includes or []], "version": t.version,
           "used_by": uses.get(t.id, []), "broken": broken, "updated_at": t.updated_at}
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
    includes: list[IncludeIn] = Field(default_factory=list, max_length=20)


@router.get("")
def list_templates(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ensure_builtins(db)
    uses = _used_by(db)
    return [_out(db, t, p, uses=uses) for t in db.scalars(_visible(db, p))]


# --- YAML export and import (declared before /{tid}, which would take "export" for an id) -----------------

def _doc(name: str, description: str, content, includes: list[dict]) -> dict:
    d: dict = {"name": name, "description": description}
    if content.spec.get("timezone"):
        d["timezone"] = content.spec["timezone"]
    d["host_groups"] = content.spec.get("host_groups", [])
    d["params"] = [{"group": q["group"], "field": q["field"], "label": q["label"]} for q in content.params]
    if includes:
        d["includes"] = includes
    return d


def _yaml_response(docs: list[dict], filename: str) -> Response:
    text = yaml.safe_dump({"format": FORMAT, "templates": docs}, sort_keys=False, allow_unicode=True)
    return Response(text, media_type="application/yaml", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _bundle(db: Session, roots: list[EnvTemplate]) -> list[dict]:
    """The documents for these templates and every template they include, parts first. A part pinned to an older version
    is written as that version's content (named `<name> v<n>`), so the file stands alone: the other stack has no
    versions to pin to."""
    docs: list[dict] = []
    names: dict[tuple[int, int | None], str] = {}

    def walk(tpl: EnvTemplate, pin: int | None) -> str:
        key = (tpl.id, None if pin == tpl.version else pin)
        if key in names:
            return names[key]
        content = _content(db, tpl, pin)
        docname = tpl.name if key[1] is None else f"{tpl.name} v{pin}"
        names[key] = docname
        refs = []
        for inc in content.includes or []:
            child = db.get(EnvTemplate, inc["template_id"])
            ref = child.name if child is None or child.builtin else walk(child, inc.get("version"))
            refs.append({"template": ref, "prefix": inc["prefix"], **({"label": inc["label"]} if inc.get("label") else {}),
                         **({"overrides": inc["overrides"]} if inc.get("overrides") else {})})
        docs.append(_doc(docname, tpl.description, content, refs))
        return docname

    for t in roots:
        walk(t, None)
    return docs


@router.get("/export")
def export_all(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """Every template this person may see, except the starters (every stack has those), as one YAML bundle."""
    ensure_builtins(db)
    mine = [t for t in db.scalars(_visible(db, p)) if not t.builtin]
    return _yaml_response(_bundle(db, mine), "labape-environment-templates.yaml")


@router.get("/{tid}/export")
def export_one(tid: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """One template, with the templates it includes (a bundle that imports on its own), as YAML."""
    t = _get_visible(db, tid, p)
    audit(db, p.username, "env_template.export", object_type="env_template", object_id=t.id, detail={"name": t.name},
          source_ip=client_ip(request))
    return _yaml_response(_bundle(db, [t]), f"{re.sub(r'[^A-Za-z0-9._-]+', '-', t.name)}.yaml")


class ImportIn(BaseModel):
    yaml: str = Field(max_length=1_000_000)
    shared: bool = False


@router.post("/import", status_code=201)
def import_templates(body: ImportIn, request: Request, db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("deployer", "template_editor"))):
    """Create the templates in a YAML bundle (all, or none). Included templates are found by name, among the ones
    this person may use and the ones earlier in the same file."""
    try:
        doc = yaml.safe_load(body.yaml)
    except yaml.YAMLError as exc:
        raise FieldError("yaml", f"That isn't valid YAML: {str(exc)[:200]}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("templates"), list) or not doc["templates"]:
        raise FieldError("yaml", f"Expected a LABaPe template bundle ({FORMAT}): a `templates:` list.")
    if doc.get("format") not in (None, FORMAT):
        raise FieldError("yaml", f"This file is {doc.get('format')!r}; this stack reads {FORMAT!r}.")
    created: list[EnvTemplate] = []
    try:
        for n, raw in enumerate(doc["templates"], 1):
            if not isinstance(raw, dict):
                raise FieldError("yaml", f"Template {n} isn't a mapping.")
            name = str(raw.get("name", "")).strip()
            where = f"Template {n} ({name or 'no name'})"
            try:
                _name_free(db, name)
                groups = []
                for g in raw.get("host_groups") or []:
                    full = HostGroup(**{k: v for k, v in g.items() if k not in DROPPED}).model_dump()
                    groups.append({k: v for k, v in full.items() if k in GROUP_FIELDS})
                spec = {"host_groups": groups, **({"timezone": raw["timezone"]} if raw.get("timezone") else {})}
                params = make_params(spec, [ParamIn(**q) for q in raw.get("params") or []])
                includes = []
                for inc in raw.get("includes") or []:
                    child = db.scalar(select(EnvTemplate).where(func.lower(EnvTemplate.name) == str(inc.get("template", "")).lower()))
                    if child is None or not (p.is_admin or child.builtin or child.shared or child.owner == p.username):
                        raise FieldError("yaml", f"it includes {inc.get('template')!r}, which this stack doesn't have (import it first)")
                    includes.append(_includes(db, p, [IncludeIn(template_id=child.id, prefix=inc.get("prefix", ""),
                                                                label=inc.get("label", ""), overrides=inc.get("overrides") or {})])[0])
                if not groups and not includes:
                    raise FieldError("yaml", "it has no host groups and includes nothing")
                t = EnvTemplate(name=name, description=str(raw.get("description", ""))[:500], owner=p.username, shared=body.shared,
                                spec=spec, params=params, includes=includes)
                db.add(t)
                db.flush()
                compose(db, t)
                snapshot(db, t, p.username)
            except (ValidationError, ValueError, TypeError) as exc:
                raise FieldError("yaml", f"{where}: {str(exc)[:240]}") from exc
            except FieldError as exc:
                raise FieldError("yaml", f"{where}: {exc.detail}") from exc
            created.append(t)
    except FieldError:
        db.rollback()
        raise
    db.commit()
    audit(db, p.username, "env_template.import", detail={"names": [t.name for t in created]}, source_ip=client_ip(request))
    return {"created": [{"id": t.id, "name": t.name} for t in created]}


@router.get("/{tid}")
def get_template(tid: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    ensure_builtins(db)
    return _out(db, _get_visible(db, tid, p), p, full=True)


@router.post("", status_code=201)
def create_template(body: TemplateIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("deployer", "template_editor"))):
    if body.from_environment_id is None and not body.includes:
        raise FieldError("from_environment_id", "Say which environment to save as a template, or which templates this one is made of.")
    env = _get(db, body.from_environment_id, p) if body.from_environment_id is not None else None
    name = body.name.strip()
    _name_free(db, name)
    spec = spec_of(env) if env is not None else {"host_groups": []}
    t = EnvTemplate(name=name, description=body.description.strip(), owner=p.username, shared=body.shared, spec=spec,
                    params=make_params(spec, body.params), includes=_includes(db, p, body.includes))
    compose(db, t)                                              # refuses what could not be used, before it is saved
    t.version = 1
    db.add(t)
    db.flush()
    snapshot(db, t, p.username)
    db.commit()
    audit(db, p.username, "env_template.create", object_type="env_template", object_id=t.id,
          detail={"name": name, "from": env.name if env else "", "includes": [i["name"] for i in t.includes], "shared": t.shared},
          source_ip=client_ip(request))
    return _out(db, t, p, full=True)


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
    t.includes = _includes(db, p, body.includes)
    try:
        compose(db, t)
    except FieldError:
        db.rollback()
        raise
    bump_if_changed(db, t, p.username)
    db.commit()
    audit(db, p.username, "env_template.update", object_type="env_template", object_id=t.id,
          detail={"name": name, "shared": t.shared, "includes": [i["name"] for i in t.includes]}, source_ip=client_ip(request))
    return _out(db, t, p, full=True)


@router.delete("/{tid}")
def delete_template(tid: int, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("deployer", "template_editor"))):
    t = _get_visible(db, tid, p)
    if t.builtin or not (p.is_admin or t.owner == p.username):
        raise HTTPException(status_code=403, detail="Only the template's owner or an admin can delete it, and the starters stay")
    users = _used_by(db).get(t.id, [])
    if users:
        raise HTTPException(status_code=409, detail=f"{t.name} is part of {', '.join(users)}: take it out of those first.")
    audit(db, p.username, "env_template.delete", object_type="env_template", object_id=t.id,
          detail={"name": t.name}, source_ip=client_ip(request))
    # The versions go first, by a statement that runs now: the unit of work would not order two unrelated tables' deletes
    # (Postgres enforces the foreign key; SQLite in the tests does not).
    db.execute(delete(EnvTemplateVersion).where(EnvTemplateVersion.template_id == t.id))
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
    raw, params = compose(db, t)
    groups = apply({"host_groups": raw}, params, body.values)
    host = db.get(KvmHost, body.kvm_host_id) if body.kvm_host_id else None
    notes = check_against(db, groups, host)
    dcs = [g["name"] for g in groups if "domain_controller" in g["roles"]]
    if len(dcs) > 1:
        notes.append({"group": ", ".join(dcs), "level": "warn", "message":
                      "More than one group is a domain controller: they would all be made for the same domain. "
                      "Check that the included templates are meant to be used together."})
    return {"host_groups": groups, "timezone": t.spec.get("timezone"), "warnings": notes, "template": t.name}
