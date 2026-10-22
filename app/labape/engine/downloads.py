"""Files from a URL (design §22.3, questions 71-72).

The worker downloads into the category's location as a `files.download`
job: progress in the job log, a retry resumes the partial file with an
HTTP Range request, and cancel stops it. HTTPS only unless Settings >
Downloads allows HTTP; link-local addresses (169.254.0.0/16, fe80::/10,
which include cloud metadata endpoints) are refused, at every redirect.
LABaPe's own hash is always SHA-256; an expected value from the vendor may
be SHA-256, SHA-1, SHA-512 or MD5, and a mismatch deletes the file.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, ostargets
from ..db import session_factory
from ..files import infer_os, roots
from ..models import Job, StoredFile, utcnow
from ..security import audit

HASH_LENGTHS = {"sha256": 64, "sha1": 40, "sha512": 128, "md5": 32}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,200}$")
CHUNK = 1024 * 1024
PROGRESS_SECONDS = 10


class DownloadError(ValueError):
    pass


def parse_expected(text: str, hash_type: str) -> str:
    """A bare hex digest, a `SHA256SUMS` line (`<hex>  <name>`) or a BSD line
    (`SHA256 (name) = <hex>`) -> the lower-case digest."""
    if hash_type not in HASH_LENGTHS:
        raise DownloadError(f"Hash type must be one of {', '.join(HASH_LENGTHS)}.")
    n = HASH_LENGTHS[hash_type]
    found = re.findall(rf"(?<![0-9a-fA-F])[0-9a-fA-F]{{{n}}}(?![0-9a-fA-F])", text or "")
    if len(found) != 1:
        raise DownloadError(f"Paste one {hash_type.upper()} value ({n} hex characters), or one checksum-file line.")
    return found[0].lower()


def check_url(url: str, allow_http: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in (("https", "http") if allow_http else ("https",)):
        raise DownloadError("Only https:// addresses are allowed." if not allow_http
                            else "Use an http:// or https:// address.")
    if not parts.hostname:
        raise DownloadError("The address has no host name.")
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return
    if _link_local(ip):
        raise DownloadError("Link-local addresses (169.254.0.0/16, fe80::/10) aren't allowed.")


def _link_local(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_link_local


def check_resolves(host: str) -> None:
    """Refuse a host name that resolves to a link-local address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DownloadError(f"Can't resolve {host}: {exc}") from exc
    for info in infos:
        if _link_local(ipaddress.ip_address(info[4][0].split("%")[0])):
            raise DownloadError(f"{host} resolves to a link-local address, which isn't allowed.")


def filename_from(url: str) -> str:
    return unquote(Path(urlsplit(url).path).name)


def _partial(root: str, url: str, name: str) -> str:
    # Keyed by URL and name, so a retried job resumes the same partial file.
    return f"{root.rstrip('/')}/.downloads/{hashlib.sha1(url.encode()).hexdigest()[:12]}-{name}.part"


def _beat(job_id: int) -> bool:
    """Heartbeat; True if a cancel was requested."""
    with session_factory()() as s:
        row = s.get(Job, job_id)
        row.heartbeat_at = utcnow()
        cancel = row.cancel_requested
        s.commit()
    return cancel


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


class Cancelled(DownloadError):
    pass


