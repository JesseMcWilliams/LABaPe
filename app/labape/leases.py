"""Environment leases (design §40, batch E1; review item 4): environments expire unless extended.

Off until an admin turns it on (Settings → Environment leases), because the end of a lease can
destroy VMs. Then a new environment gets the default lease; existing ones have none until an owner
or admin sets one. Before the end, the owners get reminder emails (by default a week and a day
before). At the end the worker shuts the VMs down (force off after 5 minutes); after the grace
period it destroys the environment, or, if the setting says so, leaves it shut down. Owners extend
a lease up to the longest one allowed from now; admins can also set any date or none. Extending a
lease that already ended starts it again (the VMs stay off until someone starts them).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings
from .models import Environment, Grant, Job, User, utcnow


class LeaseError(Exception):
    pass


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def default_expiry(db: Session, now: dt.datetime | None = None) -> dt.datetime | None:
    cfg = appsettings.get(db, "leases")
    return (now or utcnow()) + dt.timedelta(days=cfg.default_days) if cfg.enabled else None


def phase(e: Environment, db: Session | None = None, now: dt.datetime | None = None) -> str:
    """none | active | expiring (inside the first reminder) | ended (shut down) | destroying."""
    exp = _aware(e.expires_at)
    if exp is None:
        return "none"
    now = now or utcnow()
    st = e.lease_state or {}
    if st.get("destroy_job"):
        return "destroying"
    if now >= exp:
        return "ended"
    if db is not None:
        cfg = appsettings.get(db, "leases")
        if cfg.remind_days and exp - now <= dt.timedelta(days=max(cfg.remind_days)):
            return "expiring"
    return "active"


def out(db: Session, e: Environment) -> dict:
    cfg = appsettings.get(db, "leases")
    exp = _aware(e.expires_at)
    destroy_at = exp + dt.timedelta(days=cfg.grace_days) if exp and cfg.after_grace == "destroy" else None
    return {"enabled": cfg.enabled, "expires_at": exp, "phase": phase(e, db), "destroy_at": destroy_at,
            "max_days": cfg.max_days, "state": e.lease_state or {}}


def set_lease(db: Session, e: Environment, *, days: int | None = None, until: dt.datetime | None = None,
              clear: bool = False, admin: bool = False, now: dt.datetime | None = None) -> None:
    """Owners: `days` more from the later of now and the current end, at most max_days from now.
    Admins: also an exact `until`, or `clear` (no expiry)."""
    cfg = appsettings.get(db, "leases")
    now = now or utcnow()
    limit = now + dt.timedelta(days=cfg.max_days)
    if clear or until is not None:
        if not admin:
            raise LeaseError("Only admins can set an exact end or remove the expiry")
        new = None if clear else _aware(until)
        if new is not None and new <= now:
            raise LeaseError("The new end must be in the future")
    else:
        if not days or days < 1:
            raise LeaseError("Extend by at least a day")
        base = max(now, _aware(e.expires_at) or now)
        new = base + dt.timedelta(days=days)
        if new > limit and not admin:
            raise LeaseError(f"A lease can reach at most {cfg.max_days} days from now ({limit:%Y-%m-%d})")
    e.expires_at = new
    e.lease_state = {}                      # reminders again, and an ended lease starts over
    db.commit()


def owner_emails(db: Session, e: Environment) -> list[str]:
    names = {e.created_by} | set(db.scalars(select(Grant.principal).where(
        Grant.object_type == "environment", Grant.object_id == e.id, Grant.principal_type == "user",
        Grant.level == "owner")))
    return sorted({u.email for u in db.scalars(select(User).where(User.username.in_(names))) if u.email and "@" in u.email})


def _email(db: Session, e: Environment, subject: str, body: str, send=None) -> None:
    from . import notify
    from .config import get_settings
    from .security import get_secret
    smtp = appsettings.get(db, "smtp")
    to = owner_emails(db, e)
    if not (to and smtp.enabled and smtp.host and smtp.from_address):
        return
    app = appsettings.get(db, "branding").app_name
    url = f"{get_settings().public_url.rstrip('/')}/environments/{e.id}"
    (send or notify.email_in_background)(smtp, get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or "", to,
                                         f"{app}: {subject}", f"{body}\n\n{url}\n")


def _busy(db: Session, e: Environment) -> bool:
    return db.scalar(select(Job).where(Job.environment_id == e.id, Job.state.in_(("queued", "running")))) is not None


def tick(db: Session, now: dt.datetime | None = None, send=None) -> list[str]:
    """The worker, every loop (not in maintenance): reminders, shut-downs and destroys that are due.
    Returns what it did, for the log."""
    cfg = appsettings.get(db, "leases")
    if not cfg.enabled:
        return []
    now = now or utcnow()
    done = []
    for e in db.scalars(select(Environment).where(Environment.expires_at.is_not(None),
                                                  Environment.status == "deployed")).all():
        exp = _aware(e.expires_at)
        st = dict(e.lease_state or {})
        if now < exp:
            due = [d for d in sorted(cfg.remind_days, reverse=True) if exp - now <= dt.timedelta(days=d)]
            fresh = [d for d in due if str(d) not in st.get("reminded", [])]
            if fresh:
                left = exp - now
                when = f"{left.days} day(s)" if left.days else f"{int(left.total_seconds() // 3600)} hour(s)"
                _email(db, e, f"{e.name} expires in {when}",
                       f"The environment {e.name} expires on {exp:%Y-%m-%d %H:%M} UTC. Then its VMs are shut down"
                       + (f", and {cfg.grace_days} day(s) later it's destroyed." if cfg.after_grace == "destroy" else ".")
                       + " Extend the lease on its page to keep it.", send)
                st["reminded"] = sorted({*st.get("reminded", []), *[str(d) for d in due]})
                e.lease_state = st
                db.commit()
                done.append(f"reminded the owners of {e.name}")
            continue
        if _busy(db, e):
            continue
        if not st.get("stopped_at"):
            job = Job(type="environment.power", kvm_host_id=e.kvm_host_id, environment_id=e.id, requested_by="lease",
                      params={"environment_id": e.id, "action": "shutdown", "vms": None, "force_after_timeout": True,
                              "timeout_seconds": 300})
            db.add(job)
            db.flush()
            st.update(stopped_at=now.isoformat(), stop_job=job.id)
            e.lease_state = st
            db.commit()
            _email(db, e, f"{e.name} expired: its VMs are shut down",
                   f"The lease of {e.name} ended on {exp:%Y-%m-%d %H:%M} UTC, so its VMs are being shut down (job {job.id})."
                   + (f" It's destroyed on {exp + dt.timedelta(days=cfg.grace_days):%Y-%m-%d} unless the lease is extended."
                      if cfg.after_grace == "destroy" else " Extend the lease to use it again."), send)
            done.append(f"shut down {e.name} (job {job.id})")
        elif cfg.after_grace == "destroy" and now >= exp + dt.timedelta(days=cfg.grace_days) and not st.get("destroy_job"):
            job = Job(type="environment.destroy", params={"environment_id": e.id}, kvm_host_id=e.kvm_host_id,
                      environment_id=e.id, requested_by="lease")
            db.add(job)
            db.flush()
            e.status = "destroying"
            st["destroy_job"] = job.id
            e.lease_state = st
            db.commit()
            _email(db, e, f"{e.name} is being destroyed",
                   f"The lease of {e.name} ended on {exp:%Y-%m-%d} and the {cfg.grace_days}-day grace period is over: "
                   f"it's being destroyed (job {job.id}).", send)
            done.append(f"destroying {e.name} (job {job.id})")
    return done
