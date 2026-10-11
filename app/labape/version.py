"""The installed LABaPe version and the daily release check (design §22.1,
question 64). The image is built with LABAPE_VERSION set to `git describe`
(e.g. v0.2.0, or v0.2.0-3-gabc1234 for commits after the tag)."""
from __future__ import annotations

import os
import re
from importlib.metadata import PackageNotFoundError, version as _pkg_version

import httpx
from sqlalchemy.orm import Session

from . import appsettings
from .models import Setting, utcnow

STATUS_KEY = "status.updates"


def current() -> str:
    v = os.environ.get("LABAPE_VERSION", "").strip()
    if v and v != "dev":
        return v
    try:
        return f"v{_pkg_version('labape')}-dev"
    except PackageNotFoundError:
        return "dev"


def parse(v: str) -> tuple[int, int, int] | None:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def newer(latest: str, installed: str) -> bool:
    a, b = parse(latest), parse(installed)
    return bool(a and b and a > b)


def check(db: Session, timeout: float = 15) -> dict:
    """Ask GitHub for the latest release and store the result."""
    cfg = appsettings.get(db, "updates")
    status: dict = {"checked_at": utcnow().isoformat(), "latest": "", "url": "", "error": ""}
    if not cfg.check_enabled:
        status["error"] = "Update checks are turned off."
    else:
        try:
            r = httpx.get(f"https://api.github.com/repos/{cfg.repo}/releases/latest", timeout=timeout,
                          headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 404:
                status["error"] = "No releases published yet."
            else:
                r.raise_for_status()
                data = r.json()
                status.update(latest=data.get("tag_name", ""), url=data.get("html_url", ""))
        except (httpx.HTTPError, ValueError) as exc:
            status["error"] = f"Couldn't reach GitHub: {exc}"
    row = db.get(Setting, STATUS_KEY)
    if row is None:
        db.add(Setting(key=STATUS_KEY, value=status))
    else:
        row.value = status
    db.commit()
    return status


def status(db: Session) -> dict:
    row = db.get(Setting, STATUS_KEY)
    s = dict(row.value) if row else {"checked_at": None, "latest": "", "url": "", "error": "Not checked yet."}
    s["current"] = current()
    s["update_available"] = newer(s.get("latest", ""), s["current"])
    return s
