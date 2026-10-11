"""Template library sync (design §21).

Each template is <template_storage_path>/<name>.qcow2 with a metadata
file <name>.json beside it:

    {"os_key": "windows_server_2022", "edition": "core", "min_cpu": 2,
     "min_memory_mb": 2048, "description": "...", "notes": "..."}

build-template.sh and promote-to-template.sh write it. For templates made
before that, the OS and edition are inferred from the name (question 61)
and the file is written once. The disk floor always comes from the
image's own virtual size. Edits made in the web interface (description,
notes, minimums) are written back to the file, so the CLI and other
instances see them too.
"""
from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KvmHost, Template

log = logging.getLogger("labape.templates")

# Name prefix -> os key (the names build-template.sh and earlier work used).
NAME_PATTERNS = [
    (r"^rocky9", "rocky9"),
    (r"^ubuntu2404|^ubuntu-?24", "ubuntu_lts"),
    (r"^ubuntu2604|^ubuntu-?26", "ubuntu_26"),
    (r"^debian13|^debian", "debian_latest"),
    (r"^win2019", "windows_server_2019"),
    (r"^win2022", "windows_server_2022"),
    (r"^win2025", "windows_server_2025"),
    (r"^win11", "windows_11"),
    (r"^win10", "windows_10"),
]

# Sensible minimums per OS family when the metadata doesn't say.
MINIMUMS = {
    "windows_11": (2, 4096),
    "windows_10": (2, 2048),
    "windows_server": (2, 2048),
    "linux": (1, 1024),
}

META_KEYS = ("os_key", "edition", "min_cpu", "min_memory_mb", "description", "notes")


def infer(name: str) -> dict:
    os_key = next((key for pat, key in NAME_PATTERNS if re.search(pat, name)), "")
    edition = ""
    if os_key.startswith("windows_server"):
        edition = "core" if "-core" in name else "desktop" if "-desktop" in name else ""
    family = os_key if os_key in ("windows_11", "windows_10") else \
        "windows_server" if os_key.startswith("windows_server") else "linux"
    cpu, mem = MINIMUMS[family]
    return {"os_key": os_key, "edition": edition, "min_cpu": cpu, "min_memory_mb": mem,
            "description": "", "notes": ""}


def disk_gb(path: Path) -> int:
    try:
        out = subprocess.run(["qemu-img", "info", "-U", "--output=json", str(path)],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        return math.ceil(json.loads(out)["virtual-size"] / 2**30)
    except (subprocess.SubprocessError, ValueError, KeyError, OSError):
        return 0


def write_meta(path: Path, meta: dict) -> None:
    try:
        path.write_text(json.dumps({k: meta.get(k) for k in META_KEYS}, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o644)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)


def sync_host(db: Session, host: KvmHost) -> int:
    lib = Path(host.template_storage_path)
    if not host.enabled or not host.libvirt_uri.startswith("qemu:///") or not lib.is_dir():
        return 0
    existing = {t.name: t for t in db.scalars(select(Template).where(Template.kvm_host_id == host.id))}
    seen = set()
    for image in sorted(lib.glob("*.qcow2")):
        name = image.stem
        seen.add(name)
        meta_file = image.with_suffix(".json")
        t = existing.get(name)
        if t is not None and t.dirty:
            write_meta(meta_file, {k: getattr(t, k) for k in META_KEYS})
            t.dirty = False
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            source = "metadata"
        except (OSError, ValueError):
            meta = infer(name)
            source = "inferred"
            write_meta(meta_file, meta)
        if t is None:
            t = Template(kvm_host_id=host.id, name=name)
            db.add(t)
        for k in META_KEYS:
            if k in meta and meta[k] is not None and getattr(t, k) != meta[k]:
                setattr(t, k, meta[k])
        size = disk_gb(image)
        if size and t.disk_gb != size:
            t.disk_gb = size
        t.source = source
        t.present = True
    for name, t in existing.items():
        if name not in seen and t.present:
            t.present = False
    db.commit()
    return len(seen)


def sync_all(db: Session) -> None:
    for host in db.scalars(select(KvmHost)):
        try:
            sync_host(db, host)
        except Exception:  # noqa: BLE001 - one host's trouble mustn't stop the others
            db.rollback()
            log.exception("template sync failed for host %s", host.name)
