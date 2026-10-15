"""Catalog → Updates (U1, question 170; design §50.7): download sites checked weekly, new versions
downloaded into Files → Installers, the newest N kept per source.

A source is a package and where its newest version is found:
  dotnet      Microsoft's release feed for a channel (8.0, 10.0): the runtime, the ASP.NET Core
              hosting bundle or the SDK, for a runtime identifier (win-x64, linux-x64); SHA-512 checked
  github      a repository's latest release, the asset whose name matches a pattern; SHA-256 checked
              when GitHub publishes the asset's digest
  url         a fixed address (e.g. Chrome's enterprise MSI); the version is taken from the address it
              redirects to (version_regex) or else from its ETag / Last-Modified
  page        a web page that links the newest version (e.g. PuTTY's latest.html): link_regex, whose
              first group is the address and second the version
  chocolatey  a Chocolatey community package's newest .nupkg (its version from the file name)
The `files.update_check` job checks every enabled source (or one), downloads what's new with the
same code as Files → Download (redirects checked, resumed, hashed, duplicates refused), records the
version, and deletes the oldest copies beyond the source's `keep`.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import Job, StoredFile, UpdateSource, utcnow

KINDS = ("dotnet", "github", "url", "page", "chocolatey")
DOTNET_FEED = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/{channel}/releases.json"
DOTNET_SECTIONS = {"runtime": "runtime", "hosting": "aspnetcore-runtime", "aspnetcore": "aspnetcore-runtime",
                   "sdk": "sdk"}
TIMEOUT = httpx.Timeout(30, read=60)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class UpdateError(ValueError):
    pass


class Found(dict):
    """version, url, filename, hash_type, expected"""


def _client(db: Session) -> httpx.Client:
    cfg = appsettings.get(db, "downloads", cached=True)
    return httpx.Client(timeout=TIMEOUT, proxy=cfg.proxy or None, follow_redirects=True,
                        headers={"User-Agent": "LABaPe package updates"})


def _dotnet(c: httpx.Client, p: dict) -> Found:
    r = c.get(DOTNET_FEED.format(channel=p["channel"]))
    r.raise_for_status()
    feed = r.json()
    latest = (feed.get("releases") or [{}])[0]
    section = latest.get(DOTNET_SECTIONS.get(p.get("product", "runtime"), "runtime")) or {}
    rid = p.get("rid", "win-x64")
    windows = rid.startswith("win")
    files = section.get("files") or []
    if p.get("product") == "hosting":
        pick = next((f for f in files if f.get("name") == "dotnet-hosting-win.exe"), None)
    else:
        pick = next((f for f in files if f.get("rid") == rid
                     and str(f.get("name", "")).endswith(".exe" if windows else ".tar.gz")), None)
    if not pick:
        raise UpdateError(f"no {p.get('product')} file for {rid} in .NET {p['channel']}'s release feed")
    url = pick["url"]
    return Found(version=str(section.get("version") or latest.get("release-version")), url=url,
                 filename=url.rsplit("/", 1)[-1], hash_type="sha512", expected=str(pick.get("hash") or "").lower())


def _github(c: httpx.Client, p: dict) -> Found:
    r = c.get(f"https://api.github.com/repos/{p['repo']}/releases/latest", headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    rel = r.json()
    asset = next((a for a in rel.get("assets") or [] if fnmatch.fnmatch(a.get("name", ""), p["asset"])), None)
    if not asset:
        raise UpdateError(f"{p['repo']}'s latest release has no asset matching {p['asset']}")
    digest = str(asset.get("digest") or "")
    return Found(version=str(rel.get("tag_name") or "").lstrip("v"), url=asset["browser_download_url"],
                 filename=asset["name"], hash_type="sha256",
                 expected=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "")


def _url(c: httpx.Client, p: dict) -> Found:
    r = c.head(p["url"])
    if r.status_code >= 400:
        r = c.get(p["url"], headers={"Range": "bytes=0-0"})
    final = str(r.url)
    version = ""
    if p.get("version_regex"):
        m = re.search(p["version_regex"], final)
        version = m.group(1) if m else ""
    if not version:
        mark = r.headers.get("etag") or r.headers.get("last-modified") or r.headers.get("content-length") or ""
        if not mark:
            raise UpdateError(f"{p['url']}: no version in its address and no ETag or Last-Modified to tell one by")
        version = "d" + hashlib.sha256(mark.encode()).hexdigest()[:10]
    return Found(version=version, url=final, filename=PurePosixPath(final.split("?")[0]).name, hash_type="sha256",
                 expected="")


def _page(c: httpx.Client, p: dict) -> Found:
    r = c.get(p["page"])
    r.raise_for_status()
    m = re.search(p["link_regex"], r.text)
    if not m:
        raise UpdateError(f"{p['page']}: no link matching the pattern")
    url = urljoin(str(r.url), m.group(1))
    version = m.group(2) if len(m.groups()) >= 2 and m.group(2) else ""
    if not version:
        raise UpdateError(f"{p['page']}: the pattern's second group (the version) matched nothing")
    return Found(version=version, url=url, filename=PurePosixPath(url.split("?")[0]).name, hash_type="sha256", expected="")


def _chocolatey(c: httpx.Client, p: dict) -> Found:
    pkg = p["package"]
    return _url(c, {"url": f"https://community.chocolatey.org/api/v2/package/{pkg}",
                    "version_regex": rf"{re.escape(pkg)}\.([0-9][0-9A-Za-z.\-]*)\.nupkg"})


RESOLVERS = {"dotnet": _dotnet, "github": _github, "url": _url, "page": _page, "chocolatey": _chocolatey}
REQUIRED = {"dotnet": ("channel", "product", "rid"), "github": ("repo", "asset"), "url": ("url",),
            "page": ("page", "link_regex"), "chocolatey": ("package",)}


def resolve(db: Session, src: UpdateSource, client: httpx.Client | None = None) -> Found:
    if src.kind not in RESOLVERS:
        raise UpdateError(f"unknown source kind {src.kind}")
    c = client or _client(db)
    try:
        return RESOLVERS[src.kind](c, src.params or {})
    except httpx.HTTPError as exc:
        raise UpdateError(f"{src.name}: {exc}") from exc
    finally:
        if client is None:
            c.close()


def stored_name(src: UpdateSource, found: Found) -> str:
    """The file name kept: the vendor's, with the version added when it hasn't one (Chrome's MSI)."""
    name = re.sub(r"[^A-Za-z0-9._()+-]", "_", found["filename"]) or src.name
    if found["version"] not in name:
        stem, dot, ext = name.partition(".")
        name = f"{stem}-{found['version']}{dot}{ext}"
    return name[:200]


def check(db: Session, src: UpdateSource, log, client: httpx.Client | None = None, fetch=None) -> str:
    """Check one source, download a new version, keep the newest `keep`. Returns what happened."""
    from . import downloads
    fetch = fetch or downloads.fetch
    src.last_checked_at = utcnow()
    try:
        found = resolve(db, src, client)
    except UpdateError as exc:
        src.last_error = str(exc)[:500]
        db.commit()
        return f"{src.name}: {exc}"
    history = list(src.history or [])
    if any(h.get("version") == found["version"] for h in history):
        src.last_error = ""
        db.commit()
        return f"{src.name}: {found['version']} is the newest; already have it"
    log.write(f"labape: {src.name}: new version {found['version']}\n")
    try:
        f = fetch(db, found["url"], "installers", stored_name(src, found), log, expected=found["expected"],
                  hash_type=found["hash_type"], targets=list(src.targets or []),
                  description=f"{src.display_name or src.name} {found['version']} (Catalog → Updates)", actor="system")
    except Exception as exc:  # noqa: BLE001 — downloads raise DownloadError, HTTP and file errors alike
        src.last_error = f"downloading {found['version']}: {exc}"[:500]
        db.commit()
        return f"{src.name}: downloading {found['version']} failed: {exc}"
    history.insert(0, {"version": found["version"], "file_id": f.id, "name": f.name,
                       "at": utcnow().isoformat(timespec="seconds")})
    removed = []
    for old in history[max(int(src.keep or 1), 1):]:
        row = db.get(StoredFile, old.get("file_id") or 0)
        if row is not None and row.present:
            try:
                from .. import filestore
                filestore.current(db).remove(row.path)
                row.present = False
                removed.append(old["version"])
            except Exception as exc:  # noqa: BLE001 — kept in the history so the next check tries again
                log.write(f"labape: {src.name}: couldn't delete {row.name}: {exc}\n")
                continue
    history = [h for h in history if h.get("version") not in removed]
    src.history, src.last_version, src.last_error = history, found["version"], ""
    db.commit()
    note = f"; removed {', '.join(removed)}" if removed else ""
    return f"{src.name}: downloaded {found['version']} ({f.name}){note}"


def run_job(db: Session, job: Job) -> int:
    """The `files.update_check` job: every enabled source, or params.source_id."""
    from .runner import _append

    class _Log:
        def write(self, text: str) -> None:
            _append(job, text)

    only = (job.params or {}).get("source_id")
    q = select(UpdateSource).order_by(UpdateSource.name)
    q = q.where(UpdateSource.id == only) if only else q.where(UpdateSource.enabled.is_(True))
    failed = 0
    with _client(db) as c:
        for src in db.scalars(q):
            what = check(db, src, _Log(), c)
            _append(job, f"labape: {what}\n")
            failed += bool(src.last_error)
    return 1 if failed else 0


def due(db: Session, now: dt.datetime | None = None) -> bool:
    """Whether this week's check is due (Settings: day and hour, the stack's local time is UTC)."""
    cfg = appsettings.get(db, "package_updates", cached=True)
    if not cfg.enabled:
        return False
    now = now or utcnow()
    slot = (now - dt.timedelta(days=(now.weekday() - DAYS.index(cfg.day)) % 7)).replace(
        hour=cfg.hour, minute=0, second=0, microsecond=0)
    if slot > now:
        slot -= dt.timedelta(days=7)
    recent = db.scalars(select(Job).where(Job.type == "files.update_check").order_by(Job.created_at.desc()).limit(20))
    last = next((j.created_at for j in recent if (j.params or {}).get("scheduled")), None)
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return last is None or last < slot


def schedule(db: Session) -> list[int]:
    if not due(db):
        return []
    if db.scalar(select(Job.id).where(Job.type == "files.update_check", Job.state.in_(("queued", "running")))):
        return []
    job = Job(type="files.update_check", params={"scheduled": True}, requested_by="system")
    db.add(job)
    db.commit()
    return [job.id]


PUTTY = r'href="(https://the\.earth\.li/~sgtatham/putty/latest/w64/putty-64bit-([0-9.]+)-installer\.msi)"'
SEED = [
    *[(f"dotnet-{ch}-{prod}-win", f".NET {ch} {label} (Windows)", "dotnet",
       {"channel": f"{ch}.0", "product": prod, "rid": "win-x64"})
      for ch in ("8", "10") for prod, label in (("runtime", "runtime"), ("hosting", "hosting bundle"), ("sdk", "SDK"))],
    ("chrome-win", "Google Chrome (Windows, enterprise MSI)", "url",
     {"url": "https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi"}),
    ("keepassxc-win", "KeePassXC (Windows MSI)", "github", {"repo": "keepassxreboot/keepassxc", "asset": "KeePassXC-*-Win64.msi"}),
    ("putty-win", "PuTTY (Windows MSI)", "page",
     {"page": "https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html", "link_regex": PUTTY}),
]
SEEDED = "package_updates.seeded"


def seed(db: Session) -> int:
    """The round's sources (question 171: .NET 8 and 10, Chrome, KeePassXC, PuTTY; Windows), once: a
    source an admin deletes doesn't come back."""
    from ..models import Setting
    if db.get(Setting, SEEDED) is not None:
        return 0
    have = set(db.scalars(select(UpdateSource.name)))
    n = 0
    for name, display, kind, params in SEED:
        if name not in have:
            db.add(UpdateSource(name=name, display_name=display, kind=kind, params=params, targets=["windows"], keep=3,
                                enabled=True, history=[]))
            n += 1
    db.add(Setting(key=SEEDED, value={"at": utcnow().isoformat(timespec="seconds")}))
    db.commit()
    return n


def to_dict(src: UpdateSource) -> dict:
    return {c: getattr(src, c) for c in ("id", "name", "display_name", "kind", "params", "targets", "keep", "enabled",
                                         "last_checked_at", "last_version", "last_error", "history")}


def validate(kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise UpdateError(f"kind must be one of {', '.join(KINDS)}")
    missing = [k for k in REQUIRED[kind] if not str(params.get(k, "")).strip()]
    if missing:
        raise UpdateError(f"{kind} needs {', '.join(missing)}")
    for k in ("version_regex", "link_regex"):
        if params.get(k):
            try:
                re.compile(params[k])
            except re.error as exc:
                raise UpdateError(f"{k}: {exc}") from exc
    for k in ("url", "page"):
        if params.get(k) and not str(params[k]).startswith("https://"):
            raise UpdateError(f"{k} must be an https:// address")
    if kind == "dotnet" and params.get("product") not in DOTNET_SECTIONS:
        raise UpdateError(f"product must be one of {', '.join(DOTNET_SECTIONS)}")
    return {k: str(v).strip() for k, v in params.items() if str(v).strip()}

