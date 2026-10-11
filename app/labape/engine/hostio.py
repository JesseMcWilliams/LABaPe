"""Reaching a KVM host: locally, or over SSH for a `qemu+ssh://` host
(design §22.4, phase 10e).

A remote host runs the same engine scripts the CLI runs there: the
worker ships the job's workspace with tar over SSH, runs the command on
the host with OpenTofu's state reached through an SSH reverse tunnel to
PostgreSQL, copies the generated inventory back and removes the copy.
libvirt calls from the worker itself (host stats, guest info) go straight
to the `qemu+ssh` URI. The host needs what `install-labape.py --profile
kvm` installs: libvirt, OpenTofu, Ansible and Packer for the service user.

The worker's key for remote hosts is `~/.ssh/labape_kvm` in the job HOME,
generated on first use; its public key is shown on the host's page and is
added to the service user's `authorized_keys` on the KVM host.
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import get_settings

KEY_NAME = "labape_kvm"
REMOTE_ROOT = ".labape/jobs"          # under the service user's home on the KVM host
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=15",
            "-o", "ConnectTimeout=15"]


def is_remote(uri: str) -> bool:
    return uri.startswith("qemu+ssh://")


def home() -> Path:
    return get_settings().data_dir / "home"


def key_path() -> Path:
    return home() / ".ssh" / KEY_NAME


def ensure_key() -> str:
    """The worker's SSH public key for remote hosts, generating the pair once."""
    key = key_path()
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(key.parent, 0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "labape-worker", "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
    return key.with_name(KEY_NAME + ".pub").read_text(encoding="utf-8").strip()


def effective_uri(uri: str) -> str:
    """A qemu+ssh URI with the worker's key and no TTY prompts added."""
    if not is_remote(uri):
        return uri
    parts = urlsplit(uri)
    q = dict(parse_qsl(parts.query))
    q.setdefault("keyfile", str(key_path()))
    q.setdefault("no_tty", "1")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/system", urlencode(q), ""))


def tool_env() -> dict[str, str]:
    """Environment for virsh/ssh run by the worker: its HOME holds known_hosts."""
    return {**os.environ, "HOME": str(home())}


class Host:
    """Run commands and Python snippets on a KVM host, locally or over SSH."""

    def __init__(self, libvirt_uri: str):
        self.uri = libvirt_uri
        self.remote = is_remote(libvirt_uri)
        if self.remote:
            parts = urlsplit(libvirt_uri)
            if not parts.hostname:
                raise ValueError(f"{libvirt_uri}: no host name")
            self.target = f"{parts.username}@{parts.hostname}" if parts.username else parts.hostname
            self.port = parts.port

    def ssh_base(self, *extra: str) -> list[str]:
        return ["ssh", "-i", str(key_path()), *SSH_OPTS, *(["-p", str(self.port)] if self.port else []), *extra,
                self.target]

    _primed: set[str] = set()

    def prime(self) -> None:
        """Record the host key once (accept-new): libvirt's own ssh transport
        refuses an unknown host, and it reads the same known_hosts."""
        if self.remote and self.target not in Host._primed:
            r = self.run("true", timeout=30)
            if r.returncode == 0:
                Host._primed.add(self.target)

    def run(self, command: str, input: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
        """Run a shell command on the host."""
        if self.remote:
            argv = [*self.ssh_base(), command]
        else:
            argv = ["bash", "-c", command]
        return subprocess.run(argv, input=input, capture_output=True, timeout=timeout, env=tool_env(), check=False)

    def python(self, script: str, args: dict, timeout: float = 60) -> dict:
        """Run a Python snippet that reads JSON args on stdin and prints JSON."""
        payload = json.dumps({"script": script, "args": args}).encode()
        boot = "import json,sys;d=json.load(sys.stdin);exec(d['script'],{'ARGS':d['args']})"
        if self.remote:
            argv = [*self.ssh_base(), f"python3 -c {shlex.quote(boot)}"]
        else:
            argv = [sys.executable, "-c", boot]
        r = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout, env=tool_env(), check=False)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).decode(errors="replace").strip()[-500:] or f"exit {r.returncode}")
        return json.loads(r.stdout.decode())


# --- snippets (run on the host with its own python3) ------------------------------

