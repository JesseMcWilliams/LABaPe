"""Linked stacks (design §34): the admin's side (link codes, linking, unlinking) and the side
other stacks call (`/api/link/*`, authenticated by the link's token, not a sign-in)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ca, linked_library, links, linksso
from ..db import get_db
from ..models import StackLink
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(tags=["links"])


class CodeIn(BaseModel):
    code: str = Field(min_length=10, max_length=2000)


class SsoProviderIn(BaseModel):
    enabled: bool
    redirect_uri: str = Field(default="", max_length=500)


class SignOnIn(BaseModel):
    enabled: bool


class JoinIn(BaseModel):
    code: str = Field(min_length=10, max_length=200)
    url: str = Field(pattern=r"^https://[A-Za-z0-9.:\[\]-]+$", max_length=255)
    ca_pem: str = Field(max_length=20000)
    token: str = Field(min_length=32, max_length=200)


# --- called by other stacks ------------------------------------------------------------------------

@router.get("/api/link/ca")
def link_ca():
    """Public: this stack's platform CA root; a joining stack checks it against the link code."""
    return {"pem": ca.root_pem()}


@router.post("/api/link/join")
def link_join(body: JoinIn, request: Request, db: Session = Depends(get_db)):
    try:
        out = links.accept(db, body.code, body.url, body.ca_pem, body.token)
    except links.LinkError as exc:
        audit(db, "link", "link.join", outcome="denied", detail={"url": body.url, "error": str(exc)},
              source_ip=client_ip(request))
        raise HTTPException(400, str(exc)) from exc
    audit(db, "link", "link.join", detail={"url": body.url}, source_ip=client_ip(request))
    return out


def _peer(db: Session, authorization: str | None) -> StackLink:
    link = links.caller(db, authorization)
    if link is None:
        raise HTTPException(401, "Not a linked stack")
    return link


