"""An MCP server over LABaPe's REST API (design §59.5, batch X4).

It adds no authority of its own: every tool is one REST call made with the caller's own API token, so
roles, the token's scope and limits, rate limits, audit and maintenance mode all apply as they do for a
script. `tools/list` offers only the tools the token's roles allow. Two transports share this module:

- `labape mcp` / `python -m labape.mcp` (stdio): for a local agent; LABAPE_URL and LABAPE_TOKEN say where and
  as whom (LABAPE_CA_FILE trusts the stack's platform CA, LABAPE_INSECURE=1 skips verification). It needs only
  Python's standard library, which is also what the Claude Desktop bundle (tools/helpers/build_mcpb.py) ships.
- `POST /mcp` (streamable HTTP, JSON responses): for a remote agent; the same bearer token, forwarded to the
  API in-process (`api/mcp.py`).

The protocol is the small part of MCP that tools need (initialize, ping, tools/list, tools/call), written
out here so there is no extra dependency.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable

SERVER_VERSION = "1"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 60000

READ = ("viewer", "deployer", "template_editor", "file_manager", "auditor")      # any of these, or admin
DEPLOY = ("deployer", "template_editor")
CONSOLE = ("console",)

INSTRUCTIONS = (
    "LABaPe manages lab environments (VMs on KVM hosts). Start with console_targets (token with the console role) "
    "or list_environments to find environment ids and VM names. run_command runs one command and returns its "
    "result; open_session gives an interactive shell or serial console: send text with session_input and read "
    "with session_output, passing back the `next` offset each time (wait up to 30 s for output). Close sessions "
    "when done. Everything is audited and limited by the token; output may contain secrets the console prints.")


@dataclass
class Tool:
    name: str
    description: str
    method: str
    path: str
    roles: tuple[str, ...]
    props: dict = field(default_factory=dict)         # JSON-schema properties
    required: tuple[str, ...] = ()
    destructive: bool = False
    read_only: bool = False

    def schema(self) -> dict:
        return {"type": "object", "properties": self.props, "required": list(self.required)}

    def listing(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema(),
                "annotations": {"readOnlyHint": self.read_only, "destructiveHint": self.destructive}}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def _str(desc: str, **extra) -> dict:
    return {"type": "string", "description": desc, **extra}


ENV = {"environment_id": _int("The environment's id")}
VM = {**ENV, "vm": _str("The VM's name in that environment")}

TOOLS: list[Tool] = [
    Tool("console_targets", "The environments and VMs this token can open consoles on (ids, VM names, status), and "
         "whether it may open KVM host consoles.", "GET", "/api/console/environments", CONSOLE, read_only=True),
    Tool("run_command", "Run one command on a VM and return {exit_code, stdout, stderr, truncated, timed_out}. "
         "Linux: a shell command; Windows: PowerShell. Output past the token's limit stops the command.",
         "POST", "/api/environments/{environment_id}/vms/{vm}/exec", CONSOLE,
         {**VM, "command": _str("The command"), "timeout": _int("Seconds to wait, 1-3600 (default 60)"),
          "account": _str("An environment account to run as; some tokens must name one")}, ("environment_id", "vm", "command")),
    Tool("open_session", "Open an interactive text session: kind 'shell' (ssh) or 'serial' (the VM's serial console "
         "through its KVM host), or give kvm_host_id for a KVM host's shell (only tokens allowed host consoles). "
         "Returns the session id and the first `next` offset. One session per target per token.",
         "POST", "/api/console/sessions", CONSOLE,
         {"environment_id": _int("The environment's id"), "vm": _str("The VM's name"),
          "kvm_host_id": _int("A KVM host's id, instead of environment_id and vm"),
          "kind": _str("shell or serial", enum=["shell", "serial"]), "account": _str("An environment account to sign in as")}),
    Tool("session_input", "Type text into a session. End a line with a newline; \\u0003 is Ctrl-C.",
         "POST", "/api/console/sessions/{session_id}/input", CONSOLE,
         {"session_id": _int("The session id"), "data": _str("The text to type")}, ("session_id", "data")),
    Tool("session_output", "Read what a session printed after offset `after` (start with 0, then pass back `next`). "
         "`wait` (seconds, up to 30) holds the call for new output. `lost` counts bytes that were dropped.",
         "GET", "/api/console/sessions/{session_id}/output", CONSOLE,
         {"session_id": _int("The session id"), "after": _int("Offset to read from"), "wait": {"type": "number",
          "description": "Seconds to wait for output, 0-30"}}, ("session_id",), read_only=True),
    Tool("close_session", "Close a session.", "DELETE", "/api/console/sessions/{session_id}", CONSOLE,
         {"session_id": _int("The session id")}, ("session_id",)),
    Tool("list_sessions", "This token's open sessions.", "GET", "/api/console/sessions", CONSOLE, read_only=True),
    Tool("list_environments", "Environments you can see (id, name, status, owner, lease).", "GET", "/api/environments",
         READ, read_only=True),
    Tool("get_environment", "One environment: its spec, status and details.", "GET", "/api/environments/{environment_id}",
         READ, ENV, ("environment_id",), read_only=True),
    Tool("list_vms", "An environment's VMs with their state and addresses.", "GET", "/api/environments/{environment_id}/vms",
         READ, ENV, ("environment_id",), read_only=True),
    Tool("get_vm", "One VM of an environment.", "GET", "/api/environments/{environment_id}/vms/{vm}", READ, VM,
         ("environment_id", "vm"), read_only=True),
    Tool("list_hosts", "KVM hosts (admins see all).", "GET", "/api/hosts", READ, read_only=True),
    Tool("get_host", "One KVM host.", "GET", "/api/hosts/{host_id}", READ, {"host_id": _int("The host's id")},
         ("host_id",), read_only=True),
    Tool("list_jobs", "Recent jobs (deploys, destroys...), newest first; filter by environment name, type prefix or state.",
         "GET", "/api/jobs", READ, {"limit": _int("How many (default 100)"), "environment": _str("Environment name contains"),
                                    "type": _str("Job type prefix"), "state": _str("Job state")}, read_only=True),
    Tool("get_job", "One job's state and details.", "GET", "/api/jobs/{job_id}", READ, {"job_id": _int("The job's id")},
         ("job_id",), read_only=True),
    Tool("latest_plan", "The newest redeploy preview of an environment.", "GET", "/api/environments/{environment_id}/plan",
         READ, ENV, ("environment_id",), read_only=True),
    Tool("deploy_environment", "Deploy (or redeploy) an environment. Returns the job id; follow it with get_job.", "POST",
         "/api/environments/{environment_id}/deploy", DEPLOY, ENV, ("environment_id",)),
    Tool("plan_redeploy", "Preview which VMs a redeploy would replace; nothing is applied. Returns a job id.", "POST",
         "/api/environments/{environment_id}/plan", DEPLOY, ENV, ("environment_id",)),
    Tool("refresh_addresses", "Re-find a deployed environment's DHCP addresses. Returns a job id.", "POST",
         "/api/environments/{environment_id}/refresh-addresses", DEPLOY, ENV, ("environment_id",)),
    Tool("destroy_environment", "Destroy an environment's VMs. Returns a job id. Destructive.", "POST",
         "/api/environments/{environment_id}/destroy", DEPLOY, ENV, ("environment_id",), destructive=True),
    Tool("cancel_job", "Cancel a queued or running job.", "POST", "/api/jobs/{job_id}/cancel", DEPLOY,
         {"job_id": _int("The job's id")}, ("job_id",), destructive=True),
]
BY_NAME = {t.name: t for t in TOOLS}

# send(method, path, params, json_body) -> (status, parsed body or text)
Send = Callable[[str, str, dict, dict | None], Awaitable[tuple[int, object]]]


def offered(tool: Tool, roles: set[str]) -> bool:
    return "admin" in roles or any(r in roles for r in tool.roles)


def build_request(tool: Tool, args: dict) -> tuple[str, dict, dict | None]:
    """The path, query and body for a call: arguments named in the path go there, the rest in the query (GET,
    DELETE) or the body."""
    unknown = set(args) - set(tool.props)
    if unknown:
        raise ValueError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    missing = [r for r in tool.required if args.get(r) in (None, "")]
    if missing:
        raise ValueError(f"missing argument(s): {', '.join(missing)}")
    names = re.findall(r"{(\w+)}", tool.path)
    path = tool.path
    for n in names:
        path = path.replace("{" + n + "}", str(args[n]))
    rest = {k: v for k, v in args.items() if k not in names and v is not None}
    return (path, rest, None) if tool.method in ("GET", "DELETE") else (path, {}, rest)


async def call_tool(name: str, args: dict, roles: set[str], send: Send) -> dict:
    tool = BY_NAME.get(name)
    if tool is None or not offered(tool, roles):
        return {"isError": True, "content": [{"type": "text", "text": f"No such tool: {name}"}]}
    try:
        path, query, body = build_request(tool, args or {})
    except ValueError as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    status, data = await send(tool.method, path, query, body)
    text = data if isinstance(data, str) else json.dumps(data, indent=1, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n... cut at {MAX_RESULT_CHARS} characters"
    if status >= 400:
        detail = data.get("detail") if isinstance(data, dict) else text
        return {"isError": True, "content": [{"type": "text", "text": f"HTTP {status}: {detail}"}]}
    return {"content": [{"type": "text", "text": text}]}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


class Session:
    """One MCP connection: finds the token's roles once, then answers messages."""

    def __init__(self, send: Send):
        self.send = send
        self._roles: set[str] | None = None
        self.problem = ""                 # why the token's roles couldn't be read (a bad token, an unreachable stack)

    async def roles(self) -> set[str]:
        if self._roles is None:
            status, data = await self.send("GET", "/api/auth/me", {}, None)
            if status == 200 and isinstance(data, dict):
                self._roles = set(data.get("roles", []))
            else:
                detail = data.get("detail") if isinstance(data, dict) else data
                self.problem = f"LABaPe refused the token (HTTP {status}): {detail}" if status < 500 else str(detail)
                print(f"labape mcp: {self.problem}", file=sys.stderr, flush=True)
                return set()                                  # not cached: the next call tries again
        return self._roles

    async def handle(self, msg: dict) -> dict | None:
        """The response to one JSON-RPC message; None for a notification."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
        id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if "id" not in msg:
            return None                                   # notifications/initialized, cancelled...
        if method == "initialize":
            want = params.get("protocolVersion")
            return {"jsonrpc": "2.0", "id": id_, "result": {
                "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "labape", "version": SERVER_VERSION}, "instructions": INSTRUCTIONS}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": id_, "result": {}}
        if method == "tools/list":
            roles = await self.roles()
            return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [t.listing() for t in TOOLS if offered(t, roles)]}}
        if method == "tools/call":
            if not isinstance(params.get("name"), str):
                return _error(id_, -32602, "tools/call needs a tool name")
            roles = await self.roles()
            if self.problem and not roles:
                return {"jsonrpc": "2.0", "id": id_, "result": {
                    "isError": True, "content": [{"type": "text", "text": self.problem}]}}
            self.problem = ""
            result = await call_tool(params["name"], params.get("arguments") or {}, roles, self.send)
            return {"jsonrpc": "2.0", "id": id_, "result": result}
        return _error(id_, -32601, f"Method not found: {method}")


# --- stdio transport -----------------------------------------------------------------------------

def http_sender(url: str, token: str, ca_file: str = "", verify: bool = True):
    """A `send` that calls LABaPe with urllib, so stdio needs nothing but Python (the MCP bundle relies on it)."""
    import ssl
    import urllib.error
    import urllib.parse
    import urllib.request
    base = url.rstrip("/")
    ctx = ssl.create_default_context(cafile=ca_file or None)
    if not verify:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

    def call(method: str, path: str, params: dict, body: dict | None) -> tuple[int, object]:
        query = "?" + urllib.parse.urlencode(params) if params else ""
        req = urllib.request.Request(
            base + path + query, method=method, data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "labape-mcp"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            return 502, f"Can't reach LABaPe at {url}: {getattr(exc, 'reason', exc)}"
        text = raw.decode("utf-8", errors="replace")
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    async def send(method, path, params, body):
        return await asyncio.get_running_loop().run_in_executor(None, call, method, path, params, body)
    return send


async def serve_stdio(send: Send, stdin=None, stdout=None) -> None:
    """Newline-delimited JSON-RPC on stdin/stdout (the MCP stdio transport)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    session = Session(send)
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, stdin.readline)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = [_error(None, -32700, "Parse error")]
        else:
            batch = msg if isinstance(msg, list) else [msg]
            out = [r for r in [await session.handle(m) for m in batch] if r is not None]
            if not isinstance(msg, list):
                out = out[:1]
        for r in out:
            stdout.write(json.dumps(r) + "\n")
        stdout.flush()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="labape mcp", description="MCP server for LABaPe over stdio")
    ap.add_argument("--url", default=os.environ.get("LABAPE_URL", ""), help="the web interface's address (LABAPE_URL)")
    ap.add_argument("--token-file", default=os.environ.get("LABAPE_TOKEN_FILE", ""),
                    help="a file holding the API token (else LABAPE_TOKEN)")
    ap.add_argument("--ca-file", default=os.environ.get("LABAPE_CA_FILE", ""), help="the stack's platform CA (LABAPE_CA_FILE)")
    ap.add_argument("--insecure", action="store_true", default=os.environ.get("LABAPE_INSECURE", "").lower() in ("1", "true", "yes"),
                    help="don't verify the server's certificate (LABAPE_INSECURE=1)")
    args = ap.parse_args(argv)
    if args.ca_file.startswith("${"):                     # an optional setting the client left unfilled
        args.ca_file = ""
    token = os.environ.get("LABAPE_TOKEN", "")
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as f:
            token = f.read().strip()
    if not args.url or not token:
        print("labape mcp: set LABAPE_URL and LABAPE_TOKEN (or --token-file)", file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")          # Windows would otherwise use the console code page
    asyncio.run(serve_stdio(http_sender(args.url, token, args.ca_file, not args.insecure)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
