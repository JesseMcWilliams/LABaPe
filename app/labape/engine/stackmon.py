"""Monitoring the hosts that run a LABaPe stack, and the stacks watching each other (design §30,
questions 142-145).

Every host has a role: kvm, stack or both. For a host that runs a stack, with its access account
set up (Set up access, question 145), the worker reads its containers (state, health, restart
count) every 5 minutes and its volume sizes hourly, on top of the host facts and metrics every
host gets (design §28.4). For the host this stack runs on, it also checks its own backups,
certificates and database sizes. Every other stack it knows (a host with a stack address) is
probed every minute: does `/api/health` answer, how fast, and when does its certificate expire.

Everything that's wrong becomes an alert with a stable key (numbers in the message don't change
the key), recorded when it starts and when it clears, shown on the dashboard and announced to
syslog and by email (question 143).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import appsettings, maintenance_mode
from ..config import get_settings
from ..models import HostFacts, HostMetric, KvmHost, MonitorAlert, MonitorSilence, Setting, StackStatus, utcnow
from . import hostadmin

log = logging.getLogger("labape.stackmon")

CONTAINERS_EVERY = dt.timedelta(minutes=5)
VOLUMES_EVERY = dt.timedelta(hours=1)
RESTART_WINDOW = dt.timedelta(minutes=15)
# Alert emails (design §31): the last send's result, and how often a failing server is re-checked.
EMAIL_STATE_KEY = "monitor.email_state"
EMAIL_KEY = "monitor:email"
EMAIL_RECHECK = dt.timedelta(minutes=5)
STACK_ROLES = ("stack", "both")
# The containers that are LABaPe's (compose projects): others on the host aren't its business.
PROJECTS = ("labape", "authentik-test")

STACK_SCRIPT = r'''
import json, shutil, subprocess
def sh(*a, timeout=120):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""
rt = "docker" if shutil.which("docker") else ("podman" if shutil.which("podman") else "")
out = {"runtime": rt, "containers": [], "volumes": None}
if rt:
    names = sh(rt, "ps", "-a", "--format", "{{.Names}}").split()
    for c in (json.loads(sh(rt, "inspect", *names) or "[]") if names else []):
        st = c.get("State") or {}
        labels = (c.get("Config") or {}).get("Labels") or {}
        out["containers"].append({"name": c.get("Name", "").lstrip("/"), "image": (c.get("Config") or {}).get("Image", ""),
                                  "project": labels.get("com.docker.compose.project", ""),
                                  "state": st.get("Status", ""), "health": (st.get("Health") or {}).get("Status", ""),
                                  "restarts": c.get("RestartCount", 0), "started_at": st.get("StartedAt", "")})
    if ARGS.get("volumes"):
        vols = []
        for line in sh(rt, "system", "df", "-v", "--format", "{{json .}}", timeout=600).splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for v in d.get("Volumes") or []:
                vols.append({"name": v.get("Name", ""), "size": v.get("Size", ""), "links": v.get("Links", 0)})
        out["volumes"] = vols
print(json.dumps(out))
'''

_lock = threading.Lock()


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t.replace(tzinfo=dt.timezone.utc) if t is not None and t.tzinfo is None else t


def runs_stack(h: KvmHost) -> bool:
    return h.role in STACK_ROLES


def is_self(h: KvmHost) -> bool:
    """The host this stack runs on: its stack address names this stack's own host, or it has no
    address and is the local libvirt host."""
    if not runs_stack(h):
        return False
    me = urlsplit(get_settings().public_url).hostname or ""
    if h.stack_url:
        return (urlsplit(h.stack_url).hostname or "") == me
    return h.libvirt_uri == "qemu:///system"


def _status(db: Session, h: KvmHost) -> StackStatus:
    return db.get(StackStatus, h.id) or StackStatus(kvm_host_id=h.id, data={})


# --- what's read -----------------------------------------------------------------------------

def collect_containers(db: Session, h: KvmHost, volumes: bool, now: dt.datetime | None = None) -> dict:
    """Containers (and, hourly, volumes) over the access account; restart increases remembered."""
    now = now or utcnow()
    row = _status(db, h)
    data = dict(row.data or {})
    got = hostadmin.for_host(h).python(STACK_SCRIPT, {"volumes": volumes}, timeout=900)
    before = {c["name"]: c for c in (data.get("containers") or [])}
    seen = dict(data.get("restart_seen") or {})
    got["containers"] = [c for c in got.get("containers", []) if c.get("project") in PROJECTS]
    for c in got.get("containers", []):
        old = before.get(c["name"])
        if old is not None and c["restarts"] > old.get("restarts", 0):
            seen[c["name"]] = now.isoformat()
    data.update(runtime=got.get("runtime", ""), containers=got.get("containers", []), restart_seen=seen,
                containers_at=now.isoformat())
    if got.get("volumes") is not None:
        data.update(volumes=got["volumes"], volumes_at=now.isoformat())
    row.data, row.checked_at, row.error = data, now, ""
    db.merge(row)
    db.commit()
    return data


def _file_cert(path: Path):
    from cryptography import x509
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return None


def served_cert(url: str, timeout: float = 10):
    """The TLS certificate served at `url` (not verified: a peer's own CA)."""
    from cryptography import x509
    u = urlsplit(url)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((u.hostname, u.port or 443), timeout=timeout) as raw, \
            ctx.wrap_socket(raw, server_hostname=u.hostname) as s:
        der = s.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der) if der else None


