"""Containers, batch C4 (plan: Planning_Disk-Health-Notifications.md): a whole compose project, and a weekly
"is there a newer image?" check. The check compares digests only (the host asks the registry; nothing is pulled)
and is off unless the host's switch is on, because it makes the host reach out to registries.
"""
from __future__ import annotations

import datetime as dt
import shlex
import subprocess

from sqlalchemy.orm import Session

from ..models import HostContainers, KvmHost, utcnow
from . import hostadmin
from .hostcontainers import IMAGE_ID, IMAGE_REF, SAFE_PATH, SAFE_WORD, actionable

UPDATE_EVERY = dt.timedelta(days=7)

UPDATE_SCRIPT = r'''
import json, subprocess
rt = ARGS["runtime"]
out = {}
def sh(*a, timeout=60):
    r = subprocess.run(list(a), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()
for ref in ARGS["refs"]:
    rc, local, _ = sh(rt, "image", "inspect", "--format", "{{json .RepoDigests}}", ref)
    try:
        local = json.loads(local) if rc == 0 else []
    except ValueError:
        local = []
    remote, err = "", ""
    if rt == "docker":
        rc, o, e = sh("docker", "buildx", "imagetools", "inspect", ref, timeout=60)
        for ln in o.splitlines():
            if ln.startswith("Digest:"):
                remote = ln.split()[1]
        err = "" if remote else (e or o or "no answer")[-200:]
    else:
        rc, o, e = sh("skopeo", "inspect", "--format", "{{.Digest}}", "docker://" + ref, timeout=60)
        remote = o if rc == 0 else ""
        err = "" if remote else (e or "skopeo is not installed")[-200:]
    out[ref] = {"local": [d.split("@", 1)[-1] for d in (local or [])], "remote": remote, "error": err}
print(json.dumps(out))
'''


def update_refs(row: HostContainers | None) -> list[str]:
    """Registry tags worth asking about: what running containers use, not digests, not ids."""
    cs = ((row.data if row else {}) or {}).get("containers") or []
    return sorted({c["image"] for c in cs if c.get("state") == "running" and IMAGE_REF.match(c.get("image") or "")
                   and "@" not in c["image"] and not c["image"].startswith("sha256:") and not IMAGE_ID.match(c["image"])})


def verdicts(raw: dict) -> dict:
    """ref -> newer | current | unknown, from the digests the host read."""
    return {ref: "unknown" if not v.get("remote") or not v.get("local") else "current" if v["remote"] in v["local"] else "newer"
            for ref, v in raw.items()}


def check(db: Session, h: KvmHost, now: dt.datetime | None = None) -> dict:
    """Ask the registries, from the host, whether the images in use have a newer digest. Pulls nothing."""
    now = now or utcnow()
    row = db.get(HostContainers, h.id)
    if row is None or not (row.data or {}).get("runtime"):
        raise hostadmin.AdminError("read this host's containers first")
    refs = update_refs(row)
    raw = hostadmin.for_host(h).python(UPDATE_SCRIPT, {"runtime": row.data["runtime"], "refs": refs}, timeout=60 * (len(refs) + 2)) if refs else {}
    data = dict(row.data)
    data["updates"] = {"at": now.isoformat(), "images": verdicts(raw), "errors": {r: v["error"] for r, v in raw.items() if v.get("error")}}
    row.data = data
    db.merge(row)
    db.commit()
    return data["updates"]


def due(db: Session, h: KvmHost, now: dt.datetime | None = None) -> None:
    """Weekly, for hosts with the switch on; a failure is tried again at the next collection."""
    if not h.container_updates:
        return
    now = now or utcnow()
    row = db.get(HostContainers, h.id)
    last = ((row.data if row else {}) or {}).get("updates") or {}
    if row is not None and (not last.get("at") or now - dt.datetime.fromisoformat(last["at"]).astimezone(dt.timezone.utc) >= UPDATE_EVERY):
        try:
            check(db, h, now)
        except (hostadmin.AdminError, subprocess.TimeoutExpired, ValueError):
            pass


def project_script(runtime: str, containers: list[dict]) -> str:
    """Pull and recreate every service of one compose project in one go."""
    if runtime != "docker":
        raise hostadmin.AdminError("updating a project needs docker compose")
    c = containers[0]
    project, folder = c.get("project", ""), c.get("compose_dir", "")
    files = [f for f in (c.get("compose_files") or "").split(",") if f]
    if not (SAFE_WORD.match(project) and SAFE_PATH.match(folder) and all(SAFE_PATH.match(f) for f in files)):
        raise hostadmin.AdminError("this project's compose labels can't be used safely")
    base = f"docker compose -p {shlex.quote(project)} " + "".join(f"-f {shlex.quote(f)} " for f in files)
    return f"set -e; cd {shlex.quote(folder)}; {base}pull; {base}up -d"


def run_project(ssh, h: KvmHost, row: HostContainers | None, project: str) -> str:
    """Pull and recreate a whole compose project; every container of it must be one the host's level lets the page change."""
    cs = [c for c in ((row.data if row else {}) or {}).get("containers") or [] if project and c.get("project") == project]
    if not cs:
        raise hostadmin.AdminError("no such compose project on this host (refresh the page)")
    for c in cs:
        ok, why = actionable(h, c)
        if not ok:
            raise hostadmin.AdminError(f"{c['name']} can't be changed from here: {why}")
    if any(c.get("quadlet") for c in cs) or not all(c.get("compose_dir") for c in cs):
        raise hostadmin.AdminError("this isn't a plain compose project")
    r = ssh.run(f"sudo -n bash -c {shlex.quote(project_script(row.data['runtime'], cs))}", timeout=1800)
    text = (r.stdout + r.stderr).decode(errors="replace").strip()
    if r.returncode != 0:
        raise hostadmin.AdminError(text[-400:] or f"exit {r.returncode}")
    return text[-400:]
