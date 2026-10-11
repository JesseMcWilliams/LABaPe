"""Maintenance mode (design §23.3): writes stop, reads and status go on.

One state row (`maintenance.state` in settings), set by an admin, by the
update agent, a rollback or a restore, or by a secret rotation that needs
a restart. While it's on, the API refuses changes with 503 except the
ALLOWED paths, and the worker claims no new jobs (running ones finish).
A scheduled maintenance turns on at its start; turning it off is always
explicit, since only whoever did the work knows it's done.
"""
from __future__ import annotations

import datetime as dt
import re
import time

from sqlalchemy.orm import Session

from .models import Setting, utcnow

KEY = "maintenance.state"
CACHE_SECONDS = 5
# Writes that stay open: signing in and out, ending maintenance, update and
# rollback controls, cancelling a job, running jobs finishing their work, and
# the browser's blocked-form reports.
ALLOWED = [re.compile(p) for p in (
    r"^/api/auth/",
    r"^/api/admin/maintenance$",
    r"^/api/admin/update",
    r"^/api/jobs/\d+/cancel$",
    r"^/api/engine/certificates$",
    r"^/api/errors/report$",
)]
EMPTY = {"active": False, "reason": "", "source": "", "started_by": "", "started_at": None, "expected_end": None,
         "scheduled_start": None}

_cache: tuple[float, dict] | None = None


def get(db: Session, cached: bool = False) -> dict:
    global _cache
    if cached and _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    row = db.get(Setting, KEY)
    state = {**EMPTY, **(row.value if row else {})}
    _cache = (time.monotonic(), state)
    return state


def _put(db: Session, state: dict) -> dict:
    global _cache
    row = db.get(Setting, KEY)
    if row is None:
        db.add(Setting(key=KEY, value=state))
    else:
        row.value = state
    db.commit()
    _cache = None
    return state


def turn_on(db: Session, actor: str, reason: str, source: str = "admin", expected_end: str | None = None) -> dict:
    return _put(db, {**EMPTY, "active": True, "reason": reason, "source": source, "started_by": actor,
                     "started_at": utcnow().isoformat(), "expected_end": expected_end})


def schedule(db: Session, actor: str, reason: str, start: str, expected_end: str | None = None) -> dict:
    return _put(db, {**EMPTY, "reason": reason, "source": "admin", "started_by": actor, "scheduled_start": start,
                     "expected_end": expected_end})


def turn_off(db: Session) -> dict:
    return _put(db, dict(EMPTY))


def allowed(path: str) -> bool:
    return any(p.match(path) for p in ALLOWED)


def start_if_due(db: Session, now: dt.datetime | None = None) -> bool:
    """The worker turns a scheduled maintenance on at its start time."""
    state = get(db)
    if state["active"] or not state.get("scheduled_start"):
        return False
    start = dt.datetime.fromisoformat(state["scheduled_start"])
    start = start if start.tzinfo else start.replace(tzinfo=dt.timezone.utc)
    if (now or utcnow()) < start:
        return False
    _put(db, {**state, "active": True, "started_at": (now or utcnow()).isoformat(), "scheduled_start": None})
    return True
