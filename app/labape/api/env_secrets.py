"""An environment's secrets on its own page (V1a, design §49).

Owners (and admins) see every secret of their environment: what LABaPe generates (`accounts`: the
Administrators, the domain admin, the environment's own accounts, the credentials sheet and
inventory) and what owners store themselves, shared (`user`) or for one VM (`vm`). They can reveal
any of them and store, replace or delete their own; LABaPe's generated ones change only through
rotation. Every reveal and change is audited, never with the value. In OpenBao each sits at its
place in the environment's folder (stores/layout.py), which the page shows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import stores
from ..db import get_db
from ..models import Environment, Secret, SecretPolicy
from ..naming import instance_names
from ..permissions import can_see, is_owner
from ..security import Principal, audit, client_ip, current_principal, default_store, get_secret, put_secret
from ..stores import layout

router = APIRouter(prefix="/api/environments", tags=["environment secrets"])

NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


def _owned(db: Session, env_id: int, p: Principal) -> Environment:
    e = db.get(Environment, env_id)
    if e is None or not can_see(db, p, "environment", env_id):
        raise HTTPException(status_code=404, detail="No such environment")
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="An environment's secrets are for its owners and admins only")
    return e


def _vms(e: Environment) -> list[str]:
    return [n for g in (e.spec or {}).get("host_groups", []) for n in instance_names(g)]


def area(rest: str) -> tuple[str, str, str]:
    """(area, vm, short name) of `environment/<id>/<rest>`: user, vm (an owner's, for one VM) or accounts."""
    if rest.startswith(layout.USER):
        return "user", "", rest[len(layout.USER):]
    if rest.startswith(layout.USER_VM):
        vm, _, short = rest[len(layout.USER_VM):].partition("/")
        return "vm", vm, short
    parts = rest.split("/")
    if parts[0] == "vm" and len(parts) >= 3:              # A2: a VM's own generated password
        return "accounts", parts[1], "/".join(parts[2:])
    if parts[0] == "rotation-candidate" and len(parts) >= 3:
        return "accounts", parts[2], rest
    return "accounts", "", rest


@router.get("/{env_id}/secrets")
def list_secrets(env_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """The environment's secrets: names, places and dates, never values."""
    e = _owned(db, env_id, p)
    prefix = f"environment/{e.id}/"
    policies = {pol.id: pol for pol in db.scalars(select(SecretPolicy))}
    out = []
    for row in db.scalars(select(Secret).where(Secret.name.startswith(prefix)).order_by(Secret.name)):
        rest = row.name[len(prefix):]
        where, vm, short = area(rest)
        if rest.endswith(".pending") or rest.startswith("rotation-candidate/"):
            continue                                      # a deploy's or rotation's attempt, not a secret of its own
        pol = policies.get(row.policy_id) if row.policy_id else None
        out.append({"name": rest, "area": where, "vm": vm, "short": short, "store": row.store,
                    "path": layout.store_name(db, row.store, row.name) if row.store != "builtin" else "",
                    "updated_at": row.updated_at, "editable": where in ("user", "vm"),
                    "rotation": pol.label if pol else ""})
    return {"secrets": out, "vms": _vms(e), "folder": f"env/{e.name}/" if _layout_store(db, e) else "",
            "openbao": _openbao(db, e)}


def _openbao(db: Session, e: Environment) -> dict | None:
    """Where the owners reach this environment's folder in OpenBao themselves (V1b), when they can."""
    from .. import appsettings, openbao_people
    from ..security import env_store
    if not openbao_people.enabled(db) or env_store(db, e.id) != "openbao":
        return None
    cfg = appsettings.get(db, "store_openbao", cached=True)
    url = cfg.people_url.rstrip("/")
    return {"url": f"{url}/ui/vault/auth?with=oidc%2F", "address": url, "mount": cfg.mount, "path": f"env/{e.name}",
            "cli": f"BAO_ADDR={url} bao login -method=oidc; bao kv list -mount={cfg.mount} env/{e.name}/accounts"}


def _layout_store(db: Session, e: Environment) -> bool:
    """Whether the environment's secrets are (or a first one would be) in a store with the folder layout."""
    return default_store(db, f"environment/{e.id}/user/x") in layout.LAYOUT_STORES


def _row(db: Session, e: Environment, rest: str) -> Secret:
    row = db.get(Secret, f"environment/{e.id}/{rest}")
    if row is None:
        raise HTTPException(status_code=404, detail="No such secret in this environment")
    return row


class RevealIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    action: str = Field(default="show", pattern=r"^(show|copy)$")


@router.post("/{env_id}/secrets/reveal")
def reveal(env_id: int, body: RevealIn, request: Request, db: Session = Depends(get_db),
           p: Principal = Depends(current_principal)):
    e = _owned(db, env_id, p)
    row = _row(db, e, body.name)
    try:
        value = get_secret(db, row.name)
    except stores.StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit(db, p.username, f"environment.secret.{body.action}", object_type="environment", object_id=e.id,
          detail={"name": body.name}, source_ip=client_ip(request))
    return {"name": body.name, "value": value or ""}


class StoreIn(BaseModel):
    name: str = Field(pattern=NAME)
    vm: str = Field(default="", max_length=63)
    value: str = Field(min_length=1, max_length=64000)


@router.put("/{env_id}/secrets")
def store_secret(env_id: int, body: StoreIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(current_principal)):
    """Store a secret of the owners' own, shared (`user/`) or for one VM, or replace it."""
    e = _owned(db, env_id, p)
    if body.vm and body.vm not in _vms(e):
        raise HTTPException(status_code=422, detail=f"{body.vm} isn't one of this environment's VMs")
    full = layout.user_name(e.id, body.name, body.vm)
    replaced = db.get(Secret, full) is not None
    try:
        put_secret(db, full, body.value)
    except stores.StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rest = full[len(f"environment/{e.id}/"):]
    audit(db, p.username, "environment.secret.replace" if replaced else "environment.secret.store",
          object_type="environment", object_id=e.id, detail={"name": rest}, source_ip=client_ip(request))
    return {"name": rest, "replaced": replaced}


@router.delete("/{env_id}/secrets")
def delete_secret(env_id: int, name: str, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(current_principal)):
    e = _owned(db, env_id, p)
    row = _row(db, e, name)
    if area(name)[0] == "accounts":
        raise HTTPException(status_code=409, detail="LABaPe's own secrets change through rotation, not here")
    if row.store != "builtin":
        try:
            stores.ext_delete(db, row.store, row.name)
        except stores.StoreError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.delete(row)
    db.commit()
    audit(db, p.username, "environment.secret.delete", object_type="environment", object_id=e.id,
          detail={"name": name}, source_ip=client_ip(request))
    return {"deleted": name}
