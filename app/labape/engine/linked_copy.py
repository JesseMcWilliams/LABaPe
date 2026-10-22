"""Copy a template or a file from a linked stack onto this stack's primary file host (backlog row 22).

A job (`linked.copy`, started from the Files page) copies one item; a host group that chose a linked stack's
template has it copied by the deploy (`ensure_template`, called where the deploy distributes templates). The
download is `downloads.fetch` with the link's token and the peer's platform CA, so it resumes, checks the SHA-256 the
peer reported, and refuses a name that is already taken."""
from __future__ import annotations

import json
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import filestore, linked_library, links
from ..models import Job, StackLink, Template
from . import downloads, filedist
from . import templates as template_sync


class CopyError(Exception):
    pass


def _link(db: Session, link_id: int) -> StackLink:
    link = db.get(StackLink, link_id)
    if link is None:
        raise CopyError("that linked stack isn't linked any more")
    return link


def _url(link: StackLink, kind: str, ident) -> str:
    return f"{link.url}/api/link/download/{kind}/{quote(str(ident), safe='')}"


def copy_file(db: Session, link: StackLink, item: dict, log, actor: str, beat=None):
    """A stored file of the peer, into the same category here."""
    downloads.fetch(db, _url(link, "file", item["id"]), item["category"], item["name"], log,
                    expected=item.get("sha256") or "", os_key=item.get("os_key") or "", targets=item.get("targets") or [],
                    description=item.get("description") or "", actor=actor, beat=beat,
                    headers=linked_library.peer_headers(db, link), verify=links.ssl_context(link.ca_pem))


def copy_template(db: Session, link: StackLink, item: dict, log, beat=None) -> None:
    """A template of the peer into the primary's library (image, then metadata), registered by a library sync."""
    p = filedist.primary(db)
    if p is None:
        raise CopyError("no KVM host is the primary file host (Admin → KVM hosts)")
    name = item["name"]
    have = db.scalar(select(Template).where(Template.kvm_host_id == p.id, Template.name == name, Template.present.is_(True)))
    if have is not None:
        log.write(f"labape: the primary already has a template named {name}; not copying it\n")
        return
    store = filestore.current(db)
    lib = p.template_storage_path.rstrip("/")
    downloads.fetch(db, _url(link, "template", name), "files", f"{name}.qcow2", log, beat=beat,
                    headers=linked_library.peer_headers(db, link), verify=links.ssl_context(link.ca_pem),
                    target=f"{lib}/{name}.qcow2", register=False)
    meta = {k: item.get(k) for k in template_sync.META_KEYS}
    with store.open_write(f"{lib}/{name}.json") as fh:
        fh.write((json.dumps(meta, indent=2) + "\n").encode())
    template_sync.sync_host(db, p)
    log.write(f"labape: template {name} is in the primary's library (from {link.name})\n")


def ensure_template(db: Session, link_id: int, name: str, log) -> bool:
    """For a deploy: the primary's library gets `name` from the linked stack when it doesn't have it yet.
    False when it was already there."""
    p = filedist.primary(db)
    if p is not None and db.scalar(select(Template).where(Template.kvm_host_id == p.id, Template.name == name,
                                                          Template.present.is_(True))):
        return False
    link = _link(db, link_id)
    log.write(f"labape: template {name} comes from the linked stack {link.name}\n")
    try:
        items = {t["name"]: t for t in linked_library.peer_library(db, link)["templates"]}
    except linked_library.LibraryError as exc:
        raise CopyError(str(exc)) from exc
    if name not in items:
        raise CopyError(f"{link.name} has no template {name} (any more)")
    copy_template(db, link, items[name], log)
    return True


def run_copy(db: Session, job: Job) -> int:
    """Job `linked.copy`: params link_id, kind (file|template), item (the peer's listing entry)."""
    p = job.params or {}
    log = open(job.log_path, "a", encoding="utf-8", buffering=1)
    try:
        link = _link(db, p["link_id"])
        beat = lambda: downloads._beat(job.id)  # noqa: E731
        if p["kind"] == "template":
            copy_template(db, link, p["item"], log, beat)
        else:
            copy_file(db, link, p["item"], log, job.requested_by, beat)
        return 0
    except downloads.Cancelled as exc:
        log.write(f"labape: {exc}\n")
        return 130
    except (CopyError, downloads.DownloadError, linked_library.LibraryError, OSError) as exc:
        log.write(f"labape: copy failed: {exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001 — a network failure ends the job with its message, not a stack trace
        log.write(f"labape: copy failed: {exc}\n")
        return 1
    finally:
        log.close()
