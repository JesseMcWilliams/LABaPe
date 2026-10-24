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


# --- connecting as one of the environment's accounts (E2, question 167) --------------------

RECOVERY_ADMIN = "labape-admin2"


def _vm_kinds(env: Environment) -> dict[str, tuple[str, bool]]:
    """VM -> (windows|linux, is a domain controller)."""
    return {n: ("windows" if str(g.get("os", "")).startswith("windows") else "linux",
                "domain_controller" in (g.get("roles") or []))
            for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}


def account_of(env: Environment, rest: str) -> dict | None:
    """What an environment secret (`environment/<id>/<rest>`) signs in as: {user, scope (local|domain),
    vm (a local account's VM), admin (a domain admin)}; None for a secret that isn't an account."""
    from . import env_accounts
    parts = rest.split("/")
    if parts[0] == "vm" and len(parts) >= 3:
        kind = "/".join(parts[2:])
        user = {"windows_admin": "Administrator", "windows_admin2": RECOVERY_ADMIN}.get(kind)
        if user is None and kind.startswith("account/"):
            user = kind.split("/", 1)[1]
        return {"user": user, "scope": "local", "vm": parts[1], "admin": False} if user else None
    if rest in ("domain_admin", "domain_admin2"):
        return {"user": "Administrator" if rest == "domain_admin" else RECOVERY_ADMIN, "scope": "domain",
                "vm": "", "admin": True}
    if parts[0] == "account" and len(parts) == 2:
        a = next((x for x in env_accounts.accounts(env) if x["name"] == parts[1]), None)
        if a and a["kind"] == "domain":
            return {"user": a["name"], "scope": "domain", "vm": "",
                    "admin": "Domain Admins" in (a.get("groups") or [])}
    return None


def domain_names(db: Session, env: Environment) -> tuple[str, str]:
    """(NetBIOS name, DNS name) of the environment's domain, from the inventory ("" when it has none)."""
    from .engine.vmaccess import _hosts, _inventory
    inv = _inventory(db, env)
    every = [(inv.get("all") or {}).get("vars") or {}, *_hosts(inv).values()]
    pick = lambda key: next((str(h[key]) for h in every if h.get(key)), "")  # noqa: E731
    return pick("netbios_name"), pick("domain_name")


def computer_name(vm: str) -> str:
    """The name Windows knows a VM by: the host name, upper case, at most 15 characters."""
    return vm[:15].upper()


def login_of(env: Environment, rest: str, netbios: str = "", dns: str = "") -> str:
    """What to type as the user name to sign in as `rest`. Not the secret's name (windows_admin): a Windows
    local account is COMPUTER, a backslash, then the user (a bare name is taken as the client's own domain or the
    VM's domain); on a domain controller the local Administrator is the domain's, so DOMAIN, backslash, user;
    a domain account is DOMAIN, backslash, user (or user@dns.name where there is no NetBIOS name); a Linux local
    account is the bare name."""
    bs = chr(92)
    if rest in ("windows_admin", "windows_admin2"):             # the shared copy: each Windows VM has its own account
        return f"<VM name>{bs}{'Administrator' if rest == 'windows_admin' else RECOVERY_ADMIN}"
    acc = account_of(env, rest)
    if acc is None:
        return ""
    kinds = _vm_kinds(env)
    if acc["scope"] == "local":
        os_, dc = kinds.get(acc["vm"], ("", False))
        if os_ != "windows":
            return acc["user"]
        if dc:
            return f"{netbios}{bs}{acc['user']}" if netbios else acc["user"]
        return f"{computer_name(acc['vm'])}{bs}{acc['user']}"
    return f"{netbios}{bs}{acc['user']}" if netbios else (f"{acc['user']}@{dns}" if dns else acc["user"])


def rdp_domain(db: Session, env: Environment, vm: str) -> str:
    """The domain part to put before a VM's local Administrator in an .rdp file."""
    _, dc = _vm_kinds(env).get(vm, ("", False))
    return domain_names(db, env)[0] if dc else computer_name(vm)


def account_targets(env: Environment, rest: str) -> list[dict]:
    """Where an account can sign in, as [{vm, protocol}] (question 167): a local Windows account RDP
    to its VM; a local Linux account SSH to its VM; a domain account RDP to the Windows member
    servers (and the domain controllers for a domain admin) and SSH to the Linux VMs (they join
    the domain when it has a controller)."""
    acc = account_of(env, rest)
    if acc is None:
        return []
    from . import linux_desktop
    kinds = _vm_kinds(env)

    def linux(vm: str, local_user: str = "") -> list[dict]:
        # SSH always; RDP where xrdp runs; VNC on a local account's own display (E3).
        have = linux_desktop.desktops(env, vm)
        out = [{"vm": vm, "protocol": "ssh"}] + ([{"vm": vm, "protocol": "rdp"}] if "xrdp" in have else [])
        if local_user and linux_desktop.vnc_display(env, vm, local_user):
            out.append({"vm": vm, "protocol": "vnc"})
        return out

    if acc["scope"] == "local":
        os_, _ = kinds.get(acc["vm"], ("", False))
        if os_ == "windows":
            return [{"vm": acc["vm"], "protocol": "rdp"}]
        return linux(acc["vm"], acc["user"]) if os_ else []
    out = []
    for vm, (os_, dc) in kinds.items():
        if os_ == "windows" and (not dc or acc["admin"]):
            out.append({"vm": vm, "protocol": "rdp"})
        elif os_ == "linux":
            out += linux(vm)
    return out


