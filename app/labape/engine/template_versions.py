"""Dated template versions: monthly refresh and retention (Design_Base-Images.md §8).

A template named `<line>-YYYY.MM` (optionally `.N`, e.g. a second refresh in
one month) is one version of the line `<line>`: `win2022-desktop-2026.10.1`
belongs to `win2022-desktop`. Names without a date belong to no line, so
nothing here ever touches them.

- Refresh (question 16): once a month, on the day and time in Settings →
  Templates, the worker queues a `template.refresh` of each line's newest
  version into `<line>-<this month>`, unless that version already exists.
- Retention (question 15): each line keeps its newest `keep_versions`
  versions. An older one is deleted (a `template.delete` job) once nothing
  references it: no environment that isn't destroyed uses it, and no
  queued or running template job reads it. The delete job checks the host
  itself too, and refuses while any VM's disk has the image in its backing
  chain (a clone's overlay reads the template for its whole life).
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import Environment, Job, KvmHost, Setting, Template, utcnow
from . import hostio

log = logging.getLogger("labape.templates")

VERSION = re.compile(r"^(?P<line>.+)-(?P<y>\d{4})\.(?P<m>\d{2})(?:\.(?P<n>\d+))?$")
ACTIVE = ("queued", "running")
STATE_KEY = "status.template_refresh"     # {"month": "YYYY-MM"}: the month already queued


def parse(name: str) -> tuple[str, tuple[int, int, int]] | None:
    """`win2022-desktop-2026.10.1` -> ("win2022-desktop", (2026, 10, 1))."""
    m = VERSION.match(name)
    if not m:
        return None
    return m["line"], (int(m["y"]), int(m["m"]), int(m["n"] or 0))


def lines(db: Session, host_id: int) -> dict[str, list[Template]]:
    """A host's present templates by line, newest version first."""
    out: dict[str, list[tuple[tuple[int, int, int], Template]]] = {}
    for t in db.scalars(select(Template).where(Template.kvm_host_id == host_id, Template.present.is_(True))):
        p = parse(t.name)
        if p:
            out.setdefault(p[0], []).append((p[1], t))
    return {line: [t for _, t in sorted(vs, key=lambda x: x[0], reverse=True)] for line, vs in out.items()}


def _template_jobs(db: Session, host_id: int) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.kvm_host_id == host_id, Job.type.startswith("template."),
                                             Job.state.in_(ACTIVE))))


def users(db: Session, host_id: int, name: str) -> list[str]:
    """What still references a template: environments (not destroyed) and active template jobs."""
    out = []
    for env in db.scalars(select(Environment).where(Environment.kvm_host_id == host_id,
                                                    Environment.status != "destroyed")):
        if any(g.get("image_source") == "packer_template" and g.get("template") == name
               for g in (env.spec or {}).get("host_groups", [])):
            out.append(f"environment {env.name}")
    for j in _template_jobs(db, host_id):
        p = j.params or {}
        if name in (p.get("template"), p.get("name"), p.get("new_name")):
            out.append(f"job {j.id} ({j.type})")
    return out


def _queue(db: Session, host_id: int, job_type: str, params: dict) -> Job:
    job = Job(type=job_type, params=params, kvm_host_id=host_id, requested_by="system")
    db.add(job)
    db.flush()
    return job


def schedule_refresh(db: Session, now: dt.datetime | None = None) -> list[int]:
    """Queue this month's refreshes once the configured day and time (UTC) have passed."""
    cfg = appsettings.get(db, "templates")
    if not cfg.refresh_enabled:
        return []
    now = now or utcnow()
    hh, mm = (int(x) for x in cfg.refresh_time.split(":"))
    if now < now.replace(day=cfg.refresh_day, hour=hh, minute=mm, second=0, microsecond=0):
        return []
    month = f"{now.year:04d}-{now.month:02d}"
    state = db.get(Setting, STATE_KEY)
    if state is not None and (state.value or {}).get("month") == month:
        return []
    queued = []
    for host in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))):
        busy = {(j.params or {}).get("new_name") for j in _template_jobs(db, host.id)}
        for line, versions in lines(db, host.id).items():
            newest = versions[0]
            _, (y, m, _n) = parse(newest.name)
            new_name = f"{line}-{now.year:04d}.{now.month:02d}"
            if (y, m) >= (now.year, now.month) or new_name in busy or not newest.os_key:
                continue
            job = _queue(db, host.id, "template.refresh", {
                "template": newest.name, "new_name": new_name, "os_key": newest.os_key, "network": None,
                "addressing": "dhcp", "scheduled": True})
            queued.append(job.id)
    if state is None:
        db.add(Setting(key=STATE_KEY, value={"month": month}))
    else:
        state.value = {"month": month}
    db.commit()
    return queued


