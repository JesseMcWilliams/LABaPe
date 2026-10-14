"""Browser remote access through guacd (design §28.5, questions 116-119).

LABaPe is the Guacamole tunnel: the browser runs guacamole-common-js and
opens a WebSocket to the API; the API checks the session's one-time token,
connects to guacd (in the stack, never published), performs the Guacamole
handshake with the target's address and credentials filled in server-side,
and then relays instructions both ways. Credentials never reach the browser.

Targets: an environment's VM (owners and admins, question 118): RDP for
Windows (the environment's Administrator or domain Administrator), SSH for
Linux (the admin user with the VM key); a KVM host (admins): SSH as its
LABaPe access account with the host-admin key; a VM's own console (question
119) through its KVM host: serial for Linux, VNC for desktops. Sessions are recorded when
Settings → Remote access says so (question 117), and listed for auditors.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import secrets as _secrets
import shlex
import uuid

from sqlalchemy.orm import Session, object_session

from . import appsettings, spread
from .models import Environment, KvmHost, RemoteSession
from .naming import instance_names

GUACD = ("labape-guacd", 4822)
RECORDING_DIR = "/home/guacd"                        # inside guacd (the recordings volume)
TOKEN_SECONDS = 120


class RemoteError(ValueError):
    pass


# --- Guacamole protocol -------------------------------------------------------------------

def encode(*elements: str) -> str:
    """One instruction: LENGTH.VALUE,LENGTH.VALUE,...; (lengths in Unicode code points)."""
    return ",".join(f"{len(e)}.{e}" for e in elements) + ";"


def parse(buffer: str) -> tuple[list[list[str]], str]:
    """Complete instructions at the start of `buffer`, and what's left over (a partial one)."""
    out, pos = [], 0
    while True:
        elems, p = [], pos
        while True:
            dot = buffer.find(".", p)
            if dot < 0:
                return out, buffer[pos:]
            try:
                n = int(buffer[p:dot])
            except ValueError as exc:
                raise RemoteError(f"bad instruction from guacd near {buffer[p:p + 20]!r}") from exc
            end = dot + 1 + n
            if end >= len(buffer):
                return out, buffer[pos:]
            elems.append(buffer[dot + 1:end])
            sep = buffer[end]
            p = end + 1
            if sep == ";":
                out.append(elems)
                pos = p
                break
            if sep != ",":
                raise RemoteError("bad separator in a guacd instruction")


def complete_prefix(buffer: str) -> tuple[str, str]:
    """(text made of whole instructions, the rest): the browser wants whole instructions per message."""
    instructions, rest = parse(buffer)
    return buffer[:len(buffer) - len(rest)], rest


# --- what to connect to -------------------------------------------------------------------

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    t = _secrets.token_urlsafe(32)
    return t, token_hash(t)


def vm_target(db: Session, env: Environment, vm: str) -> dict:
    """Protocol, address and credentials for one of an environment's VMs."""
    from .engine import engine_secrets
    from .engine.vmaccess import _hosts, _inventory
    groups = {n: g for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}
    if vm not in groups:
        raise RemoteError(f"{vm} isn't a VM of {env.name}")
    host = _hosts(_inventory(db, env)).get(vm)
    if not host or not host.get("ansible_host"):
        raise RemoteError(f"{vm} has no address yet (deploy the environment, or refresh DHCP addresses)")
    address = str(host["ansible_host"])
    if str(groups[vm].get("os", "")).startswith("windows"):
        user = str(host.get("ansible_user") or "Administrator")
        domain = ""
        if "\\" in user:
            domain, user = user.split("\\", 1)
        elif "@" in user:
            user, domain = user.split("@", 1)
        return {"protocol": "rdp", "address": address, "params": {
            "hostname": address, "port": "3389", "username": user, "password": str(host.get("ansible_password") or ""),
            "domain": domain, "security": "any", "ignore-cert": "true", "resize-method": "display-update",
            "enable-font-smoothing": "true", "disable-audio": "true"}}
    key = engine_secrets._get(db, engine_secrets.SSH_KEY)
    return {"protocol": "ssh", "address": address, "params": {
        "hostname": address, "port": "22", "username": str(host.get("ansible_user") or "labape"), "private-key": key,
        "color-scheme": "gray-black", "font-size": "11", "scrollback": "2000"}}


def host_target(h: KvmHost) -> dict:
    from .engine import hostadmin
    if h.access_status != "ready":
        raise RemoteError("set up SSH access to this host first")
    key = hostadmin.key_path().read_text(encoding="utf-8")
    return {"protocol": "ssh", "address": h.ssh_address, "params": {
        "hostname": h.ssh_address, "port": str(h.ssh_port or 22), "username": h.ssh_user, "private-key": key,
        "color-scheme": "gray-black", "font-size": "11", "scrollback": "2000"}}


