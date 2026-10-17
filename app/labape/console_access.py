"""What an API token may do on a console (design §59, batch X1): the `console` role, the token's
environment list, its limits, and whether it needs an account. Console calls take a bearer token only
(no session), so none of this can be sidestepped from a browser.

The reach of a call is the token's list intersected with what its owner can use: an admin's or a
service token's list is the whole grant, anyone else must also own the environment.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from .permissions import is_owner
from .security import Principal, current_principal

_calls: dict[int, deque] = defaultdict(deque)       # token id -> times of its recent calls (this process)
WINDOW = 60.0


def console_principal(p: Principal = Depends(current_principal)) -> Principal:
    """A console call: an API token holding the console role, never a browser session."""
    if not p.via_token or p.scope is None:
        raise HTTPException(403, "Console calls need an API token (Authorization: Bearer ...)")
    if not p.has_role("console"):
        raise HTTPException(403, "This token doesn't have the console role")
    return p


def rate_limit(p: Principal, now: float | None = None) -> None:
    """Count one call against the token's calls-per-minute limit; 429 over it."""
    limit, tid, now = p.scope["rate_per_minute"], p.scope["token_id"], time.monotonic() if now is None else now
    q = _calls[tid]
    while q and now - q[0] >= WINDOW:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, f"This token is limited to {limit} console calls a minute",
                            headers={"Retry-After": str(max(1, int(WINDOW - (now - q[0]))))})
    q.append(now)


def environment_allowed(db: Session, p: Principal, environment_id: int) -> bool:
    envs = p.scope["environments"]
    if envs is not None and environment_id not in envs:
        return False
    return p.is_admin or p.scope["owner_admin"] or is_owner(db, p, "environment", environment_id)


def check_environment(db: Session, p: Principal, environment_id: int) -> None:
    """404 (not 403) when the token can't reach the environment, so its existence isn't revealed."""
    if not environment_allowed(db, p, environment_id):
        raise HTTPException(404, "No such environment")


def check_account(p: Principal, account: str) -> str:
    if p.scope["require_account"] and not account:
        raise HTTPException(400, "This token must name an account to run as (the account field)")
    return account


def check_host_consoles(p: Principal) -> None:
    """KVM host consoles: only on a token an admin allowed it on, and only while it holds admin."""
    if not (p.scope["host_consoles"] and p.is_admin):
        raise HTTPException(403, "This token can't open KVM host consoles")


def limits(p: Principal) -> dict:
    return {"session_seconds": p.scope["max_session_minutes"] * 60, "output_bytes": p.scope["max_output_kb"] * 1024}
