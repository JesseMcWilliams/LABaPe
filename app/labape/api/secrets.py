"""Settings > Secrets (design §24, §25): rotation policies, Rotate now, elevated accounts, and the
stored-secret catalog with Move between stores."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import stores
from ..db import get_db
from ..engine.rotation import (ELEVATED_PREFIX, ENV_KINDS, REGISTRY, RESTART_INTERVAL_HOURS, RotationError,
                               ensure_policies, policy_secret, request_agent, uses_agent)
from ..engine import secret_health
from ..errors import FieldError
from ..models import Environment, Job, Secret, SecretPolicy
from ..naming import instance_names
from ..security import Principal, audit, client_ip, get_secret, pinned, put_secret, require_roles

router = APIRouter(prefix="/api/admin", tags=["secrets"])
FIELDS = ("id", "name", "kind", "label", "store", "secret_class", "interval_hours", "needs_restart", "enabled",
          "managed_externally", "elevated_ref", "params", "status", "last_rotated_at", "next_due_at", "last_error",
          "notes",
          "check_status", "check_error", "last_checked_at", "next_check_at")


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
            "last_job": {"id": last.id, "state": last.state} if last else None, "secret_name": policy_secret(p),
            **secret_health.describe(p),
            # The Secrets page's sections (S2): the stack, the KVM hosts, the environments.
            "section": "kvm" if p.kind == "worker_ssh_key" else "environments" if p.kind in ENV_KINDS else "stack",
            "environment_id": (p.params or {}).get("environment_id") if p.kind in ENV_KINDS else None}


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
    if uses_agent(db, pol.kind):
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


@router.post("/secrets/{policy_id}/check")
def check_now(policy_id: int, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    """Check the secret now (S1): does the stored value still work; recovered if it doesn't and can be."""
    pol = db.get(SecretPolicy, policy_id)
    if pol is None:
        raise FieldError("policy", "No such secret.", status_code=404)
    if not secret_health.describe(pol)["checkable"]:
        raise FieldError("policy", f"There's no check for {pol.label}.")
    busy = next((j for j in db.scalars(select(Job).where(Job.type.in_(("secret.check", "secret.rotate")),
                                                          Job.state.in_(("queued", "running"))))
                 if (j.params or {}).get("policy_id") == pol.id), None)
    if busy:
        raise FieldError("policy", f"Job {busy.id} is already working on it.", status_code=409)
    job = Job(type="secret.check", params={"policy_id": pol.id}, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "secret.check.start", object_type="secret_policy", object_id=pol.id,
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

ENV_SECRET = re.compile(r"^environment/(\d+)/(.+)$")
KEYISH = re.compile(r"(^|[/_.])(ssh_key|key|secret_id|api_key|token)(_pub|_history)?$")


def _vms_of(env: Environment, what: str) -> list[str]:
    """The VMs an environment secret belongs to, from the spec (S2)."""
    from .. import env_accounts
    groups = (env.spec or {}).get("host_groups", [])
    vms = [(n, set(g.get("roles") or []), str(g.get("os", ""))) for g in groups for n in instance_names(g)]
    dcs = [n for n, r, _ in vms if "domain_controller" in r]
    kind = what.split("/")[-1]
    if kind in ("windows_admin", "windows_admin2"):
        return [n for n, r, os_ in vms if os_.startswith("windows") and "domain_controller" not in r]
    if kind in ("domain_admin", "domain_admin2"):
        return dcs
    if what.split("/")[0] in ("account",) or kind.startswith("account-"):
        name = kind[len("account-"):] if kind.startswith("account-") else kind
        a = next((x for x in env_accounts.accounts(env) if x["name"] == name), None)
        if a is None:
            return []
        return dcs if a["kind"] == "domain" else [n for n, r, _ in vms if r & set(a.get("hosts") or [])]
    return [n for n, _, _ in vms]


def classify(name: str, envs: dict[int, Environment]) -> dict:
    """Which section of the Secrets page a stored secret belongs to and, for an environment's, the
    environment, its VMs, local or domain, and password, key or document (S2)."""
    m = ENV_SECRET.match(name)
    if m:
        env = envs.get(int(m.group(1)))
        what = m.group(2)
        parts = what.split("/")
        here = {"section": "environments", "environment_id": int(m.group(1)),
                "environment": env.name if env else f"#{m.group(1)} (gone)",
                "environment_status": env.status if env else "gone"}
        if parts[0] == "user":                              # stored by the owners, shared (V1)
            return {**here, "vms": [], "scope": "", "type": "secret", "attempt": False, "owners": True}
        if parts[0] == "uservm" and len(parts) >= 3:          # stored by the owners for one VM (V1)
            return {**here, "vms": [parts[1]], "scope": "", "type": "secret", "attempt": False, "owners": True}
        if parts[0] == "vm" and len(parts) >= 3:          # a VM's own (A2): vm/<vm>/<kind> or vm/<vm>/account/<name>
            rest = "/".join(parts[2:])
            scope = "local"
            if rest.startswith("account/") and env is not None:
                from .. import env_accounts
                a = next((x for x in env_accounts.accounts(env) if x["name"] == rest.removesuffix(".pending").split("/", 1)[1]), None)
                scope = a["kind"] if a else "local"
            return {**here, "vms": [parts[1]], "scope": scope, "type": "password", "attempt": rest.endswith(".pending")}
        if parts[0] == "rotation-candidate" and len(parts) >= 3:      # an attempt for one VM (A2)
            return {**here, "vms": [parts[2]], "scope": "local", "type": "password", "attempt": True}
        kind = parts[-1]
        account = what.startswith("account/") or kind.startswith("account-")
        if kind in ("credentials", "inventory"):
            typ = "document"
        else:
            typ = "password"
        scope = "domain" if kind in ("domain_admin", "domain_admin2") else "local" if kind in ("windows_admin", "windows_admin2") else ""
        if account and env is not None:
            from .. import env_accounts
            aname = kind[len("account-"):] if kind.startswith("account-") else kind
            a = next((x for x in env_accounts.accounts(env) if x["name"] == aname), None)
            scope = a["kind"] if a else ""
        return {"section": "environments", "environment_id": int(m.group(1)),
                "environment": env.name if env else f"#{m.group(1)} (gone)",
                "environment_status": env.status if env else "gone", "vms": _vms_of(env, what) if env else [],
                "scope": scope, "type": typ, "attempt": "rotation-candidate" in what}
    section = "kvm" if name.startswith(("kvm/", "hosts/")) else "stack"
    return {"section": section, "type": "key" if KEYISH.search(name) else "password"}

@router.post("/openbao-people/sync")
def sync_openbao_people(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """Bring OpenBao's per-environment policies and owner groups in step now (V1b, design §49.2)."""
    from .. import openbao_people
    if not openbao_people.enabled(db):
        raise HTTPException(status_code=409, detail="People don't sign in to OpenBao here (stores.sh, LABAPE_OPENBAO_PEOPLE=yes)")
    from .. import openbao_vms
    openbao_vms.forget(db)                          # an admin's sync asks for the VMs' policies again too
    try:
        changes = openbao_people.sync(db)
    except stores.StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit(db, p.username, "openbao.people.sync", detail={"changes": changes[:50]}, source_ip=client_ip(request))
    return {"changes": changes}


@router.get("/stored-secrets")      # not under /secrets/, where /secrets/{policy_id} would catch it
def list_stored(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    """Every stored secret's name, store and rotation; never a value."""
    ensure_policies(db)
    pols = {p.id: p for p in db.scalars(select(SecretPolicy))}
    envs = {e.id: e for e in db.scalars(select(Environment))}
    out = []
    for row in db.scalars(select(Secret).order_by(Secret.name)):
        pol = pols.get(row.policy_id) if row.policy_id else None
        out.append({"name": row.name, "store": row.store, "updated_at": row.updated_at, "pinned": pinned(row.name),
                    **classify(row.name, envs),
                    # A cleared secret is stored as an encrypted "" (spent job tokens, destroyed environments).
                    "empty": row.store == "builtin" and not (get_secret(db, row.name) or ""),
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
