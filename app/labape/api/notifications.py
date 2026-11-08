"""Admin -> Notifications (plan N1): channels, rules and the delivery log for alerts.

The built-in email list and syslog switch (Settings -> Monitoring) stay as they are and are shown read-only at the top
of the page; channels and rules add to them. A webhook address or ntfy token is write-only: it goes to the platform
secret store and the page only says one is set.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..db import get_db
from ..engine import notifications as N
from ..errors import FieldError
from ..models import KvmHost, NotificationChannel, NotificationLog, NotificationRule
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(tags=["notifications"])
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str
    enabled: bool = True
    to: list[str] = []                      # email
    shape: str = "generic"                  # webhook
    server: str = ""                        # ntfy
    topic: str = ""                         # ntfy
    secret: str = ""                        # webhook address or ntfy token; empty keeps the one set


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    min_level: str = "warning"
    host_ids: list[int] = []
    categories: list[str] = []
    channel_ids: list[int] = Field(default=[], min_length=1)
    quiet_start: str = ""
    quiet_end: str = ""
    reminder_hours: int = Field(default=0, ge=0, le=24 * 30)
    send_cleared: bool = True
    send_digest: bool = False


def _config(body: ChannelIn, secret_set: bool) -> dict:
    if body.kind not in N.KINDS:
        raise FieldError("kind", f"Choose one of {', '.join(N.KINDS)}.")
    if body.kind == "email":
        bad = [a for a in body.to if not re.fullmatch(r"[^@\s]+@[^@\s]+", a.strip())]
        if not body.to or bad:
            raise FieldError("to", "Give at least one email address" + (f"; not an address: {bad[0]}." if bad else "."))
        return {"to": [a.strip() for a in body.to]}
    if body.kind == "webhook":
        if body.shape not in N.SHAPES:
            raise FieldError("shape", f"Choose one of {', '.join(N.SHAPES)}.")
        if body.secret:
            u = urlsplit(body.secret)
            if u.scheme not in ("http", "https") or not u.hostname:
                raise FieldError("secret", "The address must start with http:// or https://.")
        elif not secret_set:
            raise FieldError("secret", "Give the webhook address.")
        return {"shape": body.shape, "host": (urlsplit(body.secret).hostname or "") if body.secret else None}
    if body.kind == "ntfy":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body.topic):
            raise FieldError("topic", "A topic is letters, digits, - and _ (up to 64).")
        if body.server and urlsplit(body.server).scheme not in ("http", "https"):
            raise FieldError("server", "The server must start with http:// or https://.")
        return {"server": body.server.strip(), "topic": body.topic}
    return {}


def _get(db: Session, cid: int) -> NotificationChannel:
    ch = db.get(NotificationChannel, cid)
    if ch is None:
        raise FieldError("id", "No such channel.", 404)
    return ch


def _rule(db: Session, body: RuleIn, rule: NotificationRule | None) -> NotificationRule:
    if body.min_level not in ("warning", "error"):
        raise FieldError("min_level", "Choose warning or error.")
    bad = [c for c in body.categories if c not in N.CATEGORIES]
    if bad:
        raise FieldError("categories", f"Unknown category {bad[0]}.")
    have = set(db.scalars(select(NotificationChannel.id)))
    if any(c not in have for c in body.channel_ids):
        raise FieldError("channel_ids", "Choose existing channels.")
    for f in ("quiet_start", "quiet_end"):
        if getattr(body, f) and not HHMM.match(getattr(body, f)):
            raise FieldError(f, "Use the form HH:MM (24-hour, UTC).")
    if bool(body.quiet_start) != bool(body.quiet_end):
        raise FieldError("quiet_end", "Give both ends of the quiet hours, or neither.")
    dupe = db.scalar(select(NotificationRule).where(NotificationRule.name == body.name))
    if dupe and (rule is None or dupe.id != rule.id):
        raise FieldError("name", "A rule with that name exists.")
    rule = rule or NotificationRule()
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    return rule


def _view(db: Session) -> dict:
    mon = appsettings.get(db, "monitoring")
    return {
        "builtin": {"email_to": mon.email_to, "syslog": mon.syslog},
        "kinds": N.KINDS, "shapes": N.SHAPES, "categories": N.CATEGORIES,
        "channels": [N.public(c) for c in db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))],
        "rules": [{"id": r.id, "name": r.name, "enabled": r.enabled, "min_level": r.min_level, "host_ids": r.host_ids,
                   "categories": r.categories, "channel_ids": r.channel_ids, "quiet_start": r.quiet_start, "quiet_end": r.quiet_end,
                   "reminder_hours": r.reminder_hours, "send_cleared": r.send_cleared, "send_digest": r.send_digest}
                  for r in db.scalars(select(NotificationRule).order_by(NotificationRule.name))],
        "hosts": [{"id": h.id, "name": h.name} for h in db.scalars(select(KvmHost).order_by(KvmHost.name))],
        "log": [{"at": e.at, "channel": e.channel_name, "rule": e.rule_name, "ok": e.ok, "error": e.error, "summary": e.summary}
                for e in db.scalars(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(N.LOG_KEEP))],
    }


@router.get("/api/admin/notifications")
def overview(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return _view(db)


@router.post("/api/admin/notifications/channels")
def add_channel(body: ChannelIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    if db.scalar(select(NotificationChannel).where(NotificationChannel.name == body.name)):
        raise FieldError("name", "A channel with that name exists.")
    cfg = _config(body, False)
    ch = NotificationChannel(name=body.name, kind=body.kind, enabled=body.enabled, config={k: v for k, v in cfg.items() if v is not None})
    db.add(ch)
    db.commit()
    if body.secret:
        N.set_secret(db, ch, body.secret)
    audit(db, p.username, "notification.channel.add", object_type="notification_channel", object_id=ch.id,
          detail={"name": ch.name, "kind": ch.kind}, source_ip=client_ip(request))
    return _view(db)


@router.put("/api/admin/notifications/channels/{cid}")
def edit_channel(cid: int, body: ChannelIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    ch = _get(db, cid)
    if body.kind != ch.kind:
        raise FieldError("kind", "A channel's kind can't change; add a new channel.")
    dupe = db.scalar(select(NotificationChannel).where(NotificationChannel.name == body.name))
    if dupe and dupe.id != ch.id:
        raise FieldError("name", "A channel with that name exists.")
    cfg = _config(body, ch.secret_set)
    ch.config = {**ch.config, **{k: v for k, v in cfg.items() if v is not None}}
    ch.name, ch.enabled = body.name, body.enabled
    db.commit()
    if body.secret:
        N.set_secret(db, ch, body.secret)
    audit(db, p.username, "notification.channel.edit", object_type="notification_channel", object_id=ch.id,
          detail={"name": ch.name, "secret_changed": bool(body.secret)}, source_ip=client_ip(request))
    return _view(db)


@router.delete("/api/admin/notifications/channels/{cid}")
def delete_channel(cid: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    ch = _get(db, cid)
    used = [r.name for r in db.scalars(select(NotificationRule)) if cid in r.channel_ids]
    if used:
        raise FieldError("id", f"Rule {used[0]} still sends to this channel; change the rule first.", 409)
    name = ch.name
    db.delete(ch)
    db.commit()
    audit(db, p.username, "notification.channel.delete", object_type="notification_channel", object_id=cid, detail={"name": name},
          source_ip=client_ip(request))
    return _view(db)


@router.post("/api/admin/notifications/channels/{cid}/test")
def test_channel(cid: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    ch = _get(db, cid)
    ok, error = N.test_message(db, ch, p.username)
    audit(db, p.username, "notification.channel.test", object_type="notification_channel", object_id=cid,
          detail={"name": ch.name, "error": error}, outcome="ok" if ok else "failed", source_ip=client_ip(request))
    return {"ok": ok, "error": error, **_view(db)}


@router.post("/api/admin/notifications/rules")
def add_rule(body: RuleIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    rule = _rule(db, body, None)
    db.add(rule)
    db.commit()
    audit(db, p.username, "notification.rule.add", object_type="notification_rule", object_id=rule.id, detail={"name": rule.name},
          source_ip=client_ip(request))
    return _view(db)


@router.put("/api/admin/notifications/rules/{rid}")
def edit_rule(rid: int, body: RuleIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    rule = db.get(NotificationRule, rid)
    if rule is None:
        raise FieldError("id", "No such rule.", 404)
    _rule(db, body, rule)
    db.commit()
    audit(db, p.username, "notification.rule.edit", object_type="notification_rule", object_id=rid, detail={"name": rule.name},
          source_ip=client_ip(request))
    return _view(db)


@router.delete("/api/admin/notifications/rules/{rid}")
def delete_rule(rid: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    rule = db.get(NotificationRule, rid)
    if rule is None:
        raise FieldError("id", "No such rule.", 404)
    name = rule.name
    db.delete(rule)
    db.commit()
    audit(db, p.username, "notification.rule.delete", object_type="notification_rule", object_id=rid, detail={"name": name},
          source_ip=client_ip(request))
    return _view(db)
