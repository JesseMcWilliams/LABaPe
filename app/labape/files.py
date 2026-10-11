"""File manager locations and ISO handling (design §9, §21.3).

Each category lives under its own location, default /data/<category>
(decision 41), configurable as the `files.roots` setting (the admin
settings page, batch E; `labape bootstrap` can set it too).
"""
from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting, StoredFile

CATEGORIES = ("isos", "installers", "files")
DEFAULT_ROOTS = {"isos": "/data/isos", "installers": "/data/installers", "files": "/data/files"}
ROOTS_KEY = "files.roots"

# Same patterns as deploy/labape_deploy/steps.py ISO_PATTERNS: how an ISO's
# name says which OS it installs.
ISO_PATTERNS = {
    "rocky9": ["*rocky-9*minimal*.iso", "*rocky-9*.iso"],
    "windows_server_2019": ["*server*2019*.iso"],
    "windows_server_2022": ["*server*2022*.iso"],
    "windows_server_2025": ["*server*2025*.iso"],
    "windows_11": ["*windows*11*.iso"],
    "windows_10": ["*windows*10*.iso"],
    "ubuntu_lts": ["*ubuntu-24.04*live-server*.iso"],
    "ubuntu_26": ["*ubuntu-26.04*live-server*.iso"],
    "debian_latest": ["*debian-13*netinst*.iso"],
}


def roots(db: Session) -> dict[str, str]:
    row = db.get(Setting, ROOTS_KEY)
    return {**DEFAULT_ROOTS, **((row.value or {}) if row else {})}


def set_roots(db: Session, new: dict[str, str]) -> dict[str, str]:
    merged = {**roots(db), **{k: v for k, v in new.items() if k in CATEGORIES and v}}
    row = db.get(Setting, ROOTS_KEY)
    if row is None:
        db.add(Setting(key=ROOTS_KEY, value=merged))
    else:
        row.value = merged
    db.commit()
    return merged


def infer_os(name: str) -> str:
    low = name.lower()
    for key, pats in ISO_PATTERNS.items():
        if any(fnmatch.fnmatch(low, p) for p in pats):
            return key
    return ""


def sha256_of(path: Path, chunk: int = 8 * 2**20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def iso_paths(db: Session) -> dict[str, str]:
    """os_key -> ISO path for environment.yml's os_iso_paths: the ISO
    marked default for that OS, else the newest present one."""
    out: dict[str, tuple] = {}
    for f in db.scalars(select(StoredFile).where(StoredFile.category == "isos", StoredFile.present.is_(True),
                                                 StoredFile.os_key != "")):
        rank = (f.is_default, f.mtime.timestamp() if f.mtime else 0)
        if f.os_key not in out or rank > out[f.os_key][0]:
            out[f.os_key] = (rank, f.path)
    return {k: v[1] for k, v in out.items()}
