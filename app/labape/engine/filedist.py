"""Files from the primary KVM host to the others (design §29.4, questions 131-135).

One KVM host is the primary: it holds every file LABaPe uses (ISOs, installers, other files,
and the template library's images). A job on another host gets the files it needs copied
there, streamed host to host through the worker (`ssh primary cat | ssh host 'cat >'`), into
that host's cache (`<vm storage>/.labape-files/...`) or, for templates, its template library.
A file already there with the same size (and SHA-256, when LABaPe knows it) isn't copied
again. A host whose admin mounted the primary's file locations at the same paths (a share)
reads them there instead. While the primary can't be reached, a job waits up to
Settings → File distribution's limit before it fails, unless the host already has the file.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import KvmHost, StoredFile, Template
from . import hostio

CACHE = ".labape-files"              # under a host's VM storage path
POLL_SECONDS = 30


class FileDistError(RuntimeError):
    pass


class Unreachable(FileDistError):
    pass


class Missing(FileDistError):
    """The file isn't on the primary."""


def primary(db: Session) -> KvmHost | None:
    return db.scalar(select(KvmHost).where(KvmHost.is_primary.is_(True)))


def ensure_primary(db: Session) -> KvmHost | None:
    """There's always a primary once there's a host (design §29.4): the host the stack runs on
    (qemu:///system), else the first one registered. A new install's database is created at
    the latest schema (no migration picks one) and `labape bootstrap` registers hosts directly,
    so the worker checks this every minute."""
    p = primary(db)
    if p is not None:
        return p
    hosts = list(db.scalars(select(KvmHost).order_by(KvmHost.id)))
    if not hosts:
        return None
    p = next((h for h in hosts if not hostio.is_remote(h.libvirt_uri)), hosts[0])
    p.is_primary, p.files_from_share = True, False
    db.commit()
    return p


def endpoint(h: KvmHost) -> hostio.Host | None:
    """None: the host the worker runs on (its paths are local)."""
    return hostio.Host(h.libvirt_uri) if hostio.is_remote(h.libvirt_uri) else None


def cache_dir(h: KvmHost, kind: str) -> str:
    return f"{h.vm_storage_path.rstrip('/')}/{CACHE}/{kind}"


def _run(ep: hostio.Host, command: str, timeout: float = 120) -> subprocess.CompletedProcess:
    r = ep.run(command, timeout=timeout)
    if r.returncode == 255:                                  # ssh itself failed: the host is down or unreachable
        raise Unreachable(f"{ep.target}: {r.stderr.decode(errors='replace').strip()[-200:]}")
    return r


def size_of(ep: hostio.Host | None, path: str) -> int:
    """The file's size, -1 when it isn't there."""
    if ep is None:
        try:
            return os.stat(path).st_size
        except OSError:
            return -1
    out = _run(ep, f"stat -L -c %s {shlex.quote(path)} 2>/dev/null || echo -1").stdout.decode().strip()
    return int(out or -1)


def sha256_of(ep: hostio.Host | None, path: str) -> str:
    if ep is None:
        from ..files import sha256_of as local_sha
        return local_sha(Path(path))
    out = _run(ep, f"sha256sum {shlex.quote(path)}", timeout=3600).stdout.decode().split()
    return out[0] if out else ""