@router.get("/api/link/summary")
def link_summary(db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    _peer(db, authorization)
    return links.summary(db)


@router.get("/api/link/library")
def link_library(db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    """The templates and files this stack offers a linked stack (backlog row 22)."""
    _peer(db, authorization)
    return linked_library.local_library(db)


@router.get("/api/link/download/{kind}/{ident}")
def link_download(kind: str, ident: str, db: Session = Depends(get_db), authorization: str | None = Header(default=None),
                  range: str | None = Header(default=None)):
    """One template (by name) or file (by id) for a linked stack; `Range: bytes=N-` resumes a partial copy."""
    import re
    link = _peer(db, authorization)
    start = 0
    if range:
        m = re.fullmatch(r"bytes=(\d+)-", range.strip())
        if not m:
            raise HTTPException(416, "Only open-ended ranges (bytes=N-) are supported")
        start = int(m.group(1))
    try:
        chunks, size = linked_library.open_local(db, kind, ident, start)
    except linked_library.LibraryError as exc:
        raise HTTPException(404, str(exc)) from exc
    if start >= size:
        raise HTTPException(416, "Nothing left to send", headers={"Content-Range": f"bytes */{size}"})
    audit(db, f"link:{links._name(link.url)}", "link.download", detail={"kind": kind, "item": ident, "from": start})
    headers = {"Content-Length": str(size - start), "Accept-Ranges": "bytes"}
    if start:
        headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
    return StreamingResponse(chunks, status_code=206 if start else 200, media_type="application/octet-stream", headers=headers)


@router.post("/api/link/sso/provider")
def link_sso_provider(body: SsoProviderIn, request: Request, db: Session = Depends(get_db),
                      authorization: str | None = Header(default=None)):
    """The linked stack lets this stack's users sign in there (design §47.2): make (or remove) the
    client its Authentik source uses."""
    link = _peer(db, authorization)
    try:
        out = linksso.provide(link, body.enabled, body.redirect_uri)
    except linksso.SsoError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, "link", "link.sso.provider", object_type="stack_link", object_id=link.id,
          detail={"enabled": body.enabled, "redirect_uri": body.redirect_uri}, source_ip=client_ip(request))
    return out


@router.post("/api/link/leave")
def link_leave(request: Request, db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    link = _peer(db, authorization)
    url = link.url
    links.remove(db, link, tell_peer=False)
    audit(db, "link", "link.leave", detail={"url": url}, source_ip=client_ip(request))
    return {"ok": True}


# --- the admin's side ------------------------------------------------------------------------------

@router.get("/api/admin/links")
def list_links(db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    rows = list(db.scalars(select(StackLink).order_by(StackLink.name)))
    for x in rows:
        linksso.state(db, x)                       # picks up the agent's result
    return {"self": {"url": links.own_url(), "ca_fingerprint": links.fingerprint(ca.root_pem())},
            "links": [links.out(x) for x in rows]}


@router.put("/api/admin/links/{link_id}/sign-on")
def set_sign_on(link_id: int, body: SignOnIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    """Let the linked stack's users sign in here with their own accounts (design §47.2; question 157:
    per link, on each side). They keep their role groups (question 156)."""
    link = db.get(StackLink, link_id)
    if link is None:
        raise HTTPException(404, "No such link")
    try:
        out = linksso.set_sign_on(db, link, body.enabled, p.username)
    except linksso.SsoError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, p.username, "link.sign_on", object_type="stack_link", object_id=link.id,
          detail={"enabled": body.enabled}, source_ip=client_ip(request))
    return out


@router.post("/api/admin/links/invite")
def make_invite(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    out = links.invite(db, p.username)
    audit(db, p.username, "link.invite", source_ip=client_ip(request))
    return out


@router.post("/api/admin/links")
def add_link(body: CodeIn, request: Request, db: Session = Depends(get_db),
             p: Principal = Depends(require_roles("admin"))):
    try:
        link = links.join(db, body.code, p.username)
    except links.LinkError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(db, p.username, "link.add", object_type="stack_link", object_id=link.id, detail={"url": link.url},
          source_ip=client_ip(request))
    links.poll(db, link)
    return links.out(link)


@router.post("/api/admin/links/{link_id}/poll")
def poll_link(link_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    link = db.get(StackLink, link_id)
    if link is None:
        raise HTTPException(404, "No such link")
    links.poll(db, link)
    return links.out(link)


@router.delete("/api/admin/links/{link_id}")
def delete_link(link_id: int, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    link = db.get(StackLink, link_id)
    if link is None:
        raise HTTPException(404, "No such link")
    url = link.url
    links.remove(db, link)
    audit(db, p.username, "link.remove", object_type="stack_link", object_id=link_id, detail={"url": url},
          source_ip=client_ip(request))
    return {"ok": True}


@router.post("/api/link/backups/{name}")
async def receive_backup(name: str, request: Request, db: Session = Depends(get_db),
                         authorization: str | None = Header(default=None),
                         x_sha256: str = Header(default=""), x_labape_sidecar: str = Header(default="")):
    """A linked stack's newest full backup (design §36): staged, checked against its SHA-256, then
    a job keeps it on this stack's primary file host. It's sealed with the other stack's key."""
    import base64
    import hashlib
    import json
    import shutil

    from ..engine import stackbackup
    from ..models import Job
    link = _peer(db, authorization)
    if not stackbackup.ARCHIVE_NAME.match(name) or len(x_sha256) != 64:
        raise HTTPException(400, "Not a full-stack backup archive")
    size = int(request.headers.get("content-length") or 0)
    d = stackbackup.incoming_dir(db)
    d.mkdir(parents=True, exist_ok=True)
    if size <= 0 or shutil.disk_usage(d).free < size * 1.2:
        raise HTTPException(507, "Not enough room here for that backup")
    peer = links._name(link.url)
    staged = d / f"{peer}--{name}"
    part = staged.with_name(staged.name + ".part")
    h, got = hashlib.sha256(), 0
    with open(part, "wb") as out:
        async for chunk in request.stream():
            out.write(chunk)
            h.update(chunk)
            got += len(chunk)
    if got != size or h.hexdigest() != x_sha256.lower():
        part.unlink(missing_ok=True)
        raise HTTPException(400, "The backup arrived damaged (size or SHA-256 differs); send it again")
    part.replace(staged)
    try:
        side = json.loads(base64.b64decode(x_labape_sidecar)) if x_labape_sidecar else {}
    except ValueError:
        side = {}
    side.update(received_from=link.url, sha256=x_sha256.lower())
    staged.with_name(staged.name + ".json").write_text(json.dumps(side, default=str), encoding="utf-8")
    job = Job(type="backup.store_linked", requested_by=f"link:{peer}",
              params={"staged": str(staged), "archive": name, "peer": peer, "peer_url": link.url, "size": size,
                      "created_at": side.get("created_at")})
    db.add(job)
    db.commit()
    audit(db, f"link:{peer}", "backup.linked.received", detail={"archive": name, "size": size, "job": job.id},
          source_ip=client_ip(request))
    return {"ok": True, "job": job.id}