PROBE = r'''
import json, os, shutil
out = {"load": [], "cpu": None, "disk": None, "storage": {}}
try:
    out["load"] = [float(x) for x in open("/proc/loadavg").read().split()[:3]]
    f = [int(x) for x in open("/proc/stat").readline().split()[1:9]]
    out["cpu"] = [f[3] + f[4], sum(f)]
except OSError:
    pass
for key, path in ARGS["paths"].items():
    try:
        u = shutil.disk_usage(path)
        out["storage"][key] = {"path": path, "total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1)}
    except OSError:
        out["storage"][key] = {"path": path, "total_gb": 0, "free_gb": 0}
try:
    st = os.stat(ARGS["paths"]["vm"])
    want = (os.major(st.st_dev), os.minor(st.st_dev))
    for line in open("/proc/diskstats"):
        f = line.split()
        if (int(f[0]), int(f[1])) == want:
            out["disk"] = [int(f[5]), int(f[9])]
except (OSError, ValueError, IndexError, KeyError, AttributeError):
    pass
print(json.dumps(out))
'''

TEMPLATES = r'''
import json, math, os, subprocess
lib, out = ARGS["lib"], []
if os.path.isdir(lib):
    for name in sorted(os.listdir(lib)):
        if not name.endswith(".qcow2"):
            continue
        base = name[:-6]
        meta = None
        try:
            meta = json.load(open(os.path.join(lib, base + ".json")))
        except (OSError, ValueError):
            pass
        size = 0
        try:
            info = subprocess.run(["qemu-img", "info", "-U", "--output=json", os.path.join(lib, name)],
                                  capture_output=True, text=True, timeout=30, check=True).stdout
            size = math.ceil(json.loads(info)["virtual-size"] / 2**30)
        except Exception:
            pass
        out.append({"name": base, "meta": meta, "disk_gb": size})
print(json.dumps({"exists": os.path.isdir(lib), "templates": out}))
'''

WRITE_FILES = r'''
import json, os
for path, text in ARGS["files"].items():
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o644)
print(json.dumps({"written": len(ARGS["files"])}))
'''

CHECK = r'''
import json, os, shutil
tools = {t: bool(shutil.which(t) or os.path.exists(os.path.expanduser("~/.local/bin/" + t)))
         for t in ("virsh", "virt-install", "qemu-img", "xorrisofs", "tofu", "ansible-playbook", "packer", "tar")}
print(json.dumps({"tools": tools, "home": os.path.expanduser("~"),
                  "paths": {p: os.path.isdir(p) and os.access(p, os.W_OK) for p in ARGS["paths"]}}))
'''


# --- running a job on a remote host --------------------------------------------