def _validity(cert) -> dict:
    return {"issued": cert.not_valid_before_utc.isoformat(), "expires": cert.not_valid_after_utc.isoformat()} if cert else {}


def cert_due(c: dict, now: dt.datetime, days: int) -> bool:
    """Less left than `days`, or than a quarter of its lifetime for short-lived, auto-renewed
    certificates (the web ones from the platform CA last 24 hours; Caddy renews at a third left)."""
    if not c.get("expires"):
        return False
    exp = dt.datetime.fromisoformat(c["expires"])
    limit = dt.timedelta(days=days)
    if c.get("issued"):
        limit = min(limit, (exp - dt.datetime.fromisoformat(c["issued"])) / 4)
    return exp - now < limit


def self_checks(db: Session) -> dict:
    """This stack's own: newest backup, certificate expiry, database sizes."""
    s = get_settings()
    out: dict = {"certs": {}, "databases": {}}
    cfg = appsettings.get(db, "backup")
    d = Path(cfg.directory)
    newest = max((p.stat().st_mtime for p in d.glob("*") if p.is_file()), default=None) if d.is_dir() else None
    out["backup"] = {"enabled": cfg.enabled,
                     "newest": dt.datetime.fromtimestamp(newest, dt.timezone.utc).isoformat() if newest else None}
    from . import stackbackup
    sb = appsettings.get(db, "stack_backup")
    last = stackbackup.newest(db)
    out["stack_backup"] = {"enabled": sb.enabled, "newest": last.get("created_at") if last else None,
                           "archive": last.get("archive") if last else None,
                           "ok": bool(last and (last.get("check") or {}).get("ok"))}
    for name, fn in (("web", lambda: served_cert(s.public_url)),
                     ("ca intermediate", lambda: _file_cert(s.ca_root.with_name("intermediate_ca.crt"))),
                     ("syslog collector", lambda: _file_cert(s.syslog_tls_dir / "server.crt"))):
        try:
            cert = fn()
        except (OSError, ssl.SSLError) as exc:
            out["certs"][name] = {"error": str(exc)[:200]}
            continue
        if cert is not None:
            out["certs"][name] = _validity(cert)
    try:
        out["databases"]["labape"] = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
    except Exception:  # noqa: BLE001 — SQLite in tests
        pass
    from .. import syslogstack
    if syslogstack.present():
        try:
            with syslogstack.engine().connect() as c:
                out["databases"]["syslog"] = c.execute(text("SELECT pg_database_size(current_database())")).scalar()
        except Exception:  # noqa: BLE001
            pass
    return out


def probe_peer(url: str, timeout: float = 10) -> dict:
    """Does another stack's /api/health answer, how fast, and its certificate's expiry."""
    import httpx
    t0 = time.monotonic()
    try:
        r = httpx.get(url.rstrip("/") + "/api/health", verify=False, timeout=timeout)
        ok = r.status_code == 200 and '"ok":true' in r.text.replace(" ", "")
        out = {"ok": ok, "status": r.status_code, "ms": int((time.monotonic() - t0) * 1000), "error": "" if ok else r.text[:200]}
    except (httpx.HTTPError, OSError) as exc:
        out = {"ok": False, "status": None, "ms": None, "error": str(exc)[:200]}
    try:
        v = _validity(served_cert(url, timeout))
        out["cert_expires"], out["cert_issued"] = v.get("expires"), v.get("issued")
    except (OSError, ssl.SSLError, ValueError):
        out["cert_expires"] = out["cert_issued"] = None
    return out