def _as_account(db: Session, env: Environment, vm: str, rest: str, protocol: str, host: dict) -> dict:
    """The user name, domain and password `rest` signs in to `vm` with."""
    from .security import get_secret
    if {"vm": vm, "protocol": protocol} not in account_targets(env, rest):
        raise RemoteError(f"{rest} can't sign in to {vm} over {protocol.upper()}")
    acc = account_of(env, rest)
    password = get_secret(db, f"environment/{env.id}/{rest}") or ""
    if not password:
        raise RemoteError(f"{rest} has no stored password")
    if acc["scope"] == "local":
        return {"username": acc["user"], "domain": "", "password": password}
    # The domain's names: the inventory's `all: vars:` (generate-inventory.py), or a host's own.
    from .engine.vmaccess import _hosts, _inventory
    inv = _inventory(db, env)
    every = [host, (inv.get("all") or {}).get("vars") or {}, *_hosts(inv).values()]
    name_of = lambda key: next((str(h[key]) for h in every if h.get(key)), "")  # noqa: E731
    if protocol == "rdp" and _vm_kinds(env).get(vm, ("",))[0] == "windows":
        return {"username": acc["user"], "domain": name_of("netbios_name"), "password": password}
    domain = name_of("domain_name")
    return {"username": f"{acc['user']}@{domain}" if domain else acc["user"], "domain": "", "password": password}


def vm_target(db: Session, env: Environment, vm: str, account: str = "", protocol: str = "") -> dict:
    """Protocol, address and credentials for one of an environment's VMs: as LABaPe's admin, or as
    `account` (an environment secret's name, E2)."""
    from .engine import engine_secrets
    from .engine.vmaccess import _hosts, _inventory
    groups = {n: g for g in (env.spec or {}).get("host_groups", []) for n in instance_names(g)}
    if vm not in groups:
        raise RemoteError(f"{vm} isn't a VM of {env.name}")
    host = _hosts(_inventory(db, env)).get(vm)
    if not host or not host.get("ansible_host"):
        raise RemoteError(f"{vm} has no address yet (deploy the environment, or refresh DHCP addresses)")
    address = str(host["ansible_host"])
    windows = str(groups[vm].get("os", "")).startswith("windows")
    if account:
        protocol = protocol or ("rdp" if windows else "ssh")
        who = _as_account(db, env, vm, account, protocol, host)
        if protocol == "vnc":                     # E3: the account's own TigerVNC display
            from . import linux_desktop
            display = linux_desktop.vnc_display(env, vm, account_of(env, account)["user"])
            return {"protocol": "vnc", "address": address, "params": {
                "hostname": address, "port": str(linux_desktop.VNC_BASE + display), "password": who["password"]}}
        if protocol == "rdp":                     # Windows, or a Linux VM with xrdp (E3)
            return {"protocol": "rdp", "address": address, "params": {
                "hostname": address, "port": "3389", **who, "security": "any", "ignore-cert": "true",
                "resize-method": "display-update", "enable-font-smoothing": "true", "disable-audio": "true"}}
        return {"protocol": "ssh", "address": address, "params": {
            "hostname": address, "port": "22", "username": who["username"], "password": who["password"],
            "color-scheme": "gray-black", "font-size": "11", "scrollback": "2000", **_vm_host_keys(db, address)}}
    if windows:
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
    from .engine import vmadmin
    user = str(host.get("ansible_user") or "labape")
    key = vmadmin.vm_key_text(db, address, user)       # the current key, or the VM's earlier one after adding it
    return {"protocol": "ssh", "address": address, "params": {
        "hostname": address, "port": "22", "username": user, "private-key": key,
        "color-scheme": "gray-black", "font-size": "11", "scrollback": "2000", **_vm_host_keys(db, address)}}


def _vm_host_keys(db: Session, address: str) -> dict:
    """The SSH host keys recorded for a VM (question 198): `known-keys` for the commands and text sessions,
    and `host-key` (one known_hosts line) for guacd's browser SSH. Empty until a record exists."""
    from .engine import vmidentity
    value = vmidentity.trusted(db, address, vmidentity.SSH)
    if not value:
        return {}
    lines = vmidentity.known_hosts(address, value).splitlines()
    best = next((x for x in lines if " ssh-ed25519 " in x), lines[0])
    return {"known-keys": value, "host-key": best}


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
        t = console_target(env, s.target, s.protocol) if s.protocol in ("vnc", "serial") and not s.account else \
            vm_target(db, env, s.target, s.account or "", s.protocol)
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
