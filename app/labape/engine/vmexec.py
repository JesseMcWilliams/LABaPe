"""Run one command on an environment's VM and return what it printed (design §59.2, batch X2).

Linux: `ssh` as the VM's access user (LABaPe's key) or as an environment account (its stored password,
given to ssh through SSH_ASKPASS, never on a command line). A VM on a remote KVM host is reached through
that host (`ssh -W`). Windows: WinRM with PowerShell (pywinrm), on the stack host's own networks only.
Output is capped (the process is stopped past the cap) and this call's own credentials are masked.
"""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Environment, KvmHost
from . import hostio

MASK = "***"


class ExecError(RuntimeError):
    """The command couldn't be run at all (no address, no stored password, a remote Windows VM...)."""


@dataclass
class Result:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False
    duration: float = 0.0
    masked: bool = field(default=False)

    def as_dict(self) -> dict:
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr,
                "truncated": self.truncated, "timed_out": self.timed_out, "duration": round(self.duration, 3)}


def mask(text: str, secrets: list[str]) -> str:
    for s in sorted({x for x in secrets if x and len(x) >= 4}, key=len, reverse=True):
        text = text.replace(s, MASK)
    return text


def _read(stream, cap: int, out: list, flag: dict, proc: subprocess.Popen) -> None:
    got = 0
    while True:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
        if not chunk:
            return
        room = cap - got
        if room > 0:
            out.append(chunk[:room])
        got += len(chunk)
        if got > cap:
            flag["truncated"] = True
            proc.kill()
            return


def run_process(argv: list[str], *, env: dict | None = None, timeout: float, cap: int,
                input_: bytes | None = None) -> Result:
    start = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out: list = []
    err: list = []
    flag = {"truncated": False}
    threads = [threading.Thread(target=_read, args=(proc.stdout, cap, out, flag, proc), daemon=True),
               threading.Thread(target=_read, args=(proc.stderr, cap, err, flag, proc), daemon=True)]
    for t in threads:
        t.start()
    try:
        proc.stdin.write(input_ or b"")
        proc.stdin.close()
    except OSError:
        pass
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    for t in threads:
        t.join(timeout=5)
    return Result(None if (timed_out or flag["truncated"]) else proc.returncode,
                  b"".join(out).decode(errors="replace"), b"".join(err).decode(errors="replace"),
                  flag["truncated"], timed_out, time.monotonic() - start)


def _ssh_argv(params: dict, command: str, tmp: Path, via: KvmHost | None) -> tuple[list[str], dict, list[str]]:
    """ssh arguments, extra environment and the secrets to mask."""
    user, address, port = params["username"], params["hostname"], str(params.get("port") or 22)
    argv = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-p", port]
    extra: dict = {}
    secrets: list[str] = []
    if via is not None:
        from .hostadmin import for_host
        base = for_host(via).base()
        argv += ["-o", "ProxyCommand=" + shlex.join([*base[:-1], "-W", "%h:%p", base[-1]])]
    if params.get("private-key"):
        key = tmp / "key"
        key.write_text(params["private-key"].rstrip("\n") + "\n", encoding="utf-8")
        os.chmod(key, stat.S_IRUSR | stat.S_IWUSR)
        argv += ["-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes"]
        secrets.append(params["private-key"])
    else:
        askpass = tmp / "askpass.sh"
        askpass.write_text("#!/bin/sh\nprintf '%s' \"$LABAPE_EXEC_PW\"\n", encoding="utf-8")
        os.chmod(askpass, stat.S_IRWXU)
        extra = {"SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": "labape",
                 "LABAPE_EXEC_PW": params.get("password", "")}
        argv += ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                 "-o", "NumberOfPasswordPrompts=1"]
        secrets.append(params.get("password", ""))
    return [*argv, f"{user}@{address}", command], extra, secrets


def _run_linux(params: dict, command: str, via: KvmHost | None, timeout: float, cap: int) -> Result:
    with tempfile.TemporaryDirectory() as d:
        argv, extra, secrets = _ssh_argv(params, command, Path(d), via)
        env = {**hostio.tool_env(), **extra}
        r = run_process(argv, env=env, timeout=timeout, cap=cap)
    r.stdout, r.stderr = mask(r.stdout, secrets), mask(r.stderr, secrets)
    return r


def _run_windows(host: dict, params: dict, command: str, timeout: float, cap: int) -> Result:
    import winrm                                              # noqa: PLC0415 (pywinrm is in the image)
    user, domain = str(params.get("username") or "Administrator"), str(params.get("domain") or "")
    login = f"{domain}\\{user}" if domain and "\\" not in user and "@" not in user else user
    port = int(host.get("ansible_port") or 5986)
    scheme = "https" if port != 5985 else "http"
    start = time.monotonic()
    session = winrm.Session(f"{scheme}://{params['hostname']}:{port}/wsman", auth=(login, params.get("password", "")),
                            transport=str(host.get("ansible_winrm_transport") or "ntlm"),
                            server_cert_validation="ignore", operation_timeout_sec=max(5, int(timeout)),
                            read_timeout_sec=max(10, int(timeout) + 10))
    secrets = [params.get("password", "")]
    try:
        r = session.run_ps(command)
    except Exception as exc:  # noqa: BLE001
        text = mask(str(exc), secrets)
        if "timed out" in text.lower() or "timeout" in text.lower():
            return Result(None, "", "", False, True, time.monotonic() - start)
        raise ExecError(f"WinRM: {text[:300]}") from exc
    out, err = mask(r.std_out.decode(errors="replace"), secrets), mask(r.std_err.decode(errors="replace"), secrets)
    truncated = len(out) > cap or len(err) > cap
    return Result(r.status_code, out[:cap], err[:cap], truncated, False, time.monotonic() - start)


def run(db: Session, env: Environment, vm: str, command: str, *, account: str = "", timeout: float = 60,
        cap: int = 262144) -> Result:
    """Run `command` on `vm` as the access user, or as the environment account `account`."""
    from .. import remote, spread
    from .vmaccess import _hosts, _inventory, remote_env
    try:
        target = remote.vm_target(db, env, vm, account=account)
    except remote.RemoteError as exc:
        raise ExecError(str(exc)) from exc
    params = target["params"]
    host = _hosts(_inventory(db, env)).get(vm) or {}
    windows = target["protocol"] == "rdp" or host.get("ansible_connection") == "winrm"
    via = None
    if remote_env(env):
        via = db.get(KvmHost, spread.vm_host_id(env, vm))
        if via is None or via.access_status != "ready":
            raise ExecError("this VM is on a remote KVM host: an admin sets up that host's SSH access first")
        if windows:
            raise ExecError("commands on Windows VMs of a remote KVM host aren't supported yet (WinRM can't be tunnelled)")
    if windows:
        return _run_windows(host, params, command, timeout, cap)
    return _run_linux(params, command, via, timeout, cap)
