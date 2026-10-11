"""Running commands, as root or as the service user, honoring --dry-run."""
from __future__ import annotations

import os
import pwd
import shlex
import shutil
import subprocess
from pathlib import Path


class StepError(RuntimeError):
    pass


class Runner:
    def __init__(self, dry_run: bool, service_user: str | None = None):
        self.dry_run = dry_run
        self.service_user = service_user

    def home(self, user: str | None = None) -> Path:
        return Path(pwd.getpwnam(user or self.service_user).pw_dir)

    def run(self, cmd: list[str] | str, *, as_user: bool = False, check: bool = True, cwd: str | Path | None = None,
            input: str | None = None, capture: bool = False, env: dict | None = None,
            mutating: bool = True) -> subprocess.CompletedProcess:
        """Run a command. as_user runs it as the service user with their HOME.
        mutating=False marks read-only checks, which also run under --dry-run."""
        shell = isinstance(cmd, str)
        if as_user and self.service_user and self.service_user != pwd.getpwuid(os.geteuid()).pw_name:
            home = str(self.home())
            prefix = ["runuser", "-u", self.service_user, "--", "env", f"HOME={home}",
                      f"PATH=/usr/local/bin:/usr/bin:/bin:{home}/.local/bin"]
            cmd = prefix + (["bash", "-c", cmd] if shell else cmd)
            shell = False
        shown = cmd if isinstance(cmd, str) else shlex.join(cmd)
        if self.dry_run and mutating:
            print(f"[dry-run] {shown}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        print(f"$ {shown}", flush=True)
        full_env = None
        if env:
            full_env = {**os.environ, **env}
        res = subprocess.run(cmd, shell=shell, cwd=cwd, input=input, text=True, env=full_env,
                             capture_output=capture)
        if check and res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip()[-800:] if capture else ""
            raise StepError(f"command failed ({res.returncode}): {shown}" + (f"\n{detail}" if detail else ""))
        return res

    def out(self, cmd: list[str] | str, **kw) -> str:
        """Read-only command; returns stdout ('' on failure, or if the program doesn't exist)."""
        try:
            res = self.run(cmd, capture=True, check=False, mutating=False, **kw)
        except FileNotFoundError:
            return ""
        return (res.stdout or "").strip() if res.returncode == 0 else ""

    def write(self, path: Path, content: str, mode: int = 0o644, owner: str | None = None) -> bool:
        """Write a file if its content differs; returns whether it changed."""
        path = Path(path)
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False
        if self.dry_run:
            print(f"[dry-run] write {path} ({len(content)} bytes, mode {oct(mode)})")
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)
        if owner:
            shutil.chown(path, owner, owner)
        print(f"wrote {path}")
        return True

    def which(self, name: str) -> str | None:
        return shutil.which(name)