# --- alerts --------------------------------------------------------------------------------------

def _key(prefix: str, message: str) -> str:
    return f"{prefix}:{re.sub(r'[0-9]+(?:[.,][0-9]+)?', '#', message)}"[:255]


def evaluate(db: Session, now: dt.datetime | None = None) -> dict[str, tuple[int | None, str, str]]:
    """key -> (host id, level, message) for everything that's wrong now."""
    now = now or utcnow()
    cfg = appsettings.get(db, "monitoring")
    out: dict[str, tuple[int | None, str, str]] = {}

    def add(h: KvmHost | None, level: str, message: str, kind: str = "") -> None:
        prefix = f"host{h.id}" if h else "stack"
        out[_key(f"{prefix}:{kind}" if kind else prefix, message)] = (h.id if h else None, level,
                                                                       f"{h.name}: {message}" if h else message)

    for h in db.scalars(select(KvmHost).order_by(KvmHost.id)):
        stack = runs_stack(h)
        if h.access_status == "ready":
            f = db.get(HostFacts, h.id)
            latest = db.scalar(select(HostMetric).where(HostMetric.kvm_host_id == h.id).order_by(HostMetric.at.desc()).limit(1))
            ld = latest.data if latest else {}
            for w in hostadmin.warnings(f.facts if f else {}, f.smart if f else {}, ld):
                if w["level"] == "info":
                    continue
                if stack and ("°C" in w["message"] or "% full" in w["message"]):
                    continue                                     # stack hosts: the monitoring thresholds below
                add(h, w["level"], w["message"])
            if stack:
                for name, t in (ld.get("temps") or {}).items():
                    if isinstance(t, (int, float)) and t >= cfg.temp_c:
                        add(h, "warning" if t < cfg.temp_c + 5 else "error", f"{name} at {t:.0f} °C", "temp")
                for mount, pct in (ld.get("fs_used_pct") or {}).items():
                    if pct >= cfg.disk_pct:
                        add(h, "warning" if pct < 95 else "error", f"{mount} is {pct}% full", "fs")
        if not stack:
            continue
        st = db.get(StackStatus, h.id)
        data = (st.data if st else {}) or {}
        for c in data.get("containers") or []:
            name = c["name"]
            if c.get("state") not in ("running", "created") and not name.endswith("-init"):
                add(h, "error", f"container {name} is {c.get('state') or 'gone'}", "container")
            elif c.get("health") == "unhealthy":
                add(h, "warning", f"container {name} is unhealthy", "container")
            seen = (data.get("restart_seen") or {}).get(name)
            if seen and now - dt.datetime.fromisoformat(seen) < RESTART_WINDOW:
                add(h, "warning", f"container {name} restarted (restart count {c.get('restarts')})", "restart")
        if is_self(h):
            me = data.get("self") or {}
            b = me.get("backup") or {}
            if b.get("enabled"):
                newest = dt.datetime.fromisoformat(b["newest"]) if b.get("newest") else None
                if newest is None or now - newest > dt.timedelta(hours=cfg.backup_hours):
                    add(h, "warning", f"no backup in the last {cfg.backup_hours} hours", "backup")
            sb = me.get("stack_backup") or {}
            if sb.get("enabled"):
                newest = dt.datetime.fromisoformat(sb["newest"]) if sb.get("newest") else None
                if newest is None or now - newest > dt.timedelta(hours=cfg.backup_hours):
                    add(h, "warning", f"no full stack backup in the last {cfg.backup_hours} hours", "stack-backup")
                elif not sb.get("ok"):
                    add(h, "error", f"the latest full stack backup ({sb.get('archive')}) failed its check", "stack-backup-check")
            for name, c in (me.get("certs") or {}).items():
                if cert_due(c, now, cfg.cert_days):
                    expired = dt.datetime.fromisoformat(c["expires"]) <= now
                    add(h, "error" if expired else "warning",
                        f"the {name} certificate expires {c['expires'][:16].replace('T', ' ')} UTC", "cert")
        elif h.stack_url:
            p = data.get("peer") or {}
            if p.get("failures", 0) >= cfg.peer_failures:
                add(h, "error", f"the stack at {h.stack_url} isn't answering ({p.get('error') or p.get('status')})", "peer")
            if cert_due({"expires": p.get("cert_expires"), "issued": p.get("cert_issued")}, now, cfg.cert_days):
                add(h, "warning", f"the stack's certificate expires {p['cert_expires'][:16].replace('T', ' ')} UTC", "cert")
    em = email_state(db)
    if em.get("ok") is False:
        out[EMAIL_KEY] = (None, "error", f"alert emails can't be sent: {em.get('error', '')[:300]}")
    return out


