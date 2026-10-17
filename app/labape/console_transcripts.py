"""Text transcripts of API console use (design §59.3, question 182): kept only while Settings → Remote
access has recording on, beside the browser recordings. guacd writes the browser ones into a volume the
API mounts read-only, so the API writes its own into a volume it owns; the worker's daily purge covers
both, and auditors download them from the same route.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from . import appsettings

DIR = Path(os.environ.get("LABAPE_TRANSCRIPTS_DIR", "/var/lib/labape-transcripts"))


def enabled(db: Session) -> bool:
    return bool(appsettings.get(db, "remote").record_sessions)


def path(session_id: int, root: Path | None = None) -> Path:
    return (root or DIR) / f"session-{session_id}.txt"


def append(session_id: int, text: str, root: Path | None = None) -> bool:
    """Add `text` to the session's transcript; False when it can't be written (the session is then not recorded)."""
    p = path(session_id, root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(text)
        return True
    except OSError:
        return False
