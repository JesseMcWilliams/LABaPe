"""The full-stack backup's app side (design §32, batch B1).

The update agent does the work on the host (deploy/stack-backup.sh): the app asks for it through
the agent's request file, on the daily schedule or Back up now, and lists the archives from their
sidecars (`<archive>.json`: versions, sizes, the check's result; no secrets), which sit in a folder
under the data root that the containers see at the same path.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from .. import appsettings, update
from ..models import Setting, utcnow

ARCHIVE_NAME = re.compile(r"^labape-stack-[A-Za-z0-9._-]{1,64}-\d{8}-\d{6}\.tar\.sealed$")
REQUESTED_KEY = "backup.stack_requested_at"
KEY_SAVED_KEY = "backup.stack_key_saved"


class Busy(Exception):
    pass


def archives(directory: str) -> list[dict]:
    """The archives with a sidecar, newest first."""
    d = Path(directory)
    out = []
    if not d.is_dir():
        return out
    for side in d.glob("labape-stack-*.tar.sealed.json"):
        archive = side.with_suffix("")
        if not ARCHIVE_NAME.match(archive.name) or not archive.is_file():
            continue
        try:
            m = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m["archive"] = archive.name
        m["size"] = archive.stat().st_size
        out.append(m)
    return sorted(out, key=lambda m: m.get("created_at") or "", reverse=True)


def _setting(db: Session, key: str) -> dict | None:
    row = db.get(Setting, key)
    return dict(row.value) if row and row.value else None


def _put(db: Session, key: str, value: dict) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def request(db: Session, actor: str, now: dt.datetime | None = None) -> dict:
    """Ask the host's agent for a full backup now; Busy when it can't take one."""
    if not update.agent_installed():
        raise Busy("The update agent isn't installed on the stack host, and it's what takes the full backup.")
    if update.busy():
        raise Busy("The update agent is busy with another request; try again when it's done.")
    cfg = appsettings.get(db, "stack_backup")
    req = update.write_request("full_backup", "", actor, dest=cfg.directory, keep_days=cfg.retention_days,
                               syslog=cfg.include_syslog, recordings=cfg.include_recordings)
    _put(db, REQUESTED_KEY, {"at": (now or utcnow()).isoformat(), "by": actor, "id": req["id"]})
    return req


def schedule(db: Session, now: dt.datetime | None = None) -> bool:
    """Ask for today's full backup once its time (UTC) has passed and none was asked for since.
    While the agent is busy (an update, a rotation) it's asked again on a later pass."""
    cfg = appsettings.get(db, "stack_backup")
    if not cfg.enabled or not update.agent_installed():
        return False
    now = now or utcnow()
    hh, mm = (int(x) for x in cfg.time_of_day.split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due:
        return False
    last = _setting(db, REQUESTED_KEY)
    if last and dt.datetime.fromisoformat(last["at"]) >= due:
        return False
    if update.busy():
        return False
    request(db, "system", now)
    return True


def key_saved(db: Session) -> dict | None:
    return _setting(db, KEY_SAVED_KEY)


def mark_key_saved(db: Session, actor: str) -> dict:
    v = {"by": actor, "at": utcnow().isoformat()}
    _put(db, KEY_SAVED_KEY, v)
    return v


def newest(db: Session) -> dict | None:
    a = archives(appsettings.get(db, "stack_backup").directory)
    return a[0] if a else None
