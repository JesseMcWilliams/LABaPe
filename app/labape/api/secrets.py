"""Settings > Secrets (design §24, §25): rotation policies, Rotate now, elevated accounts, and the
stored-secret catalog with Move between stores."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import stores
from ..db import get_db
from ..engine.rotation import (ELEVATED_PREFIX, REGISTRY, RESTART_INTERVAL_HOURS, RotationError, ensure_policies,
                               policy_secret, request_agent)
from ..errors import FieldError
from ..models import Job, Secret, SecretPolicy
from ..security import Principal, audit, client_ip, pinned, put_secret, require_roles

router = APIRouter(prefix="/api/admin", tags=["secrets"])
FIELDS = ("id", "name", "kind", "label", "store", "secret_class", "interval_hours", "needs_restart", "enabled",
          "managed_externally", "elevated_ref", "params", "status", "last_rotated_at", "next_due_at", "last_error",
          "notes")


def _last_jobs(db: Session) -> dict[int, Job]:
    out: dict[int, Job] = {}
    for j in db.scalars(select(Job).where(Job.type == "secret.rotate").order_by(Job.id.desc()).limit(500)):
        out.setdefault((j.params or {}).get("policy_id"), j)
    return out


def _out(db: Session, p: SecretPolicy, last_jobs: dict[int, Job] | None = None) -> dict:
    r = REGISTRY.get(p.kind)
    last = (last_jobs if last_jobs is not None else _last_jobs(db)).get(p.id)
    return {**{f: getattr(p, f) for f in FIELDS}, "automatic": bool(r and r.automatic),
            "needs_elevated": bool(r and r.needs_elevated), "rotator_notes": r.notes if r else "",
            "last_job": {"id": last.id, "state": last.state} if last else None, "secret_name": policy_secret(p)}


def _elevated(db: Session) -> list[str]:
    return sorted(n[len(ELEVATED_PREFIX):] for n in db.scalars(select(Secret.name).where(
        Secret.name.startswith(ELEVATED_PREFIX))) if n)


@router.get("/secrets")
def list_policies(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    ensure_policies(db)
    jobs = _last_jobs(db)
    return {"policies": [_out(db, p, jobs) for p in db.scalars(select(SecretPolicy).order_by(SecretPolicy.name))],
            "elevated": _elevated(db), "stores": stores.available(db)}


@router.get("/secrets/{policy_id}")
def get_policy(policy_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    p = db.get(SecretPolicy, policy_id)
    if p is None:
        raise FieldError("policy", "No such secret.", status_code=404)
    return {**_out(db, p), "elevated": _elevated(db), "stores": stores.available(db)}


class PolicyIn(BaseModel):
    interval_hours: int = Field(ge=1, le=24 * 365)
    enabled: bool = True
    managed_externally: bool = False
    store: str = "builtin"
    elevated_ref: str = ""
    params: dict = {}
    notes: str = Field(default="", max_length=4000)


@router.put("/secrets/{policy_id}")
def update_policy(policy_id: int, body: PolicyIn, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("admin"))):
    pol = db.get(SecretPolicy, policy_id)
    if pol is None:
        raise FieldError("policy", "No such secret.", status_code=404)
    if body.elevated_ref and body.elevated_ref not in _elevated(db):
        raise FieldError("elevated_ref", "Add that elevated account first.")
    if pol.needs_restart and body.interval_hours < RESTART_INTERVAL_HOURS and not body.managed_externally:
        raise FieldError("interval_hours", "This rotation restarts services, so it runs at most weekly, in the "
                                           f"maintenance window ({RESTART_INTERVAL_HOURS} hours or more).")
    if body.managed_externally and body.store == "builtin":
        raise FieldError("managed_externally", "Only a secret in an external store can be rotated by that store.")
    if body.store != pol.store:                       # the secret moves first; the policy follows (design §25)
        name = policy_secret(pol)
        if not name or db.get(Secret, name) is None:
            raise FieldError("store", "Nothing is stored for this secret yet, so there's nothing to move. "
                                      "It's created in the built-in store; move it after its first rotation.")
        try:
            stores.move(db, name, body.store, p.username, source_ip=client_ip(request))
        except stores.StoreError as exc:
            raise FieldError("store", str(exc)) from exc
        db.refresh(pol)
    for k, v in body.model_dump(exclude={"store"}).items():
        setattr(pol, k, v)
    db.commit()
    audit(db, p.username, "secret.policy", object_type="secret_policy", object_id=pol.id,
          detail={"name": pol.name, **body.model_dump(exclude={"notes"})}, source_ip=client_ip(request))
    return _out(db, pol)


@router.post("/secrets/{policy_id}/rotate")
def rotate_now(policy_id: int, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    pol = db.get(SecretPolicy, policy_id)
    if pol is None:
        raise FieldError("policy", "No such secret.", status_code=404)
    r = REGISTRY.get(pol.kind)
    if r is None or not r.automatic:
        raise FieldError("policy", f"LABaPe doesn't rotate {pol.label} yet: {r.notes if r else ''}")
    if pol.managed_externally:
        raise FieldError("policy", "Its secret store rotates it; LABaPe only reads it.")
    if r.via_agent:
        try:
            return {"agent_request": request_agent(db, pol, p.username)}
        except RotationError as exc:
            raise FieldError("policy", str(exc), status_code=409) from exc
    busy = db.scalar(select(Job).where(Job.type == "secret.rotate", Job.state.in_(("queued", "running"))))
    if busy and (busy.params or {}).get("policy_id") == pol.id:
        raise FieldError("policy", f"Job {busy.id} is already rotating it.", status_code=409)
    job = Job(type="secret.rotate", params={"policy_id": pol.id}, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "secret.rotate.start", object_type="secret_policy", object_id=pol.id,
          detail={"name": pol.name, "job": job.id}, source_ip=client_ip(request))
    return {"job_id": job.id}


class ElevatedIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    value: str = Field(min_length=8, max_length=8000)
    description: str = Field(default="", max_length=300)


@router.post("/secrets/elevated", status_code=201)
def add_elevated(body: ElevatedIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    """An elevated account's credential, used by rotators that can't rotate their own secret."""
    put_secret(db, ELEVATED_PREFIX + body.name, body.value)
    audit(db, p.username, "secret.elevated", detail={"name": body.name, "description": body.description},
          source_ip=client_ip(request))
    return {"name": body.name}



# --- stored secrets (the catalog, design §25) -------------------------------------

@router.get("/stored-secrets")      # not under /secrets/, where /secrets/{policy_id} would catch it
def list_stored(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    """Every stored secret's name, store and rotation; never a value."""
    ensure_policies(db)
    pols = {p.id: p for p in db.scalars(select(SecretPolicy))}
    out = []
    for row in db.scalars(select(Secret).order_by(Secret.name)):
        pol = pols.get(row.policy_id) if row.policy_id else None
        out.append({"name": row.name, "store": row.store, "updated_at": row.updated_at, "pinned": pinned(row.name),
                    "empty": row.store == "builtin" and not row.value,
                    "rotation": {"policy_id": pol.id, "label": pol.label, "kind": pol.kind, "enabled": pol.enabled,
                                 "interval_hours": pol.interval_hours, "elevated_ref": pol.elevated_ref,
                                 "status": pol.status, "next_due_at": pol.next_due_at} if pol else None})
    return {"secrets": out, "stores": [{"name": n, "display_name": stores.providers()[n].display_name}
                                       for n in stores.available(db)]}


class MoveIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)
    store: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


@router.post("/stored-secrets/move")
def move_stored(body: MoveIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    """Move secrets to another store, one at a time; each either moves or stays put."""
    results = []
    for name in body.names:
        try:
            results.append({"name": name, "ok": True,
                            "message": stores.move(db, name, body.store, p.username, source_ip=client_ip(request))})
        except stores.StoreError as exc:
            db.rollback()
            audit(db, p.username, "secret.move", object_type="secret", object_id=name, outcome="failed",
                  detail={"name": name, "to": body.store, "error": str(exc)}, source_ip=client_ip(request))
            results.append({"name": name, "ok": False, "message": str(exc)})
    return {"results": results, "moved": sum(r["ok"] for r in results)}