def email_state(db: Session) -> dict:
    row = db.get(Setting, EMAIL_STATE_KEY)
    return dict(row.value) if row else {}


def _put_email_state(db: Session, ok: bool, error: str, now: dt.datetime) -> None:
    value = {"ok": ok, "error": error, "at": now.isoformat()}
    row = db.get(Setting, EMAIL_STATE_KEY)
    if row is None:
        db.add(Setting(key=EMAIL_STATE_KEY, value=value))
    else:
        row.value = value
    db.commit()


def silenced(db: Session, now: dt.datetime) -> dict[str, MonitorSilence]:
    return {s.key: s for s in db.scalars(select(MonitorSilence).where(MonitorSilence.until > now))}


def sync_alerts(db: Session, current: dict, now: dt.datetime | None = None, send=None) -> tuple[list, list]:
    """Open the alerts that started and clear the ones that ended, then announce what's due.

    A start is announced once, when it's neither silenced nor in maintenance mode, so one that began
    in either is announced when that ends if it's still open (design §31). A clear is announced when
    its start was, silence or not, so whoever got the start also gets the all-clear. `send` returns
    an error text when the alert email failed. Returns the (started, cleared) alerts announced."""
    now = now or utcnow()
    open_ = {a.key: a for a in db.scalars(select(MonitorAlert).where(MonitorAlert.cleared_at.is_(None)))}
    cleared = []
    for key, (host_id, level, message) in current.items():
        a = open_.get(key)
        if a is None:
            a = MonitorAlert(key=key, kvm_host_id=host_id, level=level, message=message, started_at=now)
            db.add(a)
            open_[key] = a
        else:
            a.level, a.message = level, message
    for key, a in list(open_.items()):
        if key not in current:
            a.cleared_at = now
            if a.announced_at is not None:
                cleared.append(a)
            del open_[key]
    quiet = maintenance_mode.get(db)["active"]
    hush = silenced(db, now)
    started = [] if quiet else [a for a in open_.values() if a.announced_at is None and a.key not in hush]
    for a in started:
        a.announced_at = now
    db.commit()
    if started or cleared:
        err = (send or announce)(db, started, cleared)
        if err is not None:
            _put_email_state(db, not err, err, now)
    return started, cleared


def announce(db: Session, started: list, cleared: list) -> str | None:
    """Syslog (through the audit trail's sender) and one email for the batch (question 143).
    Returns "" when the email went, its error when it failed, None when none was due."""
    from .. import notify
    from ..appsettings import SMTP_PASSWORD_SECRET
    from ..security import get_secret
    cfg = appsettings.get(db, "monitoring")
    if cfg.syslog:
        sc = appsettings.get(db, "syslog")
        for state, alerts in (("started", started), ("cleared", cleared)):
            for a in alerts:
                notify.audit_to_syslog(sc, {"action": f"monitor.alert.{state}", "actor": "labape-monitor",
                                            "outcome": "ok" if state == "cleared" else ("failed" if a.level == "error" else "denied"),
                                            "detail": {"level": a.level, "message": a.message, "key": a.key,
                                                       "started_at": a.started_at, "cleared_at": a.cleared_at}})
    smtp = appsettings.get(db, "smtp")
    # The email alert's own start can't go by email.
    started = [a for a in started if a.key != EMAIL_KEY]
    if (started or cleared) and cfg.email_to and smtp.enabled and smtp.host and smtp.from_address:
        name = urlsplit(get_settings().public_url).hostname or "LABaPe"
        lines = [f"[{a.level.upper()}] {a.message}" for a in started] + [f"[CLEARED] {a.message}" for a in cleared]
        subject = f"LABaPe {name}: " + (f"{len(started)} new alert(s)" if started else "") + \
            (", " if started and cleared else "") + (f"{len(cleared)} cleared" if cleared else "")
        try:
            notify.send_email(smtp, get_secret(db, SMTP_PASSWORD_SECRET) or "", cfg.email_to, subject,
                              "\n".join(lines) + f"\n\nDashboard: {get_settings().public_url}\n")
        except Exception as exc:  # noqa: BLE001 — the dashboard still has them; raises its own alert
            log.warning("alert email failed: %s", exc)
            return str(exc)[:500] or exc.__class__.__name__
        return ""
    return None


