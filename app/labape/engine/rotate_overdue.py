"""Rotate every overdue secret now, one at a time (design §62.1, batch R2).

A person asks for it (Secrets page → Rotate overdue now) after being told that restarting rotations stop
services. The order is fixed: rotations the worker does itself first, then the ones the update agent does
(they restart the API and the worker): PostgreSQL password, CA key password, and the master key last. It stops
at the first failure. The queue is kept in the settings table and driven by `advance()` from the worker's
rotation schedule, so it survives the restarts the agent causes.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import update
from ..models import Job, SecretPolicy, Setting, utcnow
from ..security import audit
from . import rotation
from .rotation import REGISTRY, RotationError, uses_agent

KEY = "rotation_queue"
LATE = dt.timedelta(hours=1)                      # "overdue", as the Secrets page shows it
AGENT_ORDER = ("postgres_password", "ca_password", "master_key")


def _order(db: Session, p: SecretPolicy) -> tuple:
    agent = uses_agent(db, p.kind)
    return (agent, AGENT_ORDER.index(p.kind) if p.kind in AGENT_ORDER else 0, p.id)


def overdue(db: Session, now: dt.datetime | None = None) -> list[SecretPolicy]:
    """The overdue policies, in the order they are rotated; those already being rotated are left out."""
    rows = [p for p in rotation.due(db, now, any_time=True, late=LATE) if p.status != "running"]
    return sorted(rows, key=lambda p: _order(db, p))


def plan(db: Session) -> list[dict]:
    now = utcnow()
    return [{"id": p.id, "label": p.label, "restart": bool(p.needs_restart),
             "late_hours": int((now - rotation._aware(p.next_due_at)).total_seconds() // 3600)}
            for p in overdue(db, now)]


def _load(db: Session) -> dict | None:
    row = db.get(Setting, KEY)
    return dict(row.value) if row is not None and row.value else None


def _save(db: Session, st: dict) -> None:
    row = db.get(Setting, KEY)
    if row is None:
        db.add(Setting(key=KEY, value=st))
    else:
        row.value = st
    db.commit()


def status(db: Session) -> dict | None:
    """The queue as the page shows it: state, who started it, what is done, what is running, what is left."""
    st = _load(db)
    if st is None:
        return None
    names = {p.id: p.label for p in db.scalars(select(SecretPolicy).where(SecretPolicy.id.in_(
        [*st.get("ids", []), *st.get("done", []), *([st["current"]] if st.get("current") else [])] or [0])))}
    return {"state": st["state"], "by": st["by"], "started": st["started"], "note": st.get("note", ""),
            "total": st["total"], "done": [names.get(i, str(i)) for i in st.get("done", [])],
            "current": names.get(st.get("current")), "left": [names.get(i, str(i)) for i in st.get("ids", [])]}


def start(db: Session, actor: str) -> dict:
    st = _load(db)
    if st and st["state"] == "running":
        raise RotationError("a rotation of the overdue secrets is already running")
    ids = [p.id for p in overdue(db)]
    if not ids:
        raise RotationError("nothing is overdue")
    _save(db, {"state": "running", "by": actor, "started": utcnow().isoformat(), "ids": ids, "done": [],
               "current": None, "job": None, "total": len(ids), "note": ""})
    audit(db, actor, "secret.rotate_overdue.start", detail={"secrets": len(ids)})
    return status(db)


def cancel(db: Session, actor: str) -> dict | None:
    st = _load(db)
    if st and st["state"] == "running":
        st.update(state="cancelled", note=f"cancelled by {actor}")
        _save(db, st)                                  # a rotation already handed over finishes on its own
        audit(db, actor, "secret.rotate_overdue.cancel", detail={})
    return status(db)


def _stop(db: Session, st: dict, state: str, note: str) -> None:
    st.update(state=state, note=note, current=None, job=None)
    _save(db, st)
    audit(db, st["by"], "secret.rotate_overdue." + state, outcome="ok" if state == "done" else "failed", detail={"note": note})


def advance(db: Session) -> None:
    """Move the queue on one step when nothing it started is still running. Called every minute."""
    st = _load(db)
    if not st or st["state"] != "running":
        return
    if st.get("current"):
        pol = db.get(SecretPolicy, st["current"])
        job = db.get(Job, st["job"]) if st.get("job") else None
        if job is not None and job.state in ("queued", "running"):
            return
        if pol is not None and pol.status == "running":
            return                                     # the agent is still on it
        if pol is None or pol.status != "ok":
            _stop(db, st, "stopped", f"{pol.label if pol else 'a secret'} failed"
                  + (f": {pol.last_error}" if pol and pol.last_error else "") + "; the rest were not rotated")
            return
        st["done"].append(pol.id)
        st["current"], st["job"] = None, None
    if update.busy():
        return                                         # the update agent is doing something else: wait
    while st["ids"]:
        pid = st["ids"].pop(0)
        pol = db.get(SecretPolicy, pid)
        if pol is None or not pol.enabled or pol.status == "running":
            continue
        try:
            if uses_agent(db, pol.kind):
                rotation.request_agent(db, pol, st["by"])
                st["current"] = pol.id
            else:
                job = Job(type="secret.rotate", params={"policy_id": pol.id}, requested_by=st["by"],
                          environment_id=(pol.params or {}).get("environment_id") if pol.kind in rotation.ENV_KINDS else None)
                db.add(job)
                db.commit()
                st["current"], st["job"] = pol.id, job.id
        except RotationError as exc:
            _stop(db, st, "stopped", f"{pol.label}: {exc}; the rest were not rotated")
            return
        _save(db, st)
        return
    _stop(db, st, "done", "")
