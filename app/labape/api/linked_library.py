"""What the linked stacks offer, and copying it here (backlog row 22). The calls between stacks are in api/links.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import linked_library
from ..db import get_db
from ..errors import FieldError
from ..models import Job, StackLink
from ..security import Principal, audit, client_ip, current_principal, require_roles
from fastapi import Request

router = APIRouter(prefix="/api/linked", tags=["linked"])


@router.get("/library")
def library(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    """Every linked stack's templates and files, for the Template drop-down and the Files page."""
    return {"stacks": linked_library.all_libraries(db)}


class CopyIn(BaseModel):
    link_id: int
    kind: str = Field(pattern="^(file|template)$")
    item: str = Field(min_length=1, max_length=255)          # the template's name, or the file's id


@router.post("/copy", status_code=201)
def copy(body: CopyIn, request: Request, db: Session = Depends(get_db),
         p: Principal = Depends(require_roles("file_manager"))):
    """Start a job that copies one template or file from a linked stack onto this stack's primary file host."""
    link = db.get(StackLink, body.link_id)
    if link is None:
        raise FieldError("link_id", "No such linked stack.", status_code=404)
    try:
        lib = linked_library.peer_library(db, link)
    except linked_library.LibraryError as exc:
        raise FieldError("link_id", str(exc), status_code=502) from exc
    if body.kind == "template":
        item = next((t for t in lib["templates"] if t["name"] == body.item), None)
    else:
        item = next((f for f in lib["files"] if str(f["id"]) == body.item), None)
    if item is None:
        raise FieldError("item", f"{link.name} doesn't have that {body.kind} (any more).", status_code=404)
    job = Job(type="linked.copy", requested_by=p.username, params={"link_id": link.id, "kind": body.kind, "item": item})
    db.add(job)
    db.commit()
    audit(db, p.username, "linked.copy.start", object_type="job", object_id=job.id,
          detail={"from": link.name, "kind": body.kind, "item": item.get("name")}, source_ip=client_ip(request))
    return {"job_id": job.id, "name": item["name"]}
