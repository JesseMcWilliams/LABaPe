"""Ownership checks on top of roles (Claude_Docs/Planning_Web-Interface-Design.md §5)."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Grant
from .security import Principal


def _principal_filter(p: Principal):
    return or_(
        (Grant.principal_type == "user") & (Grant.principal == p.username),
        (Grant.principal_type == "group") & (Grant.principal.in_(p.groups or ["\x00"])),
    )


def grant_levels(db: Session, p: Principal, object_type: str, object_id: int) -> set[str]:
    rows = db.scalars(
        select(Grant.level).where(Grant.object_type == object_type, Grant.object_id == object_id, _principal_filter(p))
    )
    return set(rows)


def is_owner(db: Session, p: Principal, object_type: str, object_id: int) -> bool:
    return p.is_admin or "owner" in grant_levels(db, p, object_type, object_id)


def can_see(db: Session, p: Principal, object_type: str, object_id: int) -> bool:
    return p.is_admin or bool(grant_levels(db, p, object_type, object_id))


def visible_ids(db: Session, p: Principal, object_type: str) -> set[int] | None:
    """IDs of objects the principal holds any grant on; None means all (admin)."""
    if p.is_admin:
        return None
    return set(db.scalars(select(Grant.object_id).where(Grant.object_type == object_type, _principal_filter(p))))


def add_owner(db: Session, object_type: str, object_id: int, username: str) -> None:
    db.add(Grant(object_type=object_type, object_id=object_id, principal_type="user", principal=username, level="owner"))
    db.commit()
