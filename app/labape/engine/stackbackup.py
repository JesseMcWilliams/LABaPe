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

from sqlalchemy import select
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


# --- copies to and from linked stacks (design §36, batch B3; question 149) ----------------------

SENT_KEY = "backup.stack_sent"            # {link id: {"archive", "at", "error", "tried_at"}}
COPIES_KEY = "backup.linked_copies"       # {peer url: [{"archive", "size", "created_at", "received_at", "host", "path"}]}
SHA_KEY = "backup.stack_sha256"           # {archive: sha256}, so a file is hashed once
RETRY = dt.timedelta(minutes=10)
LINKED_DIR = "linked-backups"             # under the primary file host's file cache


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def send_to_links(db: Session, now: dt.datetime | None = None, client_for=None) -> dict:
    """Send the newest checked archive to each linked stack that doesn't have it yet. A failed send
    is tried again after RETRY. Returns {link id: result}."""
    import base64

    import httpx

    from .. import links
    from ..models import StackLink
    from ..security import get_secret
    now = now or utcnow()
    cfg = appsettings.get(db, "stack_backup")
    out: dict = {}
    newest_ok = next((a for a in archives(cfg.directory) if (a.get("check") or {}).get("ok")), None)
    if not cfg.send_to_links or newest_ok is None:
        return out
    path = Path(cfg.directory) / newest_ok["archive"]
    sent = _setting(db, SENT_KEY) or {}
    shas = _setting(db, SHA_KEY) or {}
    for link in db.scalars(select(StackLink)).all():
        st = sent.get(str(link.id)) or {}
        if st.get("archive") == newest_ok["archive"] and not st.get("error"):
            continue
        if st.get("error") and st.get("tried_at") and now - dt.datetime.fromisoformat(st["tried_at"]) < RETRY:
            continue
        if newest_ok["archive"] not in shas:
            shas = {newest_ok["archive"]: sha256_file(path)}      # only the newest is ever sent
            _put(db, SHA_KEY, shas)
        side = {k: v for k, v in newest_ok.items() if k not in ("key_file",)}
        token = get_secret(db, links._secret_name(link.id))
        try:
            with open(path, "rb") as fh, (client_for or links.client)(link.ca_pem) as h:
                r = h.post(f"{link.url}/api/link/backups/{newest_ok['archive']}", content=fh,
                           headers={"Authorization": f"Bearer {token}", "X-Sha256": shas[newest_ok["archive"]],
                                    "X-LABaPe-Sidecar": base64.b64encode(json.dumps(side, default=str).encode()).decode(),
                                    "Content-Length": str(path.stat().st_size)},
                           timeout=httpx.Timeout(60, write=3600, read=600))
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            sent[str(link.id)] = {"archive": newest_ok["archive"], "at": now.isoformat(), "error": "",
                                  "tried_at": now.isoformat()}
        except Exception as exc:  # noqa: BLE001 — recorded and tried again later
            sent[str(link.id)] = {**st, "error": str(exc)[:300] or exc.__class__.__name__, "tried_at": now.isoformat()}
        out[link.id] = sent[str(link.id)]
        _put(db, SENT_KEY, sent)
    return out


def incoming_dir(db: Session) -> Path:
    """Where a linked stack's archive lands before the worker moves it to the primary file host."""
    return Path(appsettings.get(db, "stack_backup").directory).parent / "linked" / ".incoming"


def store_linked(db: Session, job) -> int:
    """Job `backup.store_linked`: a received archive onto this stack's primary file host, with its
    sidecar, then the peer's copies there past the retention (the newest always stays)."""
    import shlex

    from . import filedist
    p = job.params or {}
    staged = Path(p["staged"])
    peer = p["peer"]
    with open(job.log_path, "a", encoding="utf-8") as log:
        prim = filedist.primary(db)
        if prim is None:
            log.write("labape: no primary file host to keep the copy on\n")
            return 1
        ep = filedist.endpoint(prim)
        d = f"{filedist.cache_dir(prim, LINKED_DIR)}/{peer}"
        dst = f"{d}/{p['archive']}"
        log.write(f"labape: {p['archive']} from {p['peer_url']} -> {prim.name}:{dst}\n")
        side_path = staged.with_name(staged.name + ".json")
        filedist.copy(None, str(staged), ep, dst, log)
        if side_path.is_file():
            filedist.copy(None, str(side_path), ep, f"{dst}.json", log)
        staged.unlink(missing_ok=True)
        side_path.unlink(missing_ok=True)
        cfg = appsettings.get(db, "stack_backup")
        keep_after = utcnow() - dt.timedelta(days=cfg.retention_days)
        copies = _setting(db, COPIES_KEY) or {}
        mine = [c for c in copies.get(p["peer_url"], []) if c["archive"] != p["archive"]]
        mine.insert(0, {"archive": p["archive"], "size": p["size"], "created_at": p.get("created_at"),
                        "received_at": utcnow().isoformat(), "host": prim.name, "path": dst})
        mine.sort(key=lambda c: c.get("created_at") or "", reverse=True)
        kept, gone = mine[:1], []
        for c in mine[1:]:
            (gone if dt.datetime.fromisoformat(c["received_at"]) < keep_after else kept).append(c)
        for c in gone:
            cmd = f"rm -f {shlex.quote(c['path'])} {shlex.quote(c['path'] + '.json')}"
            if ep is None:
                Path(c["path"]).unlink(missing_ok=True)
                Path(c["path"] + ".json").unlink(missing_ok=True)
            else:
                ep.run(cmd, timeout=120)
            log.write(f"labape: removed {c['archive']} (older than {cfg.retention_days} days)\n")
        copies[p["peer_url"]] = kept
        _put(db, COPIES_KEY, copies)
    return 0