def copy(src: hostio.Host | None, src_path: str, dst: hostio.Host | None, dst_path: str, log) -> None:
    """Stream one file; it appears under its name only once complete (.part, then mv)."""
    part = f"{dst_path}.part"
    reader = None
    try:
        if src is None:
            fh = open(src_path, "rb")
            stdin = fh
        else:
            reader = subprocess.Popen([*src.ssh_base(), f"cat {shlex.quote(src_path)}"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=hostio.tool_env())
            stdin = reader.stdout
        if dst is None:
            Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as out:
                while block := stdin.read(8 * 2**20):
                    out.write(block)
            os.replace(part, dst_path)
            rc, err = 0, b""
        else:
            d = os.path.dirname(dst_path)
            r = subprocess.run([*dst.ssh_base(), f"mkdir -p {shlex.quote(d)} && cat > {shlex.quote(part)} && "
                                                 f"mv {shlex.quote(part)} {shlex.quote(dst_path)}"],
                               stdin=stdin, capture_output=True, timeout=12 * 3600, env=hostio.tool_env(), check=False)
            rc, err = r.returncode, r.stderr
        if reader is not None:
            reader.stdout.close()
            if reader.wait(timeout=60) != 0:
                raise FileDistError(f"reading {src_path} on the primary failed: "
                                    f"{reader.stderr.read().decode(errors='replace').strip()[-200:]}")
        if rc != 0:
            raise FileDistError(f"writing {dst_path} failed: {err.decode(errors='replace').strip()[-200:]}")
    finally:
        if src is None and "fh" in locals():
            fh.close()
        if reader is not None and reader.poll() is None:
            reader.kill()


def ensure(db: Session, target: KvmHost, src_path: str, dst_path: str, log, size: int = -1, sha256: str = "",
           sleep=time.sleep, now=time.monotonic, use_share: bool = True) -> str:
    """Make `src_path` (on the primary) available on `target`; returns the path the target uses.
    `size`/`sha256`: what LABaPe knows about the file (it then needn't reach the primary to
    see that the target's copy is current)."""
    p = primary(db)
    if p is None:
        raise FileDistError("no KVM host is the primary file host (Admin → KVM hosts)")
    if target.id == p.id:
        return src_path
    tgt = endpoint(target)
    if target.files_from_share and use_share:
        if size_of(tgt, src_path) < 0:
            raise FileDistError(f"{target.name} reads files from a share, but {src_path} isn't there")
        return src_path
    have = size_of(tgt, dst_path)
    if have >= 0 and have == size and (not sha256 or sha256_of(tgt, dst_path) == sha256):
        return dst_path
    src = endpoint(p)
    wait = appsettings.get(db, "file_distribution").wait_minutes * 60
    deadline, name = now() + wait, os.path.basename(src_path)
    while True:
        try:
            want = size_of(src, src_path)
            if want < 0:
                raise Missing(f"{src_path} isn't on the primary ({p.name})")
            if have == want and (not sha256 or sha256_of(tgt, dst_path) == sha256):
                return dst_path
            log.write(f"labape: copying {name} ({want / 2**30:.1f} GB) from {p.name} to {target.name}\n")
            copy(src, src_path, tgt, dst_path, log)
            if size_of(tgt, dst_path) != want:
                raise FileDistError(f"{name}: the copy on {target.name} has the wrong size")
            return dst_path
        except Unreachable as exc:
            if now() >= deadline:
                raise FileDistError(f"the primary ({p.name}) couldn't be reached for {name} "
                                    f"within {wait // 60} minutes: {exc}") from exc
            log.write(f"labape: waiting for the primary ({p.name}) for {name}: {exc} "
                      f"({int((deadline - now()) // 60)} min left)\n")
            sleep(POLL_SECONDS)


def known(db: Session, path: str) -> tuple[int, str]:
    """(size, sha256) LABaPe recorded for a file on the primary, or (-1, '')."""
    f = db.scalar(select(StoredFile).where(StoredFile.path == path))
    return (f.size or -1, f.sha256 or "") if f is not None else (-1, "")


def to_primary(db: Session, source: KvmHost, name: str, log) -> bool:
    """A template built on `source` copied into the primary's library (its template sync then
    registers it). False when `source` is the primary or the primary already has it."""
    p = primary(db)
    if p is None or source is None or p.id == source.id:
        return False
    src, dst = endpoint(source), endpoint(p)
    src_dir, dst_dir = source.template_storage_path.rstrip("/"), p.template_storage_path.rstrip("/")
    image = f"{src_dir}/{name}.qcow2"
    want = size_of(src, image)
    if want < 0:
        raise Missing(f"{image} isn't on {source.name}")
    if size_of(dst, f"{dst_dir}/{name}.qcow2") == want:
        return False
    for suffix in (".json", ".qcow2"):
        path = f"{src_dir}/{name}{suffix}"
        if size_of(src, path) < 0:
            continue
        log.write(f"labape: copying {name}{suffix} to the primary file host, {p.name}\n")
        copy(src, path, dst, f"{dst_dir}/{name}{suffix}", log)
    return True


def ensure_template(db: Session, target: KvmHost, name: str, log) -> bool:
    """A template from the primary's library on `target` (image and metadata, then registered).
    False when the target already has it."""
    have = db.scalar(select(Template).where(Template.kvm_host_id == target.id, Template.name == name,
                                            Template.present.is_(True)))
    if have is not None:
        return False
    p = primary(db)
    src_t = db.scalar(select(Template).where(Template.kvm_host_id == p.id, Template.name == name)) if p else None
    if src_t is None:
        raise FileDistError(f"template {name} is neither on {target.name} nor on the primary")
    src_dir, dst_dir = p.template_storage_path.rstrip("/"), target.template_storage_path.rstrip("/")
    for suffix in (".json", ".qcow2"):           # the image last: its arrival is what the sync looks for
        try:
            # Always into the host's own library (deploys clone from there), share or not.
            ensure(db, target, f"{src_dir}/{name}{suffix}", f"{dst_dir}/{name}{suffix}", log, use_share=False)
        except Missing:
            if suffix == ".json":                # metadata is optional (inferred from the name otherwise)
                continue
            raise
    cols = {c.name for c in Template.__table__.columns} - {"id", "kvm_host_id"}
    t = Template(kvm_host_id=target.id, **{c: getattr(src_t, c) for c in cols})
    t.present, t.dirty = True, False
    db.add(t)
    db.commit()
    log.write(f"labape: template {name} is now on {target.name} too\n")
    return True


# --- templates ahead of need (design §43) ---------------------------------------------------------

SYNC_KEY = "files.templates_synced_at"


def run_sync_templates(db: Session, job) -> int:
    """Job `files.sync_templates`: every template in the primary's library onto every enabled KVM host
    that doesn't have it. One host or template failing doesn't stop the others."""
    p = primary(db)
    with open(job.log_path, "a", encoding="utf-8") as log:
        if p is None:
            log.write("labape: no primary file host\n")
            return 1
        names = sorted(set(db.scalars(select(Template.name).where(Template.kvm_host_id == p.id,
                                                                   Template.present.is_(True)))))
        hosts = [h for h in db.scalars(select(KvmHost).where(KvmHost.enabled.is_(True)))
                 if h.id != p.id and h.role != "stack"]
        log.write(f"labape: {len(names)} template(s) from {p.name} to {len(hosts)} host(s)\n")
        failed = 0
        for h in hosts:
            for name in names:
                try:
                    ensure_template(db, h, name, log)
                except FileDistError as exc:
                    failed += 1
                    log.write(f"labape: {name} to {h.name} failed: {exc}\n")
        from ..models import Setting, utcnow
        row = db.get(Setting, SYNC_KEY)
        value = {"at": utcnow().isoformat(), "failed": failed}
        if row is None:
            db.add(Setting(key=SYNC_KEY, value=value))
        else:
            row.value = value
        db.commit()
        log.write(f"labape: done; {failed} copy(ies) failed\n" if failed else "labape: done\n")
        return 1 if failed else 0


def schedule_template_sync(db: Session, now=None) -> int | None:
    """Daily, when Settings → File distribution asks for it and no sync is queued or running."""
    import datetime as dt

    from .. import appsettings
    from ..models import Job, Setting, utcnow
    if not appsettings.get(db, "file_distribution").copy_templates_to_all:
        return None
    now = now or utcnow()
    row = db.get(Setting, SYNC_KEY)
    if row is not None and row.value and row.value.get("at"):
        last = dt.datetime.fromisoformat(row.value["at"])
        if now - (last if last.tzinfo else last.replace(tzinfo=dt.timezone.utc)) < dt.timedelta(hours=24):
            return None
    if db.scalar(select(Job).where(Job.type == "files.sync_templates", Job.state.in_(("queued", "running")))):
        return None
    job = Job(type="files.sync_templates", params={}, requested_by="system")
    db.add(job)
    # Marked now, so a slow or failing sync isn't queued again every minute.
    value = {"at": now.isoformat(), "queued": True}
    if row is None:
        db.add(Setting(key=SYNC_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return job.id

