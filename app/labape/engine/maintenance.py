"""Housekeeping the worker does (design §21.5): database backups (on demand
and daily), history retention purges, and job-result emails."""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import subprocess
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .. import appsettings, notify
from ..config import get_settings
from ..db import session_factory
from ..models import AuditLog, Job, Setting, User, UserError, utcnow
from ..security import get_secret

log = logging.getLogger("labape.maintenance")
FINISHED = ("succeeded", "failed", "cancelled")


# --- backups -------------------------------------------------------------------

def _pg(url) -> tuple[dict, list[str]]:
    # The password goes in the environment, not on the command line.
    return ({**os.environ, "PGPASSWORD": url.password or ""},
            [f"--host={url.host or 'localhost'}", f"--port={url.port or 5432}", f"--username={url.username or 'labape'}"])


def dump_database(db: Session, logf, label: str = "") -> tuple[int, Path]:
    """Dump the database to <backup dir>/labape-YYYYmmdd-HHMMSS[-label].dump."""
    cfg = appsettings.get(db, "backup")
    d = Path(cfg.directory)
    d.mkdir(parents=True, exist_ok=True)
    suffix = f"-{label}" if label else ""
    target = d / f"labape-{utcnow():%Y%m%d-%H%M%S}{suffix}.dump"
    url = make_url(get_settings().database_url)
    logf.write(f"labape: backing up the database to {target}\n")
    logf.flush()
    if url.get_backend_name() == "sqlite":
        src = sqlite3.connect(url.database)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        rc = 0
    else:
        env, conn = _pg(url)
        rc = subprocess.run(["pg_dump", "--format=custom", "--no-owner", f"--file={target}", *conn,
                             url.database or "labape"], env=env, stdout=logf, stderr=subprocess.STDOUT,
                            check=False).returncode
    if rc != 0:
        target.unlink(missing_ok=True)
        logf.write(f"labape: pg_dump failed (exit {rc})\n")
        return rc, target
    logf.write(f"labape: wrote {target.stat().st_size} bytes\n")
    return 0, target


def restore_database(path: Path, logf) -> int:
    """Restore a dump over the database (the update agent's rollback across a
    change of layout). The API and worker must be stopped first."""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() == "sqlite":
        dst = sqlite3.connect(url.database)
        src = sqlite3.connect(path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return 0
    env, conn = _pg(url)
    logf.write(f"labape: restoring {path}\n")
    logf.flush()
    return subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction",
                           f"--dbname={url.database or 'labape'}", *conn, str(path)],
                          env=env, stdout=logf, stderr=subprocess.STDOUT, check=False).returncode


def run_backup(db: Session, job: Job) -> int:
    """A backup job: dump, then drop dumps older than the retention. Writes to the job log."""
    cfg = appsettings.get(db, "backup")
    with open(job.log_path, "a", encoding="utf-8") as logf:
        rc, _ = dump_database(db, logf, (job.params or {}).get("label", ""))
        if rc != 0:
            return rc
        for old in purge_backups(cfg):
            logf.write(f"labape: removed {old.name} (older than {cfg.retention_days} days)\n")
    return 0


def purge_backups(cfg) -> list[Path]:
    cutoff = (utcnow() - dt.timedelta(days=cfg.retention_days)).timestamp()
    gone = []
    d = Path(cfg.directory)
    if d.is_dir():
        for f in d.glob("labape-*.dump"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                gone.append(f)
    return gone


def schedule_backup(db: Session, now: dt.datetime | None = None) -> Job | None:
    """Queue today's backup once the configured time (UTC) has passed and none
    has been queued since then."""
    cfg = appsettings.get(db, "backup")
    if not cfg.enabled:
        return None
    now = now or utcnow()
    hh, mm = (int(x) for x in cfg.time_of_day.split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due:
        return None
    last = db.scalar(select(Job).where(Job.type == "system.backup").order_by(Job.id.desc()))
    if last is not None and _aware(last.created_at) >= due:
        return None
    job = Job(type="system.backup", params={"scheduled": True}, requested_by="system")
    db.add(job)
    db.commit()
    return job


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)   # SQLite drops the zone


# --- retention -----------------------------------------------------------------

def purge_history(db: Session) -> dict[str, int]:
    """Apply the retention settings: job log files, audit rows, user errors."""
    cfg = appsettings.get(db, "retention")
    now = utcnow()
    out = {"job_logs": 0, "audit": 0, "errors": 0}
    cutoff = now - dt.timedelta(days=cfg.job_logs_days)
    for job in db.scalars(select(Job).where(Job.state.in_(FINISHED), Job.finished_at < cutoff, Job.log_path != "")):
        p = Path(job.log_path)
        if p.is_file():
            p.unlink(missing_ok=True)
            out["job_logs"] += 1
    if cfg.audit_days:
        out["audit"] = db.execute(delete(AuditLog).where(
            AuditLog.at < now - dt.timedelta(days=cfg.audit_days))).rowcount or 0
    out["errors"] = db.execute(delete(UserError).where(
        UserError.at < now - dt.timedelta(days=cfg.errors_days))).rowcount or 0
    db.commit()
    purge_backups(appsettings.get(db, "backup"))
    out["recordings"] = purge_recordings(appsettings.get(db, "remote").keep_recordings_days)
    from .. import syslogstack
    try:
        out["syslog"] = syslogstack.purge(db)
    except Exception as exc:  # noqa: BLE001 — the syslog database may be down; the rest still ran
        out["syslog_error"] = str(exc)[:200]
    return out


RECORDINGS = Path("/var/lib/labape-recordings")


def purge_recordings(days: int, root: Path = RECORDINGS) -> int:
    """Delete remote session recordings older than `days` (design §28.5)."""
    if not root.is_dir():
        return 0
    cutoff = dt.datetime.now().timestamp() - days * 86400
    n = 0
    for f in root.glob("session-*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


# --- job emails ----------------------------------------------------------------

def notify_job(db: Session, job: Job) -> None:
    """Email the requester when their job finishes, if that's turned on."""
    cfg = appsettings.get(db, "smtp", cached=True)
    if not (cfg.enabled and cfg.notify_jobs and cfg.host and cfg.from_address):
        return
    user = db.scalar(select(User).where(User.username == job.requested_by))
    to = (user.email or "") if user else ""
    if "@" not in to:
        return
    base = get_settings().public_url.rstrip("/")
    subject = f"{appsettings.get(db, 'branding', cached=True).app_name} job {job.id} {job.state}: {job.type}"
    body = (f"Job {job.id} ({job.type}) {job.state}"
            f"{'' if job.exit_code is None else f' with exit code {job.exit_code}'}.\n\n"
            f"Details and log: {base}/jobs/{job.id}\n")
    notify.email_in_background(cfg, get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or "", [to], subject, body,
                               on_done=record_email)


EMAIL_STATUS_KEY = "status.email"


def record_email(ok: bool, error: str) -> None:
    """The last job email's result, for the admin dashboard."""
    value = {"at": utcnow().isoformat(), "ok": ok, "error": error}
    with session_factory()() as db:
        row = db.get(Setting, EMAIL_STATUS_KEY)
        if row is None:
            db.add(Setting(key=EMAIL_STATUS_KEY, value=value))
        else:
            row.value = value
        db.commit()
