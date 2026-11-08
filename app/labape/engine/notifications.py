"""Notification channels and rules (plan N1, Planning_Disk-Health-Notifications.md).

The built-in email list and syslog switch (Settings -> Monitoring) keep working as before. On top of them, an
admin adds channels (email, webhook, ntfy, syslog) and rules (level, hosts, categories, quiet hours, reminders).
`run` is called every monitoring tick: it sends each announced, open alert once per rule, repeats it as a
reminder when the rule asks, holds it through quiet hours, and sends the all-clear for alerts it sent.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import urllib.request
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, notify
from ..config import get_settings
from ..models import MonitorAlert, NotificationChannel, NotificationLog, NotificationRule, utcnow
from ..security import get_secret, put_secret

log = logging.getLogger("labape.notifications")

KINDS = ("email", "webhook", "ntfy", "syslog")
SHAPES = ("generic", "slack", "teams", "discord")
CATEGORIES = ("disk", "raid", "container", "certificate", "backup", "secret", "stack", "alert-path")
LOG_KEEP = 100
HTTP_TIMEOUT = 10

_KIND_OF_KEY = {"fs": "disk", "temp": "disk", "container": "container", "restart": "container", "cert": "certificate",
                "backup": "backup", "stack-backup": "backup", "stack-backup-check": "backup", "peer": "stack"}


def secret_name(channel_id: int) -> str:
    return f"notify/channel-{channel_id}"


def category(key: str, message: str) -> str:
    """Which kind of trouble an alert is, from its key (`host3:fs:...`) and, failing that, its words."""
    if key.startswith("monitor:"):
        return "alert-path"
    if key.startswith("secret:"):
        return "secret"
    if key.startswith("link:"):
        return "stack"
    m = re.match(r"(?:host\d+|stack):([a-z-]+):", key)
    if m and m.group(1) in _KIND_OF_KEY:
        return _KIND_OF_KEY[m.group(1)]
    low = message.lower()
    if re.search(r"raid array|zfs pool|volume group|\bmd\d+", low):
        return "raid"
    if re.search(r"smart|sector|self-test|/dev/|°c|% full", low):
        return "disk"
    if "container" in low:
        return "container"
    if "certificate" in low:
        return "certificate"
    if "backup" in low:
        return "backup"
    return "stack"


def in_quiet(rule: NotificationRule, now: dt.datetime) -> bool:
    if not (rule.quiet_start and rule.quiet_end) or rule.quiet_start == rule.quiet_end:
        return False
    t = now.strftime("%H:%M")
    if rule.quiet_start < rule.quiet_end:
        return rule.quiet_start <= t < rule.quiet_end
    return t >= rule.quiet_start or t < rule.quiet_end              # spans midnight


def matches(rule: NotificationRule, a: MonitorAlert) -> bool:
    if rule.min_level == "error" and a.level != "error":
        return False
    if rule.host_ids and a.kvm_host_id not in rule.host_ids:
        return False
    return not rule.categories or category(a.key, a.message) in rule.categories


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# --- channel senders ----------------------------------------------------------------------------------------

def _post(url: str, body: bytes, headers: dict) -> None:
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("the address must start with http:// or https://")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:       # noqa: S310 — scheme checked above
        r.read(2048)


def webhook_body(shape: str, title: str, text: str, lines: list[str]) -> dict:
    if shape == "slack":
        return {"text": f"*{title}*\n{text}"}
    if shape == "discord":
        return {"content": f"**{title}**\n{text}"[:2000]}
    if shape == "teams":
        return {"@type": "MessageCard", "@context": "http://schema.org/extensions", "summary": title, "title": title, "text": text.replace("\n", "<br>")}
    return {"source": "labape", "title": title, "text": text, "alerts": lines}


def send(db: Session, ch: NotificationChannel, title: str, lines: list[str]) -> None:
    """Send one message through a channel; raises on failure."""
    text = "\n".join(lines)
    if ch.kind == "email":
        smtp = appsettings.get(db, "smtp")
        if not (smtp.enabled and smtp.host and smtp.from_address):
            raise ValueError("Settings -> Email is off")
        notify.send_email(smtp, get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or "", list(ch.config.get("to") or []),
                          title, f"{text}\n\nDashboard: {get_settings().public_url}\n")
    elif ch.kind == "webhook":
        url = get_secret(db, secret_name(ch.id))
        if not url:
            raise ValueError("no address is set")
        body = webhook_body(ch.config.get("shape", "generic"), title, text, lines)
        _post(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    elif ch.kind == "ntfy":
        server = (ch.config.get("server") or "https://ntfy.sh").rstrip("/")
        headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if any("[ERROR]" in x for x in lines) else "default"}
        token = get_secret(db, secret_name(ch.id)) if ch.secret_set else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        _post(f"{server}/{ch.config.get('topic', '')}", text.encode(), headers)
    elif ch.kind == "syslog":
        sc = appsettings.get(db, "syslog")
        if not (sc.enabled and sc.host):
            raise ValueError("Settings -> Syslog is off")
        for line in lines:
            notify.audit_to_syslog(sc, {"action": "monitor.notification", "actor": "labape-monitor",
                                        "outcome": "failed" if "[ERROR]" in line else "denied", "detail": {"title": title, "message": line}})
    else:
        raise ValueError(f"unknown channel kind {ch.kind}")


def deliver(db: Session, ch: NotificationChannel, rule_name: str, title: str, lines: list[str], now: dt.datetime) -> bool:
    """`send` that records the outcome in the channel and the log; never raises."""
    error = ""
    try:
        send(db, ch, title, lines)
    except Exception as exc:  # noqa: BLE001 — a broken channel must not stop the others
        error = (str(exc) or exc.__class__.__name__)[:500]
        log.warning("notification channel %s failed: %s", ch.name, error)
    ch.last_ok, ch.last_error, ch.last_at = not error, error, now
    db.add(NotificationLog(at=now, channel_id=ch.id, channel_name=ch.name, rule_name=rule_name, ok=not error,
                           error=error, summary=f"{title}: {lines[0][:150]}" + (f" (+{len(lines) - 1} more)" if len(lines) > 1 else "")))
    db.commit()
    old = db.scalars(select(NotificationLog.id).order_by(NotificationLog.id.desc()).offset(LOG_KEEP)).all()
    if old:
        db.query(NotificationLog).filter(NotificationLog.id.in_(old)).delete(synchronize_session=False)
        db.commit()
    return not error


# --- the tick -----------------------------------------------------------------------------------------------

def _title() -> str:
    return f"LABaPe {urlsplit(get_settings().public_url).hostname or ''}".strip()


def run(db: Session, now: dt.datetime, cleared: list[MonitorAlert], silenced_keys: set[str] = frozenset()) -> int:
    """Send what the rules call for: new alerts, reminders, all-clears. Returns how many messages went."""
    rules = list(db.scalars(select(NotificationRule).where(NotificationRule.enabled.is_(True))))
    if not rules:
        return 0
    channels = {c.id: c for c in db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True)))}
    open_ = list(db.scalars(select(MonitorAlert).where(MonitorAlert.cleared_at.is_(None), MonitorAlert.announced_at.is_not(None),
                                                       MonitorAlert.acked_at.is_(None))))
    sent_to: set[tuple[int, int]] = set()                  # (channel, alert) once per tick, however many rules match
    count = 0
    for rule in rules:
        if in_quiet(rule, now):
            continue
        items: list[tuple[MonitorAlert, str]] = []
        for a in open_:
            if a.key in silenced_keys or not matches(rule, a) or _aware(a.announced_at) < _aware(rule.created_at):
                continue
            last = (a.reminded or {}).get(str(rule.id))
            if last is None:
                items.append((a, "new"))
            elif rule.reminder_hours and now - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=rule.reminder_hours):
                items.append((a, "reminder"))
        if rule.send_cleared:
            items += [(a, "cleared") for a in cleared if str((a.reminded or {}).get(str(rule.id), "")) and matches(rule, a)]
        if not items:
            continue
        lines = [f"[{a.level.upper()}] {a.message}" if what == "new" else
                 f"[REMINDER, open since {_aware(a.started_at).strftime('%Y-%m-%d %H:%M')} UTC] {a.message}" if what == "reminder" else
                 f"[CLEARED] {a.message}" for a, what in items]
        title = f"{_title()}: " + ", ".join(f"{n} {w}" for w in ("new", "reminder", "cleared")
                                            if (n := sum(1 for _, x in items if x == w)))
        for cid in rule.channel_ids:
            ch = channels.get(cid)
            if ch is None:
                continue
            fresh = [(a, w, ln) for (a, w), ln in zip(items, lines) if (cid, a.id) not in sent_to]
            if not fresh:
                continue
            deliver(db, ch, rule.name, title, [ln for _, _, ln in fresh], now)
            sent_to.update((cid, a.id) for a, _, _ in fresh)
            count += 1
        for a, what in items:
            if what != "cleared":
                a.reminded = {**(a.reminded or {}), str(rule.id): now.isoformat()}
        db.commit()
    return count


def alerts(db: Session) -> dict[str, tuple[None, str, str]]:
    """For monitoring: a channel whose last send failed (it is how you learn a webhook went stale)."""
    return {f"monitor:channel:{c.id}": (None, "warning", f"notification channel {c.name} can't deliver: {c.last_error[:300]}")
            for c in db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True), NotificationChannel.last_ok.is_(False)))}


# --- used by the API ----------------------------------------------------------------------------------------

def set_secret(db: Session, ch: NotificationChannel, value: str) -> None:
    put_secret(db, secret_name(ch.id), value)
    ch.secret_set = True
    db.commit()


def public(ch: NotificationChannel) -> dict:
    return {"id": ch.id, "name": ch.name, "kind": ch.kind, "enabled": ch.enabled, "config": ch.config, "secret_set": ch.secret_set,
            "last_ok": ch.last_ok, "last_error": ch.last_error, "last_at": ch.last_at}


def test_message(db: Session, ch: NotificationChannel, who: str) -> tuple[bool, str]:
    now = utcnow()
    ok = deliver(db, ch, "(test)", f"{_title()}: test", [f"[TEST] A test message from {who}. If you can read this, the channel works."], now)
    return ok, "" if ok else ch.last_error