def schedule_retention(db: Session) -> list[int]:
    """Queue deletes for versions beyond each line's newest `keep_versions` (0 keeps them all)."""
    keep = appsettings.get(db, "templates").keep_versions
    if keep == 0:
        return []
    queued = []
    for host in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True))):
        for versions in lines(db, host.id).values():
            for t in versions[keep:]:
                if users(db, host.id, t.name):
                    continue      # referenced (or already being deleted): try again later
                queued.append(_queue(db, host.id, "template.delete", {"name": t.name, "reason": "retention"}).id)
    db.commit()
    return queued


# --- the delete job ---------------------------------------------------------

DELETE = r'''
import json, os, subprocess
lib, name, uri = ARGS["lib"], ARGS["name"], ARGS["uri"]
image = os.path.join(lib, name + ".qcow2")
if not os.path.exists(image):
    print(json.dumps({"missing": True})); raise SystemExit
target = os.path.realpath(image)
def run(*argv):
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=120)
r = run("virsh", "-c", uri, "list", "--all", "--name")
if r.returncode != 0:
    print(json.dumps({"error": "virsh list failed: " + (r.stderr or r.stdout).strip()[-300:]})); raise SystemExit
in_use, unreadable = [], []
for dom in [d for d in r.stdout.split() if d]:
    blk = run("virsh", "-c", uri, "domblklist", dom, "--details")
    if blk.returncode != 0:
        unreadable.append(dom)
        continue
    for line in blk.stdout.splitlines():
        f = line.split(None, 3)
        # Disks only: a CD-ROM is an ISO or seed image, never built on a template.
        if len(f) < 4 or f[0] != "file" or f[1] != "disk" or f[3] in ("-", ""):
            continue
        # Fail closed: a disk whose chain can't be read might be built on the template.
        info = run("qemu-img", "info", "-U", "--backing-chain", "--output=json", f[3])
        try:
            chain = json.loads(info.stdout) if info.returncode == 0 else None
        except ValueError:
            chain = None
        if chain is None:
            unreadable.append(f"{dom} ({f[3]})")
            continue
        for layer in (chain if isinstance(chain, list) else [chain]):
            for key in ("filename", "full-backing-filename", "backing-filename"):
                p = layer.get(key)
                if p and os.path.realpath(p if os.path.isabs(p) else os.path.join(os.path.dirname(f[3]), p)) == target:
                    in_use.append(dom)
if in_use:
    print(json.dumps({"in_use": sorted(set(in_use))})); raise SystemExit
if unreadable:
    print(json.dumps({"error": "couldn't read the disks of " + ", ".join(unreadable)
                      + ", so can't tell whether they use the template; not deleted"})); raise SystemExit
for path in (image, os.path.join(lib, name + ".json")):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
run("virsh", "-c", uri, "pool-refresh", "labape-templates")
print(json.dumps({"deleted": True}))
'''


def run_delete(db: Session, job: Job) -> int:
    """Delete one template from its host's library, if nothing uses it."""
    from .runner import _append   # runner imports this module's callers; avoid a cycle at import time
    name = (job.params or {}).get("name", "")
    host = db.get(KvmHost, job.kvm_host_id) if job.kvm_host_id else None
    if host is None or not parse(name) and not (job.params or {}).get("manual"):
        _append(job, f"labape: refusing to delete {name!r}: no host, or not a dated template version\n")
        return 1
    others = [u for u in users(db, host.id, name) if u != f"job {job.id} ({job.type})"]
    if others:
        _append(job, f"labape: {name} is still in use ({', '.join(others)}); not deleted\n")
        return 1
    uri = "qemu:///system" if hostio.is_remote(host.libvirt_uri) else host.libvirt_uri
    result = hostio.Host(host.libvirt_uri).python(DELETE, {"lib": host.template_storage_path, "name": name,
                                                           "uri": uri}, timeout=900)
    if result.get("in_use"):
        _append(job, f"labape: {name} is the backing file of VM(s) {', '.join(result['in_use'])}; not deleted\n")
        return 1
    if result.get("error"):
        _append(job, f"labape: {result['error']}\n")
        return 1
    t = db.scalar(select(Template).where(Template.kvm_host_id == host.id, Template.name == name))
    if t is not None:
        t.present = False
        db.commit()
    _append(job, f"labape: {name} {'was already gone' if result.get('missing') else 'deleted'} on {host.name}\n")
    return 0
