"""Where the Files page's files are (design §29.5, questions 131 and 134).

They live on the primary KVM host. When the stack runs on that host (a full install), its
locations are local paths, as before. When it runs elsewhere (a web install, e.g. a small
machine driving a larger KVM host), every file operation runs on the primary over SSH as its
service user, the same login jobs use: uploads append each chunk to a `.part` file there,
downloads from a URL stream there, and scans and checksums run there. Nothing is staged in
full on the stack host.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session


class StoreError(OSError):
    pass


class LocalStore:
    remote = False
    where = "this host"

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def makedirs(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def touch(self, path: str) -> None:
        Path(path).touch()

    def size(self, path: str) -> int:
        try:
            return os.stat(path).st_size
        except OSError:
            return -1

    def open_write(self, path: str, append: bool = False):
        return open(path, "ab" if append else "wb")

    def replace(self, src: str, dst: str) -> None:
        os.replace(src, dst)
        os.chmod(dst, 0o644)

    def remove(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)

    def stat(self, path: str) -> tuple[int, dt.datetime]:
        st = os.stat(path)
        return st.st_size, dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)

    def hashes(self, path: str, algos: set[str]) -> dict[str, str]:
        hs = {a: hashlib.new(a) for a in algos}
        with open(path, "rb") as fh:
            while block := fh.read(8 * 2**20):
                for h in hs.values():
                    h.update(block)
        return {a: h.hexdigest() for a, h in hs.items()}

    def files(self, root: str) -> list[tuple[str, int, dt.datetime]]:
        """The regular, non-hidden files directly in `root`: (path, size, mtime)."""
        if not os.path.isdir(root):
            return []
        out = []
        for p in sorted(Path(root).iterdir()):
            if p.is_file() and not p.name.startswith("."):
                st = p.stat()
                out.append((str(p), st.st_size, dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)))
        return out


class _RemoteWriter:
    """A file on the primary written through `ssh ... 'cat > file'` (or >>)."""

    def __init__(self, host, path: str, append: bool):
        from .engine import hostio
        self.proc = subprocess.Popen([*host.ssh_base(), f"cat {'>>' if append else '>'} {shlex.quote(path)}"],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     env=hostio.tool_env())

    def write(self, data: bytes) -> int:
        self.proc.stdin.write(data)
        return len(data)

    def close(self) -> None:
        self.proc.stdin.close()
        if self.proc.wait(timeout=600) != 0:
            raise StoreError(f"writing on the primary failed: {self.proc.stderr.read().decode(errors='replace').strip()[-200:]}")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is None:
            self.close()
        else:
            self.proc.kill()


FILES_SCRIPT = r'''
import json, os, sys
root = ARGS["root"]
out = []
if os.path.isdir(root):
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not name.startswith(".") and os.path.isfile(p):
            st = os.stat(p)
            out.append([p, st.st_size, st.st_mtime])
print(json.dumps(out))
'''
HASH_SCRIPT = r'''
import hashlib, json
hs = {a: hashlib.new(a) for a in ARGS["algos"]}
with open(ARGS["path"], "rb") as fh:
    while True:
        block = fh.read(8 * 2**20)
        if not block:
            break
        for h in hs.values():
            h.update(block)
print(json.dumps({a: h.hexdigest() for a, h in hs.items()}))
'''


class RemoteStore:
    remote = True

    def __init__(self, primary):
        from .engine import hostio
        self.host = hostio.Host(primary.libvirt_uri)
        self.where = primary.name

    def _run(self, command: str, timeout: float = 120) -> subprocess.CompletedProcess:
        r = self.host.run(command, timeout=timeout)
        if r.returncode == 255:
            raise StoreError(f"the primary ({self.where}) can't be reached: "
                             f"{r.stderr.decode(errors='replace').strip()[-200:]}")
        return r

    def _ok(self, command: str, what: str, timeout: float = 120) -> None:
        r = self._run(command, timeout)
        if r.returncode != 0:
            raise StoreError(f"{what} on {self.where} failed: {r.stderr.decode(errors='replace').strip()[-200:]}")

    def exists(self, path: str) -> bool:
        return self._run(f"test -e {shlex.quote(path)}").returncode == 0

    def makedirs(self, path: str) -> None:
        self._ok(f"mkdir -p {shlex.quote(path)}", f"creating {path}")

    def touch(self, path: str) -> None:
        self._ok(f": > {shlex.quote(path)}", f"creating {path}")

    def size(self, path: str) -> int:
        out = self._run(f"stat -c %s {shlex.quote(path)} 2>/dev/null || echo -1").stdout.decode().strip()
        return int(out or -1)

    def open_write(self, path: str, append: bool = False):
        return _RemoteWriter(self.host, path, append)

    def replace(self, src: str, dst: str) -> None:
        self._ok(f"mv {shlex.quote(src)} {shlex.quote(dst)} && chmod 0644 {shlex.quote(dst)}", f"moving {src}")

    def remove(self, path: str) -> None:
        self._ok(f"rm -f {shlex.quote(path)}", f"deleting {path}")

    def stat(self, path: str) -> tuple[int, dt.datetime]:
        out = self._run(f"stat -c '%s %Y' {shlex.quote(path)}").stdout.decode().split()
        if len(out) != 2:
            raise StoreError(f"{path} isn't on {self.where}")
        return int(out[0]), dt.datetime.fromtimestamp(int(out[1]), dt.timezone.utc)

    def hashes(self, path: str, algos: set[str]) -> dict[str, str]:
        try:
            return self.host.python(HASH_SCRIPT, {"path": path, "algos": sorted(algos)}, timeout=6 * 3600)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreError(f"checksumming {path} on {self.where} failed: {exc}") from exc

    def files(self, root: str) -> list[tuple[str, int, dt.datetime]]:
        try:
            rows = self.host.python(FILES_SCRIPT, {"root": root}, timeout=120)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreError(f"listing {root} on {self.where} failed: {exc}") from exc
        return [(p, s, dt.datetime.fromtimestamp(m, dt.timezone.utc)) for p, s, m in rows]


def current(db: Session):
    """The primary's files: local paths when the stack runs on it (or there's no primary yet)."""
    from .engine import filedist
    p = filedist.primary(db)
    if p is None or filedist.endpoint(p) is None:
        return LocalStore()
    return RemoteStore(p)
