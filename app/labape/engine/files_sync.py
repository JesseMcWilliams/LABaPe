"""File manager scan (design §21.3): register files found in the
locations (copied in by hand, or there before the app), drop ones that
disappeared, and checksum unhashed files in the background.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ostargets
from ..db import session_factory
from ..files import CATEGORIES, infer_os, roots
from ..models import StoredFile

log = logging.getLogger("labape.files")
_hashing = threading.Lock()


def scan(db: Session) -> None:
    from .. import filestore
    store = filestore.current(db)                 # the primary file host's locations (design §29.5)
    locations = roots(db)
    known = {f.path: f for f in db.scalars(select(StoredFile))}
    seen = set()
    for cat in CATEGORIES:
        try:
            found = store.files(locations[cat])
        except filestore.StoreError as exc:
            log.warning("file scan: %s", exc)
            return                                # the primary is down: leave the list as it was
        for path, size, mtime in found:
            seen.add(path)
            f = known.get(path)
            if f is None:
                name = Path(path).name
                f = StoredFile(category=cat, name=name, path=path,
                               os_key=infer_os(name) if cat == "isos" else "",
                               targets=ostargets.infer(name) if cat == "installers" else [])
                db.add(f)
            if f.size != size or (f.mtime and abs((f.mtime.replace(tzinfo=f.mtime.tzinfo or dt.timezone.utc)
                                                   - mtime).total_seconds()) > 1):
                f.sha256 = ""  # content changed since it was hashed
            f.size, f.mtime, f.present, f.category = size, mtime, True, cat
    for path, f in known.items():
        if path not in seen and f.present:
            f.present = False
    db.commit()


def _hash_pending() -> None:
    try:
        with session_factory()() as db:
            from .. import filestore
            store = filestore.current(db)
            for f in db.scalars(select(StoredFile).where(StoredFile.present.is_(True), StoredFile.sha256 == "")):
                try:
                    f.sha256 = store.hashes(f.path, {"sha256"})["sha256"]
                    db.commit()
                except OSError as exc:
                    log.warning("checksum of %s failed: %s", f.path, exc)
    finally:
        _hashing.release()


def hash_in_background() -> None:
    if _hashing.acquire(blocking=False):
        threading.Thread(target=_hash_pending, name="file-checksums", daemon=True).start()
