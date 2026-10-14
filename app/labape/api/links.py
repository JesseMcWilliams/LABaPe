"""Linked stacks (design §34): the admin's side (link codes, linking, unlinking) and the side
other stacks call (`/api/link/*`, authenticated by the link's token, not a sign-in)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ca, links
from ..db import get_db
from ..models import StackLink
from ..security import Principal, audit, client_ip, require_roles

router = APIRouter(tags=["links"])


class CodeIn(BaseModel):
    code: str = Field(min_length=10, max_length=2000)


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
    return {"self": {"url": links.own_url(), "ca_fingerprint": links.fingerprint(ca.root_pem())},
            "links": [links.out(x) for x in db.scalars(select(StackLink).order_by(StackLink.name))]}


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
