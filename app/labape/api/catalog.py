"""Catalog of services, applications and packages; requests; YAML import/export
(design §21.4, questions 51-53)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ..catalog import KINDS, CatalogError, package_platforms, platforms, validate
from ..db import get_db
from ..errors import FieldError
from ..models import CatalogItem, CatalogRequest, utcnow
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
FIELDS = ("id", "kind", "name", "display_name", "description", "notes", "status", "definition", "platforms",
          "created_by", "updated_by", "updated_at")
EXPORT_FORMAT = 1


def _out(i: CatalogItem) -> dict:
    out = {f: getattr(i, f) for f in FIELDS}
    if i.kind == "package":              # from its members as they are now, so a change to one shows at once
        out["platforms"] = package_platforms(object_session(i), i.definition)
    return out


@router.get("")
def list_items(kind: str | None = None, status: str | None = None, db: Session = Depends(get_db),
               p: Principal = Depends(current_principal)):
    """Published items for everyone; admins also see drafts and retired ones."""
    q = select(CatalogItem).order_by(CatalogItem.kind, CatalogItem.name)
    if kind:
        q = q.where(CatalogItem.kind == kind)
    if not p.is_admin:
        q = q.where(CatalogItem.status == "published")
    elif status:
        q = q.where(CatalogItem.status == status)
    return [_out(i) for i in db.scalars(q)]


class ItemIn(BaseModel):
    kind: Literal["service", "application", "package"]
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=8000)
    definition: dict = {}


def _apply(db: Session, i: CatalogItem, body: ItemIn) -> None:
    try:
        validate(db, body.kind, body.definition)
    except CatalogError as exc:
        raise FieldError(exc.field, str(exc)) from exc
    i.kind, i.name, i.display_name = body.kind, body.name, body.display_name
    i.description, i.notes, i.definition = body.description, body.notes, body.definition
    i.platforms = platforms(body.kind, body.definition)


def _require_published_members(db: Session, definition: dict, field: str) -> None:
    """A package that is (or becomes) published may include only published items."""
    for it in (definition or {}).get("items", []):
        ref = db.scalar(select(CatalogItem).where(CatalogItem.kind == it["kind"], CatalogItem.name == it["name"]))
        if ref is None or ref.status != "published":
            raise FieldError(field, f"Publish {it['kind']} {it['name']} first; a published package can only include "
                                    "published items. (Save it as a draft, or take that item out.)")


@router.post("", status_code=201)
def create_item(body: ItemIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    if db.scalar(select(CatalogItem).where(CatalogItem.kind == body.kind, CatalogItem.name == body.name)):
        raise FieldError("name", f"A {body.kind} named {body.name} already exists.", status_code=409)
    i = CatalogItem(status="draft", created_by=p.username, updated_by=p.username)
    _apply(db, i, body)
    db.add(i)
    db.commit()
    audit(db, p.username, "catalog.create", object_type=body.kind, object_id=i.id, detail={"name": i.name},
          source_ip=client_ip(request))
    return _out(i)


@router.put("/{item_id}")
def update_item(item_id: int, body: ItemIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    i = db.get(CatalogItem, item_id)
    if i is None:
        raise FieldError("name", "No such catalog item.", status_code=404)
    if (body.kind, body.name) != (i.kind, i.name):
        raise FieldError("name", "Kind and name can't change; create a new item instead.")
    if i.status == "published" and i.kind == "package":
        _require_published_members(db, body.definition, "items")
    _apply(db, i, body)
    i.updated_by = p.username
    db.commit()
    audit(db, p.username, "catalog.update", object_type=i.kind, object_id=i.id, detail={"name": i.name},
          source_ip=client_ip(request))
    return _out(i)


class StatusIn(BaseModel):
    status: Literal["draft", "published", "retired"]


@router.put("/{item_id}/status")
def set_status(item_id: int, body: StatusIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    """Publish (users can choose it), retire (hidden from new choices), or back to draft."""
    i = db.get(CatalogItem, item_id)
    if i is None:
        raise FieldError("status", "No such catalog item.", status_code=404)
    if body.status == "published" and i.kind == "package":
        _require_published_members(db, i.definition, "status")
    i.status = body.status
    i.updated_by = p.username
    db.commit()
    audit(db, p.username, f"catalog.{body.status}", object_type=i.kind, object_id=i.id, detail={"name": i.name},
          source_ip=client_ip(request))
    return _out(i)


# --- export / import -------------------------------------------------------------

def _closure(db: Session, items: list[CatalogItem]) -> list[CatalogItem]:
    """Items plus everything their packages include, so an export imports cleanly."""
    out = {i.id: i for i in items}
    queue = list(items)
    while queue:
        i = queue.pop()
        for it in (i.definition or {}).get("items", []) if i.kind == "package" else []:
            ref = db.scalar(select(CatalogItem).where(CatalogItem.kind == it["kind"], CatalogItem.name == it["name"]))
            if ref is not None and ref.id not in out:
                out[ref.id] = ref
                queue.append(ref)
    return list(out.values())


@router.get("/export")
def export_items(ids: str = "", request: Request = None, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    """YAML of the chosen items (comma-separated ids; empty = everything), with dependencies."""
    q = select(CatalogItem)
    if ids:
        q = q.where(CatalogItem.id.in_([int(x) for x in ids.split(",") if x.strip().isdigit()]))
    items = _closure(db, list(db.scalars(q)))
    doc = {"labape_catalog": EXPORT_FORMAT, "exported": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    for kind in KINDS:
        doc[f"{kind}s"] = [{"name": i.name, "display_name": i.display_name, "description": i.description,
                            "notes": i.notes, "definition": i.definition}
                           for i in sorted(items, key=lambda x: x.name) if i.kind == kind]
    audit(db, p.username, "catalog.export", detail={"items": len(items)}, source_ip=client_ip(request))
    name = f"labape-catalog-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}.yaml"
    return Response(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), media_type="application/yaml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/import")
async def import_items(file: UploadFile, request: Request, publish: bool = False, overwrite: bool = False,
                       db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """Import a catalog YAML (an export, or a hand-made package). New items
    arrive as drafts unless publish=true; existing names are skipped unless
    overwrite=true. Services and applications import before packages."""
    try:
        doc = yaml.safe_load(await file.read()) or {}
    except yaml.YAMLError as exc:
        raise FieldError("file", f"That isn't valid YAML: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("labape_catalog") != EXPORT_FORMAT:
        raise FieldError("file", "Not a LABaPe catalog file: expected `labape_catalog: 1` at the top.")
    created, updated, skipped = [], [], []
    for kind in KINDS:  # packages last: they reference the others
        for raw in doc.get(f"{kind}s") or []:
            try:
                body = ItemIn(kind=kind, **{k: raw.get(k, "") for k in ("name", "display_name", "description", "notes")},
                              definition=raw.get("definition") or {})
            except ValueError as exc:
                raise FieldError("file", f"{kind} {raw.get('name')!r}: {exc}") from exc
            existing = db.scalar(select(CatalogItem).where(CatalogItem.kind == kind, CatalogItem.name == body.name))
            if existing is not None and not overwrite:
                skipped.append(f"{kind}:{body.name}")
                continue
            i = existing or CatalogItem(created_by=p.username, status="draft")
            try:
                _apply(db, i, body)
            except FieldError as exc:
                db.rollback()
                raise FieldError("file", f"{kind} {body.name}: {exc.detail}") from exc
            i.updated_by = p.username
            if publish:
                i.status = "published"
            if existing is None:
                db.add(i)
                created.append(f"{kind}:{body.name}")
            else:
                updated.append(f"{kind}:{body.name}")
            db.flush()
    db.commit()
    audit(db, p.username, "catalog.import", detail={"created": created, "updated": updated, "skipped": skipped},
          source_ip=client_ip(request))
    return {"created": created, "updated": updated, "skipped": skipped}


# --- requests (questions 51, 52) ---------------------------------------------------

REQ_FIELDS = ("id", "kind", "title", "details", "status", "response", "requested_by", "handled_by", "created_at",
              "updated_at")


class RequestIn(BaseModel):
    kind: Literal["service", "application", "package"]
    title: str = Field(min_length=3, max_length=200)
    details: str = Field(default="", max_length=8000)


@router.get("/requests")
def list_requests(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    q = select(CatalogRequest).order_by(CatalogRequest.id.desc())
    if not p.is_admin:
        q = q.where(CatalogRequest.requested_by == p.username)
    return [{f: getattr(r, f) for f in REQ_FIELDS} for r in db.scalars(q)]


@router.post("/requests", status_code=201)
def create_request(body: RequestIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(current_principal)):
    r = CatalogRequest(kind=body.kind, title=body.title, details=body.details, requested_by=p.username)
    db.add(r)
    db.commit()
    audit(db, p.username, "catalog.request", object_type="catalog_request", object_id=r.id,
          detail={"kind": r.kind, "title": r.title}, source_ip=client_ip(request))
    return {f: getattr(r, f) for f in REQ_FIELDS}


class RequestUpdate(BaseModel):
    status: Literal["open", "accepted", "done", "declined"]
    response: str = Field(default="", max_length=4000)


@router.put("/requests/{request_id}")
def handle_request(request_id: int, body: RequestUpdate, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    r = db.get(CatalogRequest, request_id)
    if r is None:
        raise FieldError("status", "No such request.", status_code=404)
    r.status, r.response, r.handled_by, r.updated_at = body.status, body.response, p.username, utcnow()
    db.commit()
    audit(db, p.username, "catalog.request.update", object_type="catalog_request", object_id=r.id,
          detail=body.model_dump(), source_ip=client_ip(request))
    return {f: getattr(r, f) for f in REQ_FIELDS}


# Last, so /export and /requests match first.
@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    i = db.get(CatalogItem, item_id)
    if i is None or not (p.is_admin or i.status == "published"):
        raise FieldError("item", "No such catalog item.", status_code=404)
    return _out(i)
