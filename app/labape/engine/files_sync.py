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

from ..db import session_factory
from ..files import CATEGORIES, infer_os, roots, sha256_of
from ..models import StoredFile

log = logging.getLogger("labape.files")
_hashing = threading.Lock()


def scan(db: Session) -> None:
    locations = roots(db)
    known = {f.path: f for f in db.scalars(select(StoredFile))}
    seen = set()
    for cat in CATEGORIES:
        root = Path(locations[cat])
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            seen.add(str(path))
            st = path.stat()
            mtime = dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
            f = known.get(str(path))
            if f is None:
                f = StoredFile(category=cat, name=path.name, path=str(path),
                               os_key=infer_os(path.name) if cat == "isos" else "")
                db.add(f)
            if f.size != st.st_size or (f.mtime and abs((f.mtime.replace(tzinfo=f.mtime.tzinfo or dt.timezone.utc)
                                                         - mtime).total_seconds()) > 1):
                f.sha256 = ""  # content changed since it was hashed
            f.size, f.mtime, f.present, f.category = st.st_size, mtime, True, cat
    for path, f in known.items():
        if path not in seen and f.present:
            f.present = False
    db.commit()


def _hash_pending() -> None:
    try:
        with session_factory()() as db:
            for f in db.scalars(select(StoredFile).where(StoredFile.present.is_(True), StoredFile.sha256 == "")):
                try:
                    f.sha256 = sha256_of(Path(f.path))
                    db.commit()
                except OSError as exc:
                    log.warning("checksum of %s failed: %s", f.path, exc)
    finally:
        _hashing.release()


def hash_in_background() -> None:
    if _hashing.acquire(blocking=False):
        threading.Thread(target=_hash_pending, name="file-checksums", daemon=True).start()