class RemoteJob:
    """Ship a job's workspace to a remote host, run it there, bring the
    outputs back. Secrets (vault password, SSH key, state connection) live
    only in the remote copy, mode 0700, and are removed with it."""

    def __init__(self, host: Host, work: Path, job_id: int):
        self.host, self.work, self.job_id = host, work, job_id
        r = host.run("echo $HOME")
        if r.returncode != 0:
            raise RuntimeError(f"can't reach {host.target} over SSH: {r.stderr.decode(errors='replace').strip()}")
        self.root = f"{r.stdout.decode().strip()}/{REMOTE_ROOT}/{job_id}"
        self.port = 20000 + secrets.randbelow(40000)

    def remote_path(self, local: str) -> str:
        """A workspace path as the remote host sees it (other arguments unchanged)."""
        if local == str(self.work) or local.startswith(str(self.work) + os.sep):
            rel = Path(local).relative_to(self.work).as_posix()
            return self.root if rel == "." else f"{self.root}/{rel}"
        return local

    def push(self, env: dict[str, str], extra_files: dict[str, Path]) -> dict[str, str]:
        """Copy the workspace and secrets; returns the environment to run with."""
        sec = self.work / ".labape-remote"
        sec.mkdir(exist_ok=True)
        os.chmod(sec, 0o700)
        remote_env = {}
        for name, src in extra_files.items():
            (sec / name).write_bytes(src.read_bytes())
            os.chmod(sec / name, 0o644 if name.endswith(".pub") else 0o600)
        for k, v in env.items():
            if k in ("LABAPE_VAULT_PASS_FILE",):
                remote_env[k] = f"{self.root}/.labape-remote/vault-pass"
            elif k == "LABAPE_SSH_PRIVATE_KEY_PATH":
                remote_env[k] = f"{self.root}/.labape-remote/ssh-key" if v else ""
            elif k == "PG_CONN_STR":
                remote_env[k] = tunnel_conn(v, self.port)
            elif k in ("ANSIBLE_FORCE_COLOR", "TF_IN_AUTOMATION", "PYTHONUNBUFFERED", "LABAPE_CERT_TOKEN",
                       "LABAPE_JOB_ID", "LABAPE_CA_RENEW_URL", "LABAPE_CERT_API"):
                remote_env[k] = v
        remote_env["LIBVIRT_URI"] = "qemu:///system"
        remote_env["TF_PLUGIN_CACHE_DIR"] = "$HOME/.labape/tofu-plugins"
        lines = [f"export {k}={shlex.quote(v) if not v.startswith('$HOME') else v}" for k, v in remote_env.items()]
        (sec / "env").write_text("\n".join(lines) + "\nmkdir -p \"$TF_PLUGIN_CACHE_DIR\"\n", encoding="utf-8")
        os.chmod(sec / "env", 0o600)
        tar = subprocess.Popen(["tar", "-C", str(self.work), "-czf", "-", "."], stdout=subprocess.PIPE)
        r = subprocess.run([*self.host.ssh_base(),
                            f"umask 077 && rm -rf {shlex.quote(self.root)} && mkdir -p {shlex.quote(self.root)} && "
                            f"tar -xzf - -C {shlex.quote(self.root)}"],
                           stdin=tar.stdout, capture_output=True, timeout=600, env=tool_env(), check=False)
        tar.stdout.close()
        tar.wait()
        if r.returncode != 0 or tar.returncode != 0:
            raise RuntimeError(f"copying the job to {self.host.target} failed: {r.stderr.decode(errors='replace').strip()}")
        return remote_env

    def command(self, cmd: list[str]) -> list[str]:
        """The local ssh command that runs `cmd` on the host. A TTY makes the
        remote side get SIGHUP when the worker kills ssh (cancel)."""
        inner = " ".join(shlex.quote(self.remote_path(a)) for a in cmd)
        script = (f"stty -onlcr 2>/dev/null; cd {shlex.quote(self.root)} && . ./.labape-remote/env && exec {inner}")
        pg = urlsplit(get_settings().tofu_pg_conn)
        if not pg.hostname:
            raise RuntimeError("LABAPE_TOFU_PG_CONN isn't set, so a remote host can't reach the state backend")
        return [*self.host.ssh_base("-tt", "-o", "ExitOnForwardFailure=yes",
                                    "-R", f"127.0.0.1:{self.port}:{pg.hostname}:{pg.port or 5432}"),
                f"bash -lc {shlex.quote(script)}"]

    def pull(self, rel: str) -> None:
        """Copy a directory of the remote workspace back over the local one."""
        r = subprocess.run([*self.host.ssh_base(),
                            f"cd {shlex.quote(self.root)} && test -d {shlex.quote(rel)} && tar -czf - {shlex.quote(rel)}"],
                           capture_output=True, timeout=300, env=tool_env(), check=False)
        if r.returncode == 0 and r.stdout:
            subprocess.run(["tar", "-C", str(self.work), "-xzf", "-"], input=r.stdout, check=True, timeout=300)

    def cleanup(self) -> None:
        self.host.run(f"rm -rf {shlex.quote(self.root)}", timeout=120)
        sec = self.work / ".labape-remote"
        for f in sec.iterdir() if sec.is_dir() else []:
            f.unlink(missing_ok=True)


def tunnel_conn(conn: str, port: int) -> str:
    """The state connection string with its host replaced by the tunnel's end."""
    parts = urlsplit(conn)
    userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    return urlunsplit((parts.scheme, f"{userinfo}127.0.0.1:{port}", parts.path, parts.query, parts.fragment))


def sync_iso(host: Host, local: Path, remote_dir: str, log) -> str:
    """Make sure an ISO is on the remote host (same name and size); returns its remote path."""
    remote = f"{remote_dir}/{local.name}"
    size = local.stat().st_size
    r = host.run(f"stat -c %s {shlex.quote(remote)} 2>/dev/null || echo 0")
    if r.stdout.decode().strip() == str(size):
        return remote
    log.write(f"labape: copying {local.name} ({size / 2**30:.1f} GB) to {host.target}:{remote_dir}\n")
    with open(local, "rb") as fh:
        res = subprocess.run([*host.ssh_base(),
                              f"mkdir -p {shlex.quote(remote_dir)} && cat > {shlex.quote(remote)}.part && "
                              f"mv {shlex.quote(remote)}.part {shlex.quote(remote)}"],
                             stdin=fh, capture_output=True, timeout=6 * 3600, env=tool_env(), check=False)
    if res.returncode != 0:
        raise RuntimeError(f"copying {local.name} failed: {res.stderr.decode(errors='replace').strip()}")
    return remote
