"""Ownership checks on top of roles (Claude_Docs/Planning_Web-Interface-Design.md §5)."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Environment, Grant, KvmHost
from .security import Principal


def _principal_filter(p: Principal):
    mine = (Grant.principal_type == "user") & (Grant.principal == p.username)
    if p.subject:           # a grant made for another account with this name isn't mine (question 200)
        mine = mine & (Grant.subject.is_(None) | (Grant.subject == p.subject))
    return or_(
        mine,
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


def account_subject(db: Session, username: str) -> str | None:
    """The subject key of the account with this username, when one has signed in (else the grant is tied at its sign-in)."""
    from .models import User
    from .security import subject_key
    users = list(db.scalars(select(User).where(User.username == username, User.provider != "breakglass")))
    return subject_key(users[0]) if len(users) == 1 else None


def add_owner(db: Session, object_type: str, object_id: int, username: str) -> None:
    db.add(Grant(object_type=object_type, object_id=object_id, principal_type="user", principal=username, level="owner",
                 subject=account_subject(db, username)))
    db.commit()


def visible_hosts(db: Session, p: Principal) -> list[KvmHost]:
    """KVM hosts a user may see (design §22.2): admins all, deployers and
    template editors the enabled ones, everyone else only the hosts of the
    environments they can see."""
    hosts = list(db.scalars(select(KvmHost).order_by(KvmHost.name)))
    if p.is_admin:
        return hosts
    if p.has_role("deployer", "template_editor"):
        return [h for h in hosts if h.enabled]
    ids = visible_ids(db, p, "environment") or set()
    from . import spread
    used = {hid for e in db.scalars(select(Environment).where(Environment.id.in_(ids or {-1}),
                                                              Environment.status != "destroyed"))
            for hid in [e.kvm_host_id, *spread.host_ids(e)]}
    return [h for h in hosts if h.id in used]
