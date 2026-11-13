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
import re
import time
from pathlib import Path

import httpx

from .config import get_settings
import uuid

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


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def log_tail(max_lines: int = 200, window: int = 256 * 1024) -> list[str]:
    """The agent's log for its latest run (from the last '=== ' line, which the agent writes at the start of
    each run), at most max_lines of it. Reads only the end of the file; [] when there's no log."""
    try:
        with open(folder() / "agent.log", "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - window))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    i = text.rfind("\n=== ")
    if i >= 0:
        text = text[i + 1:]
    text = _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    return text.splitlines()[-max_lines:]


def previous() -> dict | None:
    return _read("previous.json")


def busy() -> bool:
    return status().get("state") == "running" or status()["pending"]


def write_request(action: str, target: str, actor: str, **extra) -> dict:
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
