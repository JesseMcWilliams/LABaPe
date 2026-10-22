"""Templates and files of a linked stack (backlog row 22).

Each stack offers its template library (the primary file host's) and its stored files (ISOs, installers, other
files) to the stacks linked with it, read-only: a listing and a download that supports `Range` (so a copy resumes).
The other side picks one in its Template drop-down or on the Files page, and a job copies it onto its own primary file
host (`engine/linked_copy.py`); a template chosen for a host group is copied when the environment is deployed.

Both calls use the link's token and the peer's platform CA root (`links.client`), like the summary."""
from __future__ import annotations

import shlex
import subprocess
from typing import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import links
from .models import KvmHost, StackLink, StoredFile, Template
from .security import get_secret

CHUNK = 1024 * 1024
TEMPLATE_KEYS = ("os_key", "edition", "sku", "disk_gb", "min_cpu", "min_memory_mb", "description", "notes")


class LibraryError(Exception):
    pass


# --- this stack's side: what it offers ------------------------------------------------------------

def _primary(db: Session) -> KvmHost | None:
    from .engine import filedist
    return filedist.primary(db)


def _template_path(p: KvmHost, name: str) -> str:
    return f"{p.template_storage_path.rstrip('/')}/{name}.qcow2"


def local_library(db: Session) -> dict:
    """The templates of the primary's library and the stored files, as a peer sees them."""
    from .engine import filedist
    p = _primary(db)
    templates = []
    if p is not None:
        ep = filedist.endpoint(p)
        for t in db.scalars(select(Template).where(Template.kvm_host_id == p.id, Template.present.is_(True))
                            .order_by(Template.name)):
            templates.append({"name": t.name, "size": max(filedist.size_of(ep, _template_path(p, t.name)), 0),
                              **{k: getattr(t, k) for k in TEMPLATE_KEYS}})
    files = [{"id": f.id, "category": f.category, "name": f.name, "size": f.size, "sha256": f.sha256,
              "os_key": f.os_key, "targets": f.targets or [], "description": f.description}
             for f in db.scalars(select(StoredFile).where(StoredFile.present.is_(True)).order_by(StoredFile.category,
                                                                                               StoredFile.name))]
    return {"templates": [t for t in templates if t["size"] > 0], "files": files}


def open_local(db: Session, kind: str, ident: str, offset: int = 0) -> tuple[Iterator[bytes], int]:
    """(chunks of the file from `offset`, its full size) for a template (by name) or a stored file (by id)."""
    from .engine import filedist
    from . import filestore
    if kind == "template":
        p = _primary(db)
        t = db.scalar(select(Template).where(Template.kvm_host_id == p.id, Template.name == ident,
                                             Template.present.is_(True))) if p else None
        if t is None:
            raise LibraryError(f"no template {ident}")
        path, ep = _template_path(p, t.name), filedist.endpoint(p)
        size = filedist.size_of(ep, path)
    elif kind == "file":
        f = db.get(StoredFile, int(ident)) if ident.isdigit() else None
        if f is None or not f.present:
            raise LibraryError(f"no file {ident}")
        path, ep = f.path, None
        store = filestore.current(db)
        size = store.size(path)
        if store.remote:
            ep = store.host
    else:
        raise LibraryError("kind is template or file")
    if size < 0:
        raise LibraryError("the file isn't there any more")
    return _read(ep, path, offset), size


def _read(ep, path: str, offset: int) -> Iterator[bytes]:
    if ep is None:
        with open(path, "rb") as fh:
            fh.seek(offset)
            while block := fh.read(CHUNK):
                yield block
        return
    from .engine import hostio
    cmd = f"tail -c +{offset + 1} {shlex.quote(path)}" if offset else f"cat {shlex.quote(path)}"
    proc = subprocess.Popen([*ep.ssh_base(), cmd], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=hostio.tool_env())
    try:
        while block := proc.stdout.read(CHUNK):
            yield block
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


# --- the calling side: reading a peer's ------------------------------------------------------------

def peer_headers(db: Session, link: StackLink) -> dict[str, str]:
    token = get_secret(db, links._secret_name(link.id))
    if not token:
        raise LibraryError("no token for this link in the secret store; link the stacks again")
    return {"Authorization": f"Bearer {token}"}


def peer_library(db: Session, link: StackLink) -> dict:
    try:
        with links.client(link.ca_pem) as h:
            r = h.get(f"{link.url}/api/link/library", headers=peer_headers(db, link))
        if r.status_code in (401, 403):
            raise LibraryError("the other stack no longer accepts this link")
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LibraryError(f"{link.name} didn't answer: {str(exc)[:200] or exc.__class__.__name__}") from exc


def all_libraries(db: Session) -> list[dict]:
    """Every linked stack's templates and files; a stack that doesn't answer is listed with its error."""
    out = []
    for link in db.scalars(select(StackLink).order_by(StackLink.name)):
        try:
            out.append({"link_id": link.id, "name": link.name, "url": link.url, "ok": True, "error": "",
                        **peer_library(db, link)})
        except LibraryError as exc:
            out.append({"link_id": link.id, "name": link.name, "url": link.url, "ok": False, "error": str(exc),
                        "templates": [], "files": []})
    return out
