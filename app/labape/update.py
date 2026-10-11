"""The app's side of updates (design §23.2).

The host-side agent (deploy/update-agent/) does the work; the app and the
agent talk through a folder both can write (LABAPE_UPDATE_DIR, mounted
from container/update/):

- request.json   the app asks: {"action": "update"|"rollback", "target": tag, ...}
- status.json    the agent reports: state, step, message, from, to, times
- previous.json  the version on standby for a rollback (one step back only)
- agent.json     written when the agent is installed and on every run

Only signed tags are applied; the agent checks the signature itself.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import httpx

from .config import get_settings

_releases_cache: tuple[float, list[dict]] | None = None


def folder() -> Path:
    return get_settings().update_dir


def _read(name: str) -> dict | None:
    try:
        return json.loads((folder() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def agent_installed() -> bool:
    return (folder() / "agent.json").is_file()


def status() -> dict:
    s = _read("status.json") or {"state": "idle"}
    s["pending"] = (folder() / "request.json").is_file()
    return s


def previous() -> dict | None:
    return _read("previous.json")


def busy() -> bool:
    return status().get("state") == "running" or status()["pending"]


def write_request(action: str, target: str, actor: str, **extra) -> dict:
    import uuid
    req = {"id": uuid.uuid4().hex, "action": action, "target": target, "requested_by": actor,
           "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(), **extra}
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".request.json.tmp"
    tmp.write_text(json.dumps(req, indent=2), encoding="utf-8")
    os.replace(tmp, d / "request.json")        # the agent's path unit fires on this
    return req


def releases(repo: str, timeout: float = 15) -> list[dict]:
    """Published releases and pre-releases, newest first (cached ten minutes)."""
    global _releases_cache
    if _releases_cache and time.monotonic() - _releases_cache[0] < 600:
        return _releases_cache[1]
    r = httpx.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30}, timeout=timeout,
                  headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    out = [{"tag": x["tag_name"], "name": x.get("name") or x["tag_name"], "prerelease": bool(x.get("prerelease")),
            "url": x.get("html_url", ""), "published_at": x.get("published_at")}
           for x in r.json() if not x.get("draft")]
    _releases_cache = (time.monotonic(), out)
    return out


def manual_commands(target: str) -> list[str]:
    """What to run on the host when no agent is installed."""
    return ["cd ~/LABaPe-web   # the stack's checkout", "git fetch --tags origin",
            f"git verify-tag {target}", f"git checkout {target}",
            "cd container && export LABAPE_VERSION=$(git describe --tags --always)",
            "docker compose up -d --build"]
