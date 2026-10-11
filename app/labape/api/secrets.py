"""Settings > Secrets (design §24): rotation policies, Rotate now, elevated accounts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.rotation import ELEVATED_PREFIX, REGISTRY, RESTART_INTERVAL_HOURS, ensure_policies
from ..errors import FieldError
from ..models import Job, Secret, SecretPolicy
from ..security import Principal, audit, client_ip, put_secret, require_roles

router = APIRouter(prefix="/api/admin", tags=["secrets"])
STORES = ("builtin",)                 # OpenBao/Vault and Conjur arrive with phase 10d
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
            "last_job": {"id": last.id, "state": last.state} if last else None}


def _elevated(db: Session) -> list[str]:
    return sorted(n[len(ELEVATED_PREFIX):] for n in db.scalars(select(Secret.name).where(
        Secret.name.startswith(ELEVATED_PREFIX))) if n)


@router.get("/secrets")
def list_policies(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    ensure_policies(db)
    jobs = _last_jobs(db)
    return {"policies": [_out(db, p, jobs) for p in db.scalars(select(SecretPolicy).order_by(SecretPolicy.name))],
            "elevated": _elevated(db), "stores": list(STORES)}


@router.get("/secrets/{policy_id}")
def get_policy(policy_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    p = db.get(SecretPolicy, policy_id)
    if p is None:
        raise FieldError("policy", "No such secret.", status_code=404)
    return {**_out(db, p), "elevated": _elevated(db), "stores": list(STORES)}


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
    if body.store not in STORES:
        raise FieldError("store", f"Stores available: {', '.join(STORES)}.")
    if body.elevated_ref and body.elevated_ref not in _elevated(db):
        raise FieldError("elevated_ref", "Add that elevated account first.")
    if pol.needs_restart and body.interval_hours < RESTART_INTERVAL_HOURS and not body.managed_externally:
        raise FieldError("interval_hours", "This rotation restarts services, so it runs at most weekly, in the "
                                           f"maintenance window ({RESTART_INTERVAL_HOURS} hours or more).")
    for k, v in body.model_dump().items():
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

