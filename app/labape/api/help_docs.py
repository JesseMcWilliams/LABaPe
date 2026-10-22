"""The Help page (backlog row 24): the user guides, served by the stack so nobody needs the files.

The guides are the Markdown files in `User_Docs/`; the image carries them in `help_dir` and a checkout is read from
its own `User_Docs/`. The page renders them and links a heading from each page of the interface."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..config import get_settings
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/help", tags=["help"])

# id -> (title, file, roles that may read it; empty = everyone signed in)
DOCS: dict[str, tuple[str, str, set[str]]] = {
    "guide": ("Using the web interface", "Web-Interface-Guide.md", set()),
    "admin": ("Administering the web interface", "Web-Interface-Admin.md", {"admin", "auditor"}),
    "cli": ("Command line", "Command-Line.md", {"admin"}),
}


def _dir() -> Path:
    d = get_settings().help_dir
    return d if d.is_dir() else Path(__file__).resolve().parents[3] / "User_Docs"


def _allowed(p: Principal, roles: set[str]) -> bool:
    return not roles or any(p.has_role(r) for r in roles)


@router.get("")
def list_docs(p: Principal = Depends(current_principal)):
    return [{"id": k, "title": t} for k, (t, f, roles) in DOCS.items() if _allowed(p, roles) and (_dir() / f).is_file()]


@router.get("/{doc}")
def read_doc(doc: str, p: Principal = Depends(current_principal)):
    entry = DOCS.get(doc)
    if entry is None or not _allowed(p, entry[2]) or not (_dir() / entry[1]).is_file():
        raise HTTPException(status_code=404, detail="No such help page")
    return {"id": doc, "title": entry[0], "markdown": (_dir() / entry[1]).read_text(encoding="utf-8")}
