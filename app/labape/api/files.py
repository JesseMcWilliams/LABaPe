"""File manager (design §9, §21.3): ISOs, installers, other files.

Uploads use the tus 1.0 protocol (creation + termination), so a browser
upload of a multi-GB ISO is chunked and resumes after a dropped
connection. When the last chunk arrives, the file's SHA-256 is computed,
a file with the same content is refused as a duplicate, and the file
moves into its category's location. Admins and file managers upload,
edit and delete; everyone signed in can list.
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import FieldError
from ..files import CATEGORIES, infer_os, roots, set_roots, sha256_of
from ..models import StoredFile, Upload, utcnow
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/files", tags=["files"])
TUS = {"Tus-Resumable": "1.0.0"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,200}$")
FIELDS = ("id", "category", "name", "path", "size", "mtime", "sha256", "os_key", "is_default", "description",
          "notes", "uploaded_by", "uploaded_at", "present")


def _out(f: StoredFile) -> dict:
    return {k: getattr(f, k) for k in FIELDS}


# --- tus ------------------------------------------------------------------

def _meta(header: str) -> dict:
    out = {}
    for pair in (header or "").split(","):
        if pair.strip():
            key, _, val = pair.strip().partition(" ")
            out[key] = base64.b64decode(val).decode("utf-8") if val else ""
    return out


@router.options("/tus")
@router.options("/tus/{upload_id}")
def tus_options(upload_id: str | None = None):
    return Response(status_code=204, headers={**TUS, "Tus-Version": "1.0.0", "Tus-Extension": "creation,termination"})


@router.post("/tus")
def tus_create(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("file_manager"))):
    meta = _meta(request.headers.get("upload-metadata", ""))
    category = meta.get("category", "")
    name = Path(meta.get("filename", "")).name.strip()
    try:
        length = int(request.headers.get("upload-length", ""))
    except ValueError:
        raise FieldError("file", "The upload didn't say how big the file is (Upload-Length).") from None
    if category not in CATEGORIES:
        raise FieldError("category", f"Choose a category: {', '.join(CATEGORIES)}.")
    if not NAME_RE.match(name):
        raise FieldError("file", "File names may use letters, digits, spaces and . _ ( ) + -, starting with a "
                                 "letter or digit (up to 200 characters).")
    root = Path(roots(db)[category])
    if (root / name).exists():
        raise FieldError("file", f"{name} already exists in {root}. Delete it first, or rename the file.",
                         status_code=409)
    (root / ".uploads").mkdir(parents=True, exist_ok=True)
    up = Upload(id=secrets.token_hex(16), category=category, filename=name, length=length,
                partial_path=str(root / ".uploads" / f"{secrets.token_hex(8)}.part"),
                meta={"description": meta.get("description", "")}, created_by=p.username)
    Path(up.partial_path).touch()
    db.add(up)
    db.commit()
    return Response(status_code=201, headers={**TUS, "Location": f"/api/files/tus/{up.id}"})


def _upload(db: Session, upload_id: str, p: Principal) -> Upload:
    up = db.get(Upload, upload_id)
    if up is None or (up.created_by != p.username and not p.is_admin):
        raise HTTPException(status_code=404, detail="No such upload", headers=TUS)
    return up


@router.head("/tus/{upload_id}")
def tus_head(upload_id: str, db: Session = Depends(get_db), p: Principal = Depends(require_roles("file_manager"))):
    up = _upload(db, upload_id, p)
    return Response(status_code=200, headers={**TUS, "Upload-Offset": str(up.offset), "Upload-Length": str(up.length),
                                              "Cache-Control": "no-store"})


@router.patch("/tus/{upload_id}")
async def tus_patch(upload_id: str, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("file_manager"))):
    up = _upload(db, upload_id, p)
    if request.headers.get("content-type") != "application/offset+octet-stream":
        raise HTTPException(status_code=415, detail="Expected application/offset+octet-stream", headers=TUS)
    if int(request.headers.get("upload-offset", -1)) != up.offset:
        raise HTTPException(status_code=409, detail=f"Offset mismatch; the server has {up.offset} bytes",
                            headers=TUS)
    written = 0
    with open(up.partial_path, "ab") as fh:
        async for chunk in request.stream():
            if up.offset + written + len(chunk) > up.length:
                raise HTTPException(status_code=413, detail="More data than the declared length", headers=TUS)
            fh.write(chunk)
            written += len(chunk)
    up.offset += written
    db.commit()
    if up.offset == up.length:
        _finish(db, up, request, p)
    return Response(status_code=204, headers={**TUS, "Upload-Offset": str(up.offset)})


def _finish(db: Session, up: Upload, request: Request, p: Principal) -> None:
    partial = Path(up.partial_path)
    digest = sha256_of(partial)
    dup = db.scalar(select(StoredFile).where(StoredFile.sha256 == digest, StoredFile.present.is_(True)))
    if dup is not None:
        partial.unlink(missing_ok=True)
        db.delete(up)
        db.commit()
        raise FieldError("file", f"This file has the same content as {dup.name} ({dup.category}); it wasn't kept.",
                         status_code=409)
    target = Path(roots(db)[up.category]) / up.filename
    partial.replace(target)
    target.chmod(0o644)
    st = target.stat()
    f = StoredFile(category=up.category, name=up.filename, path=str(target), size=st.st_size,
                   mtime=dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc), sha256=digest,
                   os_key=infer_os(up.filename) if up.category == "isos" else "",
                   description=up.meta.get("description", ""), uploaded_by=p.username, uploaded_at=utcnow())
    db.add(f)
    db.delete(up)
    db.commit()
    audit(db, p.username, "file.upload", object_type="file", object_id=f.id,
          detail={"category": f.category, "name": f.name, "size": f.size, "sha256": digest},
          source_ip=client_ip(request))


@router.delete("/tus/{upload_id}")
def tus_delete(upload_id: str, db: Session = Depends(get_db), p: Principal = Depends(require_roles("file_manager"))):
    up = _upload(db, upload_id, p)
    Path(up.partial_path).unlink(missing_ok=True)
    db.delete(up)
    db.commit()
    return Response(status_code=204, headers=TUS)


# --- locations ----------------------------------------------------------------

class RootsIn(BaseModel):
    isos: str = ""
    installers: str = ""
    files: str = ""


@router.get("/roots")
def get_roots(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    return roots(db)


@router.put("/roots")
def put_roots(body: RootsIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    for k, v in body.model_dump().items():
        if v and not v.startswith("/"):
            raise FieldError(k, "Use an absolute path such as /data/isos.")
    new = set_roots(db, body.model_dump())
    audit(db, p.username, "files.roots", detail=new, source_ip=client_ip(request))
    return new


# --- files --------------------------------------------------------------------

@router.get("")
def list_files(category: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    q = select(StoredFile).where(StoredFile.present.is_(True)).order_by(StoredFile.category, StoredFile.name)
    if category:
        q = q.where(StoredFile.category == category)
    return [_out(f) for f in db.scalars(q)]


class FileIn(BaseModel):
    description: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=8000)
    os_key: str = Field(default="", pattern=r"^[a-z0-9_]*$")
    is_default: bool = False


@router.put("/{file_id}")
def update_file(file_id: int, body: FileIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("file_manager"))):
    f = db.get(StoredFile, file_id)
    if f is None:
        raise FieldError("file", "No such file.", status_code=404)
    if body.is_default and not body.os_key:
        raise FieldError("is_default", "Choose the OS this ISO installs before making it the default.")
    if body.is_default:  # one default ISO per OS
        for other in db.scalars(select(StoredFile).where(StoredFile.category == f.category,
                                                         StoredFile.os_key == body.os_key, StoredFile.id != f.id)):
            other.is_default = False
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    db.commit()
    audit(db, p.username, "file.update", object_type="file", object_id=f.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(f)


@router.delete("/{file_id}")
def delete_file(file_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("file_manager"))):
    f = db.get(StoredFile, file_id)
    if f is None:
        raise FieldError("file", "No such file.", status_code=404)
    Path(f.path).unlink(missing_ok=True)
    f.present = False
    db.commit()
    audit(db, p.username, "file.delete", object_type="file", object_id=f.id, detail={"path": f.path},
          source_ip=client_ip(request))
    return {"ok": True}


# Last, so /roots and /tus match first.
@router.get("/{file_id}")
def get_file(file_id: int, db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    f = db.get(StoredFile, file_id)
    if f is None or not f.present:
        raise FieldError("file", "No such file.", status_code=404)
    return _out(f)