def console_target(env: Environment, vm: str, kind: str) -> dict:
    """A VM's own console through its KVM host (question 119), as the host's LABaPe access
    account: the serial console is `virsh console` in an SSH session; VNC (libvirt listens on
    the host's loopback, without a password) goes through a one-shot forwarder in the API,
    `ssh -W` to the host, started when the tunnel opens."""
    from .engine import hostadmin
    groups = {n: g for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}
    if vm not in groups:
        raise RemoteError(f"{vm} isn't a VM of {env.name}")
    h = object_session(env).get(KvmHost, spread.vm_host_id(env, vm))     # the VM's own host (design §45)
    if h is None or h.access_status != "ready":
        raise RemoteError("consoles go through the KVM host: an admin sets up its SSH access first "
                          "(Admin → KVM hosts → SSH access)")
    windows = str(groups[vm].get("os", "")).startswith("windows")
    if kind == "serial":
        if windows:
            raise RemoteError(f"{vm} is a Windows VM: it has no serial console (use VNC or RDP)")
        return {"protocol": "ssh", "address": h.ssh_address, "params": {
            "hostname": h.ssh_address, "port": str(h.ssh_port or 22), "username": h.ssh_user,
            "private-key": hostadmin.key_path().read_text(encoding="utf-8"),
            "command": f"sudo -n virsh -c qemu:///system console {shlex.quote(vm)} --force",
            "color-scheme": "gray-black", "font-size": "11", "scrollback": "2000"}}
    return {"protocol": "vnc", "address": h.ssh_address,
            "params": {"hostname": "", "port": "", "color-depth": "24", "cursor": "local"},
            "forward": {"address": h.ssh_address, "port": h.ssh_port or 22, "user": h.ssh_user, "domain": vm}}


def resolve(db: Session, s: RemoteSession) -> dict:
    if s.target_type == "vm":
        env = db.get(Environment, s.environment_id)
        if env is None or env.status != "deployed":
            raise RemoteError("the environment isn't deployed")
        t = console_target(env, s.target, s.protocol) if s.protocol in ("vnc", "serial") else \
            vm_target(db, env, s.target)
    else:
        h = db.get(KvmHost, s.kvm_host_id)
        if h is None:
            raise RemoteError("no such KVM host")
        t = host_target(h)
    if t["protocol"] != {"serial": "ssh"}.get(s.protocol, s.protocol):
        raise RemoteError(f"{s.target} is reached over {t['protocol'].upper()}, not {s.protocol.upper()}")
    if appsettings.get(db, "remote").record_sessions:
        t["params"].update({"recording-path": RECORDING_DIR, "recording-name": f"session-{s.id}",
                            "create-recording-path": "true", "recording-include-keys": "false"})
    return t


def rdp_file(address: str, user: str, domain: str = "") -> str:
    """A .rdp file for a native client on the lab network; it asks for the password itself."""
    lines = [f"full address:s:{address}", f"username:s:{(domain + chr(92)) if domain else ''}{user}",
             "prompt for credentials:i:1", "authentication level:i:2", "screen mode id:i:2",
             "use multimon:i:0", "session bpp:i:32", "redirectclipboard:i:1", "audiomode:i:2"]
    return "\r\n".join(lines) + "\r\n"


# --- the VNC forwarder --------------------------------------------------------------------

API_HOST = "labape-api"                              # how guacd reaches the forwarder
FORWARD_WAIT = 30                                    # seconds guacd has to connect


def vnc_port(fwd: dict) -> int:
    """The VM's VNC port on its host (`virsh vncdisplay` gives 127.0.0.1:N, port 5900 + N)."""
    from .engine import hostadmin
    ssh = hostadmin.Ssh(fwd["address"], fwd["port"], fwd["user"])
    r = ssh.run(f"sudo -n virsh -c qemu:///system vncdisplay {shlex.quote(fwd['domain'])}", timeout=20)
    out = (r.stdout or b"").decode(errors="replace").strip()
    m = re.match(r"^\[?[0-9a-fA-F.:]*\]?:(\d+)$", out)
    if r.returncode != 0 or not m:
        err = (r.stderr or b"").decode(errors="replace").strip()[-200:]
        raise RemoteError(f"{fwd['domain']} has no VNC display (is it running?) {err}".strip())
    return 5900 + int(m.group(1))