def fetch(db: Session, url: str, category: str, name: str, log, *, expected: str = "", hash_type: str = "sha256",
          os_key: str = "", targets: list | None = None, description: str = "", actor: str = "system",
          beat=None, headers: dict | None = None, verify=None, target: str = "", register: bool = True):
    """Download `url` into the category's location as `name` (on the primary file host), checking each
    redirect, resuming a partial file, verifying `expected` (a `hash_type` digest) and refusing content
    already stored. Returns the new file's row; raises DownloadError (Cancelled when `beat()` says so).

    A linked stack's file (backlog row 22) passes `headers` (its link token), `verify` (an SSL context pinned to its
    platform CA) and, for a template, `target` (a path in the primary's template library) with `register=False`: the
    file is then only stored, and a namespace with `path`, `size` and `sha256` comes back instead of a row."""
    cfg = appsettings.get(db, "downloads")
    from .. import filestore
    store = filestore.current(db)             # on the primary file host (design §29.5)
    root = (target.rsplit("/", 1)[0] if target else roots(db)[category].rstrip("/"))
    target = target or f"{root}/{name}"
    if store.exists(target):
        raise DownloadError(f"{target} already exists; delete it first.")
    if store.remote:
        log.write(f"labape: saving on the primary file host, {store.where}\n")
    part = _partial(root, url, name)
    store.makedirs(f"{root}/.downloads")
    first = url
    with httpx.Client(timeout=httpx.Timeout(30, read=120), proxy=None if verify is not None else (cfg.proxy or None),
                      follow_redirects=False, **({"verify": verify} if verify is not None else {})) as c:
        for _ in range(10):                       # follow redirects ourselves, checking each hop
            check_url(url, cfg.allow_http)
            if not cfg.proxy:
                check_resolves(urlsplit(url).hostname)
            have = max(store.size(part), 0)
            req_headers = {**(headers or {}), **({"Range": f"bytes={have}-"} if have else {})}
            with c.stream("GET", url, headers=req_headers) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    log.write(f"labape: redirected to {url}\n")
                    continue
                if r.status_code == 416 and have:      # already complete
                    log.write("labape: the partial file is already complete.\n")
                else:
                    r.raise_for_status()
                    resumed = r.status_code == 206
                    if have and not resumed:
                        log.write("labape: the server doesn't resume downloads; starting over.\n")
                    # A compressed transfer's Content-Length counts the compressed bytes, not the file's.
                    enc = r.headers.get("content-encoding", "identity").lower()
                    total = r.headers.get("content-length") if enc in ("", "identity") else None
                    total = (int(total) + (have if resumed else 0)) if total else None
                    log.write(f"labape: downloading {url}"
                              f"{f' ({_human(total)})' if total else ''}"
                              f"{f', resuming at {_human(have)}' if resumed else ''}\n")
                    done, started, last = (have if resumed else 0), time.monotonic(), 0.0
                    with store.open_write(part, append=resumed) as fh:
                        for chunk in r.iter_bytes(CHUNK):
                            fh.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if now - last >= PROGRESS_SECONDS:
                                last = now
                                rate = (done - (have if resumed else 0)) / max(now - started, 0.001)
                                log.write(f"labape: {_human(done)}"
                                          f"{f' of {_human(total)} ({done * 100 // total}%)' if total else ''}"
                                          f" at {_human(rate)}/s\n")
                                if beat and beat():
                                    raise Cancelled("cancelled; the partial file is kept for a retry.")
                break
        else:
            raise DownloadError("too many redirects.")

    log.write("labape: download complete; checking hashes\n")
    sums = store.hashes(part, {"sha256", hash_type or "sha256"})
    sha256 = sums["sha256"]
    if expected:
        got = sums[hash_type]
        if got != expected:
            store.remove(part)
            raise DownloadError(f"{hash_type.upper()} mismatch: expected {expected}, got {got}. The file was deleted.")
        log.write(f"labape: {hash_type.upper()} matches the expected value.\n")
    dup = db.scalar(select(StoredFile).where(StoredFile.sha256 == sha256, StoredFile.present.is_(True))) if register else None
    if dup is not None:
        store.remove(part)
        raise DownloadError(f"the same content is already stored as {dup.name} ({dup.category}); not kept.")
    store.replace(part, target)
    size, mtime = store.stat(target)
    if not register:
        log.write(f"labape: saved {target} ({_human(size)}), SHA-256 {sha256}\n")
        from types import SimpleNamespace
        return SimpleNamespace(path=target, size=size, sha256=sha256)
    f = StoredFile(category=category, name=name, path=target, size=size, mtime=mtime, sha256=sha256,
                   os_key=(os_key or infer_os(name)) if category == "isos" else "",
                   targets=targets or (ostargets.infer(name) if category == "installers" else []),
                   description=description, uploaded_by=actor, uploaded_at=utcnow())
    db.add(f)
    db.commit()
    audit(db, actor, "file.download", object_type="file", object_id=f.id,
          detail={"url": first, "category": f.category, "name": name, "size": f.size, "sha256": sha256,
                  "verified": hash_type if expected else ""})
    log.write(f"labape: saved {target} ({_human(size)}){f' on {store.where}' if store.remote else ''}, "
              f"SHA-256 {sha256}\n")
    return f


def run_download(db: Session, job: Job) -> int:
    p = job.params or {}
    log = open(job.log_path, "a", encoding="utf-8", buffering=1)
    try:
        fetch(db, p["url"], p["category"], p["filename"], log, expected=p.get("expected_hash") or "",
              hash_type=p.get("hash_type") or "sha256", os_key=p.get("os_key") or "", targets=p.get("targets"),
              description=p.get("description", ""), actor=job.requested_by, beat=lambda: _beat(job.id))
        return 0
    except Cancelled as exc:
        log.write(f"labape: {exc}\n")
        return 130
    except (DownloadError, httpx.HTTPError, OSError) as exc:
        log.write(f"labape: download failed: {exc}\n")
        return 1
    finally:
        log.close()
