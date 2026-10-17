"""Interactive text sessions for API console tokens (design §59.4, batch X3).

A session is one `ssh -tt` process (a shell on a VM, a VM's serial console through its KVM host, or a
KVM host's shell). Its output is read by a thread into a rolling buffer with absolute offsets, so a
caller reads "everything after offset N" by polling or by a long poll, and never loses its place unless
it falls more than the buffer behind (it is told how much was dropped). The registry lives in the API
process (the API runs as one process); sessions end when closed, when the process exits, at the token's
session length, or after the idle time from Settings → Remote access.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_INPUT = 65536                  # bytes in one input call


class SessionError(RuntimeError):
    pass


@dataclass
class TextSession:
    id: int                              # the remote_sessions row
    token_id: int
    target: str                          # "<env id>:<vm>" or "host:<id>"
    proc: subprocess.Popen
    tmp: Path
    buffer_cap: int
    deadline: float                      # monotonic: the token's session length
    idle_seconds: float
    on_output: Callable[[str], None] | None = None
    start: int = 0                       # absolute offset of the first byte still buffered
    data: bytearray = field(default_factory=bytearray)
    input_bytes: int = 0
    last_used: float = field(default_factory=time.monotonic)
    closed_reason: str = ""
    finalized: bool = False              # its row and audit event are written
    cond: threading.Condition = field(default_factory=threading.Condition)

    @property
    def end(self) -> int:
        return self.start + len(self.data)

    @property
    def open(self) -> bool:
        return not self.closed_reason

    def _feed(self, chunk: bytes) -> None:
        with self.cond:
            self.data += chunk
            if len(self.data) > self.buffer_cap:
                drop = len(self.data) - self.buffer_cap
                del self.data[:drop]
                self.start += drop
            self.cond.notify_all()
        if self.on_output:
            self.on_output(chunk.decode(errors="replace"))

    def _pump(self) -> None:
        try:
            while True:
                chunk = self.proc.stdout.read1(65536)
                if not chunk:
                    break
                self._feed(chunk)
        finally:
            code = self.proc.wait()
            with self.cond:
                self.closed_reason = self.closed_reason or f"the session ended (exit {code})"
                self.cond.notify_all()

    def write(self, text: str) -> int:
        raw = text.encode()
        if len(raw) > MAX_INPUT:
            raise SessionError(f"input is limited to {MAX_INPUT} bytes a call")
        if not self.open:
            raise SessionError(self.closed_reason)
        try:
            self.proc.stdin.write(raw)
            self.proc.stdin.flush()
        except OSError as exc:
            raise SessionError("the session has ended") from exc
        self.input_bytes += len(raw)
        self.last_used = time.monotonic()
        return len(raw)

    def read(self, after: int, wait: float = 0.0) -> dict:
        """Output from offset `after`; waits up to `wait` seconds for some when there is none yet."""
        self.last_used = time.monotonic()
        end_at = time.monotonic() + max(0.0, wait)
        with self.cond:
            while self.end <= after and self.open:
                left = end_at - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            lost = max(0, self.start - after)
            begin = max(after, self.start)
            chunk = bytes(self.data[begin - self.start:])
            return {"data": chunk.decode(errors="replace"), "next": self.end, "lost": lost,
                    "open": self.open, "reason": self.closed_reason}

    def close(self, reason: str = "closed") -> None:
        with self.cond:
            self.closed_reason = self.closed_reason or reason
            self.cond.notify_all()
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


_sessions: dict[int, TextSession] = {}
_lock = threading.Lock()


def open_session(session_id: int, token_id: int, target: str, argv: list[str], env: dict, tmp: Path, *,
                 buffer_cap: int, seconds: float, idle_seconds: float,
                 on_output: Callable[[str], None] | None = None) -> TextSession:
    with _lock:
        if any(s.token_id == token_id and s.target == target and s.open for s in _sessions.values()):
            shutil.rmtree(tmp, ignore_errors=True)
            raise SessionError("this token already has a session open on that target; close it first")
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    s = TextSession(session_id, token_id, target, proc, tmp, buffer_cap, time.monotonic() + seconds, idle_seconds,
                    on_output)
    with _lock:
        _sessions[session_id] = s
    threading.Thread(target=s._pump, daemon=True).start()
    return s


def get(session_id: int) -> TextSession | None:
    return _sessions.get(session_id)


def sweep(now: float | None = None) -> list[TextSession]:
    """Close sessions past their length or idle time; returns the closed ones not yet finalized."""
    now = time.monotonic() if now is None else now
    done = []
    for s in list(_sessions.values()):
        if s.open and now >= s.deadline:
            s.close("the token's session length was reached")
        elif s.open and now - s.last_used >= s.idle_seconds:
            s.close("idle")
        if not s.open and not s.finalized:
            s.finalized = True
            done.append(s)
    return done


_sweeper: threading.Thread | None = None


def ensure_sweeper(finalize: Callable[["TextSession"], None], every: float = 10.0) -> None:
    """Start (once) the thread that ends sessions past their limits and calls `finalize` for each ended one."""
    global _sweeper
    with _lock:
        if _sweeper is not None and _sweeper.is_alive():
            return

        def loop():
            while True:
                time.sleep(every)
                for s in sweep():
                    try:
                        finalize(s)
                    except Exception:  # noqa: BLE001 — keep sweeping
                        pass

        _sweeper = threading.Thread(target=loop, daemon=True, name="console-sweeper")
        _sweeper.start()


def forget(session_id: int) -> None:
    with _lock:
        _sessions.pop(session_id, None)


def make_tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="labape-console-"))