def recheck_email(db: Session, now: dt.datetime) -> None:
    """While alert emails fail, try the server (log in, no send) every few minutes, so the alert
    clears when it works again even if nothing else is due to be sent."""
    from .. import notify
    from ..appsettings import SMTP_PASSWORD_SECRET
    from ..security import get_secret
    em = email_state(db)
    if em.get("ok") is not False:
        return
    if em.get("at") and now - _aware(dt.datetime.fromisoformat(em["at"])) < EMAIL_RECHECK:
        return
    smtp = appsettings.get(db, "smtp")
    if not (smtp.enabled and smtp.host):
        _put_email_state(db, True, "", now)          # email turned off: nothing is failing
        return
    try:
        notify.smtp_check(smtp, get_secret(db, SMTP_PASSWORD_SECRET) or "")
        _put_email_state(db, True, "", now)
    except Exception as exc:  # noqa: BLE001
        _put_email_state(db, False, str(exc)[:500] or exc.__class__.__name__, now)


# --- the worker's part -----------------------------------------------------------------------

def tick(db_factory) -> None:
    """Every minute, in a thread: probes, due collections, then the alerts."""
    if not _lock.acquire(blocking=False):
        return

    def go():
        try:
            with db_factory() as db:
                now = utcnow()
                for h in db.scalars(select(KvmHost).where(KvmHost.role.in_(STACK_ROLES))):
                    st = _status(db, h)
                    data = dict(st.data or {})
                    try:
                        if is_self(h):
                            data["self"] = self_checks(db)
                        elif h.stack_url:
                            p = probe_peer(h.stack_url)
                            p["failures"] = 0 if p["ok"] else (data.get("peer") or {}).get("failures", 0) + 1
                            data["peer"] = {**p, "at": now.isoformat()}
                        st.data, st.checked_at = data, now
                        db.merge(st)
                        db.commit()
                        if h.access_status == "ready":
                            last = _aware(dt.datetime.fromisoformat(data["containers_at"])) if data.get("containers_at") else None
                            if last is None or now - last >= CONTAINERS_EVERY - dt.timedelta(seconds=20):
                                vlast = _aware(dt.datetime.fromisoformat(data["volumes_at"])) if data.get("volumes_at") else None
                                collect_containers(db, h, volumes=vlast is None or now - vlast >= VOLUMES_EVERY, now=now)
                    except Exception as exc:  # noqa: BLE001 — one host's trouble doesn't stop the others
                        st = _status(db, h)
                        st.error = str(exc)[:1000]
                        db.merge(st)
                        db.commit()
                recheck_email(db, now)
                sync_alerts(db, evaluate(db, now), now)
        except Exception:  # noqa: BLE001
            log.exception("stack monitoring")
        finally:
            _lock.release()

    threading.Thread(target=go, name="stackmon", daemon=True).start()


def view(db: Session) -> dict:
    """What the dashboard and the hosts' Stack sections show."""
    hosts = {h.id: h for h in db.scalars(select(KvmHost))}
    alerts = list(db.scalars(select(MonitorAlert).order_by(MonitorAlert.started_at.desc()).limit(200)))
    hush = silenced(db, utcnow())
    out = lambda a: {"id": a.id, "key": a.key, "host": hosts[a.kvm_host_id].name if a.kvm_host_id in hosts else "",  # noqa: E731
                     "kvm_host_id": a.kvm_host_id, "level": a.level, "message": a.message,
                     "started_at": a.started_at, "cleared_at": a.cleared_at, "announced_at": a.announced_at,
                     "acked_at": a.acked_at, "acked_by": a.acked_by,
                     "silenced_until": hush[a.key].until if a.key in hush and a.cleared_at is None else None}
    rows = {r.kvm_host_id: r for r in db.scalars(select(StackStatus))}
    return {"quiet": bool(maintenance_mode.get(db)["active"]),
            "silences": [{"id": s.id, "key": s.key, "message": s.message, "reason": s.reason, "created_by": s.created_by,
                          "created_at": s.created_at, "until": s.until} for s in hush.values()],
            "open": [out(a) for a in alerts if a.cleared_at is None],
            "recent": [out(a) for a in alerts if a.cleared_at is not None][:50],
            "stacks": [{"id": h.id, "name": h.name, "role": h.role, "stack_url": h.stack_url, "self": is_self(h),
                        "status": rows[h.id].data if h.id in rows else {},
                        "checked_at": rows[h.id].checked_at if h.id in rows else None,
                        "error": rows[h.id].error if h.id in rows else ""}
                       for h in hosts.values() if runs_stack(h)]}