async def _pump(reader: asyncio.StreamReader, writer) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def start_forward(fwd: dict, allow_host: str = GUACD[0]):
    """Listen on an ephemeral port in the API container for exactly one connection, from guacd,
    and pipe it to the VM's VNC port on the KVM host through `ssh -W`. Returns (server, port);
    close the server when the session ends."""
    from .engine import hostadmin, hostio
    loop = asyncio.get_running_loop()
    port = await asyncio.to_thread(vnc_port, fwd)
    allowed = {ai[4][0] for ai in await loop.getaddrinfo(allow_host, None)} if allow_host else set()
    base = hostadmin.Ssh(fwd["address"], fwd["port"], fwd["user"]).base()
    cmd = [*base[:-1], "-W", f"127.0.0.1:{port}", base[-1]]
    used = asyncio.Event()

    async def handle(reader, writer):
        peer = (writer.get_extra_info("peername") or ("",))[0]
        if used.is_set() or (allowed and peer not in allowed):
            writer.close()
            return
        used.set()
        server.close()
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL, env=hostio.tool_env())
        try:
            await asyncio.gather(_pump(reader, proc.stdin), _pump(proc.stdout, writer))
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    server = await asyncio.start_server(handle, "0.0.0.0", 0)

    async def expire():
        await asyncio.sleep(FORWARD_WAIT)
        if not used.is_set():
            server.close()
    asyncio.create_task(expire())
    return server, server.sockets[0].getsockname()[1]


# --- the handshake and relay --------------------------------------------------------------

async def _read_instruction(reader: asyncio.StreamReader, buf: list[str], timeout: float = 15) -> list[str]:
    while True:
        instructions, rest = parse(buf[0])
        if instructions:
            first = instructions[0]
            consumed = len(encode(*first))
            buf[0] = buf[0][consumed:]
            return first
        data = await asyncio.wait_for(reader.read(65536), timeout)
        if not data:
            raise RemoteError("guacd closed the connection")
        buf[0] += data.decode("utf-8", errors="replace")


async def open_guacd(target: dict, width: int, height: int, dpi: int, address=GUACD):
    """Connect to guacd and finish the handshake. Returns (reader, writer, leftover text)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(*address), 10)
    buf = [""]
    writer.write(encode("select", target["protocol"]).encode())
    await writer.drain()
    args = await _read_instruction(reader, buf)
    if not args or args[0] != "args":
        raise RemoteError(f"unexpected reply from guacd: {args[:1]}")
    params = target["params"]
    values = [a if a.startswith("VERSION_") else params.get(a, "") for a in args[1:]]
    for instr in (("size", str(width), str(height), str(dpi)), ("audio",), ("video",),
                  ("image", "image/png", "image/jpeg", "image/webp"), ("timezone", "UTC"), ("connect", *values)):
        writer.write(encode(*instr).encode())
    await writer.drain()
    ready = await _read_instruction(reader, buf, timeout=30)
    if ready[0] == "error":
        raise RemoteError(f"guacd refused the connection: {ready[1] if len(ready) > 1 else ''}")
    if ready[0] != "ready":
        raise RemoteError(f"unexpected reply from guacd: {ready[0]}")
    return reader, writer, buf[0]


INPUT_OPCODES = {"key", "mouse", "touch", "clipboard", "size"}


async def relay(ws, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, leftover: str,
                idle_seconds: int) -> str:
    """Pump instructions both ways until either side closes or the session is idle too long.
    Returns why it ended."""
    loop = asyncio.get_running_loop()
    last_input = [loop.time()]
    await ws.send_text(encode("", str(uuid.uuid4())))       # the tunnel's UUID (internal instruction)
    if leftover:
        whole, rest = complete_prefix(leftover)
        if whole:
            await ws.send_text(whole)
        leftover = rest

    async def from_guacd():
        pending = leftover
        while True:
            data = await reader.read(65536)
            if not data:
                return "the remote side closed the connection"
            pending += data.decode("utf-8", errors="replace")
            whole, pending = complete_prefix(pending)
            if whole:
                await ws.send_text(whole)
                for instr in parse(whole)[0]:
                    if instr and instr[0] == "error":          # guacd gave up (refused, auth failed, ...)
                        return f"failed: {instr[1] if len(instr) > 1 else 'error'}"

    async def from_browser():
        while True:
            msg = await ws.receive_text()
            for instr in parse(msg)[0]:
                if instr and instr[0] == "" and len(instr) > 1 and instr[1] == "ping":
                    await ws.send_text(msg)                  # internal ping: answer it ourselves
                    break
                if instr and instr[0] in INPUT_OPCODES:
                    last_input[0] = loop.time()
                if instr and instr[0] == "disconnect":
                    writer.write(msg.encode())
                    await writer.drain()
                    return "closed by the user"
            else:
                writer.write(msg.encode())
                await writer.drain()

    async def idle_watch():
        while True:
            await asyncio.sleep(30)
            if loop.time() - last_input[0] > idle_seconds:
                return f"closed after {idle_seconds // 60} idle minutes"

    tasks = [asyncio.create_task(c) for c in (from_guacd(), from_browser(), idle_watch())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    why = "ended"
    for t in done:
        try:
            why = t.result() or why
        except Exception as exc:  # noqa: BLE001 — a closed socket ends the session
            why = f"ended: {type(exc).__name__}"
    try:
        writer.close()
    except Exception:  # noqa: BLE001
        pass
    return why
