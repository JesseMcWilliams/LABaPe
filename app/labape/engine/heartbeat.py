"""The alert-path check (plan N2, Planning_Disk-Health-Notifications.md).

OZ's mdadm and smartd alerts went nowhere for days and nothing noticed. Once a day the stack has each host send
itself a heartbeat the way mdadm and smartd would: through the host's own mail program to Mailpit, and a line to the
host's syslog. The stack then looks for both. A host that misses two days running raises an alert saying which path is
dead; the syslog path is only judged for hosts the collector has heard from at all.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
import shlex
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, stack_mail
from ..models import KvmHost, Setting
from . import hostadmin

log = logging.getLogger("labape.heartbeat")

EVERY = dt.timedelta(hours=24)
MISSES_BEFORE_ALERT = 2
WAIT_SECONDS = 30
KEY = "heartbeat.host{}"

SCRIPT = r"""
T=%(token)s
SUBJECT="LABaPe heartbeat %(host)s $T"
if command -v mail >/dev/null 2>&1; then
  printf 'Heartbeat %(host)s %(token)s\nSent by the LABaPe stack to check this host mail path.\n' | mail -s "$SUBJECT" root && echo mail=sent || echo mail=failed
elif [ -x /usr/sbin/sendmail ]; then
  printf 'To: root\nSubject: %%s\n\nHeartbeat %(token)s\n' "$SUBJECT" | /usr/sbin/sendmail -t && echo mail=sent || echo mail=failed
else
  echo mail=none
fi
logger -t labape-heartbeat "$T" && echo syslog=sent || echo syslog=failed
"""


def state(db: Session, host_id: int) -> dict:
    row = db.get(Setting, KEY.format(host_id))
    return dict(row.value) if row else {}


def _put(db: Session, host_id: int, value: dict) -> None:
    row = db.get(Setting, KEY.format(host_id))
    if row is None:
        db.add(Setting(key=KEY.format(host_id), value=value))
    else:
        row.value = value
    db.commit()


def mailpit_has(token: str) -> bool:
    r = httpx.get(f"{stack_mail.mailpit_url()}/api/v1/search", params={"query": f'subject:"{token}"'}, timeout=5)
    r.raise_for_status()
    return int(r.json().get("messages_count", 0)) > 0


def syslog_has(token: str, since: dt.datetime) -> bool:
    from .. import syslogstack
    return bool(syslogstack.query(since=since, app="labape-heartbeat", q=token, limit=1))


def syslog_judged(h: KvmHost, now: dt.datetime) -> bool:
    """Only a host the collector has heard from in the last week is expected to reach it."""
    from .. import syslogstack
    if not syslogstack.present():
        return False
    try:
        return bool(syslogstack.query(host=h.name, since=now - dt.timedelta(days=7), limit=1))
    except Exception:  # noqa: BLE001 — a collector that is down says nothing about the host
        return False


def run_host(db: Session, h: KvmHost, now: dt.datetime, wait: float = WAIT_SECONDS, sleep=time.sleep) -> dict:
    """One heartbeat for one host; records and returns the outcome."""
    prev = state(db, h.id)
    token = secrets.token_hex(6)
    out = {"at": now.isoformat(), "mail": "", "syslog": "", "error": ""}
    try:
        r = hostadmin.for_host(h).run("sudo -n sh -c " + shlex.quote(SCRIPT % {"token": token, "host": h.name}), timeout=60)
        sent = dict(line.split("=", 1) for line in r.stdout.decode(errors="replace").split() if "=" in line)
        if r.returncode != 0 and not sent:
            raise hostadmin.AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or f"exit {r.returncode}")
    except Exception as exc:  # noqa: BLE001 — a host we can't reach is the host alerts' business
        out["error"] = str(exc)[:300]
        _put(db, h.id, {**prev, **{k: v for k, v in out.items() if k == "error"}, "checked": now.isoformat()})
        return out
    judged = syslog_judged(h, now) and sent.get("syslog") == "sent"
    deadline, mail_ok, sys_ok = time.monotonic() + wait, sent.get("mail") != "sent", not judged
    while True:
        try:
            mail_ok = mail_ok or mailpit_has(token)
            sys_ok = sys_ok or syslog_has(token, now - dt.timedelta(minutes=5))
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat check for %s: %s", h.name, exc)
            out["error"] = f"couldn't look for the heartbeat: {exc}"[:300]
            _put(db, h.id, {**prev, "error": out["error"], "checked": now.isoformat()})
            return out
        if (mail_ok and sys_ok) or time.monotonic() >= deadline:
            break
        sleep(3)
    mail_state = "none" if sent.get("mail") == "none" else "ok" if sent.get("mail") == "sent" and mail_ok else "lost" if sent.get("mail") == "sent" else "failed"
    out["mail"] = mail_state
    out["syslog"] = "n/a" if not judged else ("ok" if sys_ok else "lost")
    bad = out["mail"] != "ok" or out["syslog"] == "lost"
    misses = (prev.get("misses", 0) + 1) if bad else 0
    _put(db, h.id, {**out, "misses": misses, "checked": now.isoformat(), "last_ok_at": prev.get("last_ok_at") if bad else now.isoformat()})
    return out


def due(db: Session, now: dt.datetime) -> None:
    """Heartbeat every ready host that is due; called from the monitoring tick."""
    if not appsettings.get(db, "monitoring").heartbeat or not stack_mail.available():
        return
    for h in db.scalars(select(KvmHost).where(KvmHost.access_status == "ready").order_by(KvmHost.id)):
        last = state(db, h.id).get("checked")
        if last and now - dt.datetime.fromisoformat(last).astimezone(dt.timezone.utc) < EVERY - dt.timedelta(minutes=2):
            continue
        run_host(db, h, now)


def alerts(db: Session) -> dict[str, tuple[int, str, str]]:
    """For monitoring: hosts whose heartbeat failed on two checks in a row, saying which path is dead."""
    out = {}
    if not appsettings.get(db, "monitoring").heartbeat:
        return out
    for h in db.scalars(select(KvmHost).where(KvmHost.access_status == "ready")):
        s = state(db, h.id)
        if s.get("misses", 0) < MISSES_BEFORE_ALERT:
            continue
        why = []
        if s.get("mail") == "none":
            why.append("it has no mail program, so mdadm and smartd can't send mail")
        elif s.get("mail") in ("lost", "failed"):
            why.append("its own mail didn't reach the stack's Mailpit")
        if s.get("syslog") == "lost":
            why.append("its syslog line didn't reach the collector")
        out[f"monitor:heartbeat:{h.id}"] = (h.id, "warning", f"{h.name}: alerts from this host may not arrive: " + " and ".join(why or ["the heartbeat failed"]))
    return out
