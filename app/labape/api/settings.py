"""Admin settings (design §21.5): file locations, backups, history
retention, syslog output and SMTP, with test actions and backup files."""
from __future__ import annotations

import datetime as dt
import re
import smtplib
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, notify
from ..db import get_db
from ..errors import FieldError
from ..files import roots
from ..models import Job
from ..security import Principal, audit, client_ip, get_secret, put_secret, require_roles

router = APIRouter(prefix="/api/admin", tags=["settings"])
BACKUP_NAME = re.compile(r"^labape-\d{8}-\d{6}\.dump$")


@router.get("/settings")
def get_settings_all(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    out = {name: appsettings.get(db, name).model_dump() for name in appsettings.SECTIONS}
    for section, (_, secret, flag) in appsettings.SECRET_FIELDS.items():
        out[section][flag] = bool(get_secret(db, secret))
    out["file_roots"] = roots(db)
    return out


@router.put("/settings/{section}")
def put_settings(section: str, body: dict, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    if section not in appsettings.SECTIONS:
        raise FieldError("section", f"Unknown settings section {section!r}.", status_code=404)
    field, secret, flag = appsettings.SECRET_FIELDS.get(section, (None, None, None))
    value = body.pop(field, None) if field else None
    clear = bool(body.pop(f"clear_{field}", False)) if field else False
    body.pop(flag, None)
    try:
        model = appsettings.SECTIONS[section](**body)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = str(err["loc"][0]) if err.get("loc") else section
        raise FieldError(field, err["msg"].removeprefix("Value error, ")) from exc
    if section == "syslog" and model.enabled and not model.host:
        raise FieldError("host", "Give the syslog server's host name or address, or turn syslog off.")
    if section == "smtp" and model.enabled and (not model.host or "@" not in model.from_address):
        raise FieldError("from_address" if model.host else "host",
                         "Give the SMTP server and a From address (name@domain), or turn email off.")
    appsettings.put(db, section, model)
    if secret and (value or clear):
        put_secret(db, secret, "" if clear else value)
    detail = model.model_dump()
    if field:
        detail[f"{field}_changed"] = bool(value or clear)
    audit(db, p.username, f"settings.{section}", detail=detail, source_ip=client_ip(request))
    return get_settings_all(db, p)[section]


@router.post("/settings/syslog/test")
def test_syslog(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    cfg = appsettings.get(db, "syslog")
    if not cfg.host:
        raise FieldError("host", "Save a syslog server first.")
    try:
        notify.send_syslog(cfg, {"action": "settings.syslog.test", "actor": p.username, "outcome": "ok",
                                 "detail": {"message": "LABaPe syslog test"}})
    except OSError as exc:
        raise FieldError("host", f"Couldn't send to {cfg.host}:{cfg.port} over {cfg.protocol}: {exc}") from exc
    audit(db, p.username, "settings.syslog.test", source_ip=client_ip(request))
    return {"ok": True, "message": f"Test message sent to {cfg.host}:{cfg.port} ({cfg.protocol})."}


class TestEmailIn(BaseModel):
    to: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")


@router.post("/settings/smtp/test")
def test_smtp(body: TestEmailIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    cfg = appsettings.get(db, "smtp")
    if not cfg.host or not cfg.from_address:
        raise FieldError("host", "Save the SMTP server and From address first.")
    try:
        notify.send_email(cfg, get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or "", [body.to],
                          "LABaPe test email", f"This is a test email from LABaPe, sent by {p.username}.")
    except (OSError, smtplib.SMTPException) as exc:
        raise FieldError("host", f"Sending through {cfg.host}:{cfg.port} failed: {exc}") from exc
    audit(db, p.username, "settings.smtp.test", detail={"to": body.to}, source_ip=client_ip(request))
    return {"ok": True, "message": f"Test email sent to {body.to}."}


# --- backups ------------------------------------------------------------------

@router.get("/backups")
def list_backups(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    d = Path(appsettings.get(db, "backup").directory)
    files = []
    if d.is_dir():
        for f in sorted(d.glob("labape-*.dump"), reverse=True):
            st = f.stat()
            files.append({"name": f.name, "size": st.st_size,
                          "created": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)})
    last = db.scalar(select(Job).where(Job.type == "system.backup").order_by(Job.id.desc()))
    return {"directory": str(d), "files": files,
            "last_job": {"id": last.id, "state": last.state, "finished_at": last.finished_at} if last else None}


@router.post("/backups")
def backup_now(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    busy = db.scalar(select(Job).where(Job.type == "system.backup", Job.state.in_(("queued", "running"))))
    if busy:
        raise FieldError("backup", f"Backup job {busy.id} is already {busy.state}.", status_code=409)
    job = Job(type="system.backup", params={}, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "backup.run", object_type="job", object_id=job.id, source_ip=client_ip(request))
    return {"job_id": job.id}


@router.get("/backups/{name}")
def download_backup(name: str, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("admin"))):
    if not BACKUP_NAME.match(name):
        raise FieldError("name", "No such backup.", status_code=404)
    f = Path(appsettings.get(db, "backup").directory) / name
    if not f.is_file():
        raise FieldError("name", "No such backup.", status_code=404)
    audit(db, p.username, "backup.download", detail={"name": name}, source_ip=client_ip(request))
    return FileResponse(f, filename=name, media_type="application/octet-stream")
