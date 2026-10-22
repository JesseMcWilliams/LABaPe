"""The MCP bundle for Claude Desktop (design §59.5): stdlib-only stdio client, manifest and launcher."""
import asyncio
import json
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from labape import mcp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "helpers"))
import build_mcpb  # noqa: E402


class Fake(BaseHTTPRequestHandler):
    seen: list = []

    def _answer(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        Fake.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.path == "/api/auth/me":
            out, code = {"roles": ["console"]}, 200
        elif self.path.startswith("/api/console/environments"):
            out, code = {"environments": [], "kvm_hosts": [], "account_required": True}, 200
        else:
            out, code = {"detail": "Not permitted"}, 403
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_DELETE = _answer

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    Fake.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_the_urllib_sender_sends_the_token_and_reads_errors(server):
    send = mcp.http_sender(server, "lpat_secret")
    assert asyncio.run(send("GET", "/api/console/environments", {}, None))[0] == 200
    status, body = asyncio.run(send("POST", "/api/x", {"a": 1}, {"k": "v"}))
    assert (status, body) == (403, {"detail": "Not permitted"})
    assert Fake.seen[0][2] == "Bearer lpat_secret" and Fake.seen[1][1] == "/api/x?a=1" and json.loads(Fake.seen[1][3]) == {"k": "v"}
    down = mcp.http_sender("http://127.0.0.1:9", "t")
    status, text = asyncio.run(down("GET", "/", {}, None))
    assert status == 502 and "Can't reach LABaPe" in text


def test_the_manifest_lists_the_code_tools_and_asks_for_the_settings():
    m = build_mcpb.manifest()
    assert m["manifest_version"] == "0.2" and m["server"]["type"] == "python"
    assert [t["name"] for t in m["tools"]] == [t.name for t in mcp.TOOLS]
    assert m["user_config"]["api_token"]["sensitive"] is True
    env = m["server"]["mcp_config"]["env"]
    args = m["server"]["mcp_config"]["args"]
    assert env["LABAPE_TOKEN"] == "${user_config.api_token}" and args == ["-c", build_mcpb.BOOTSTRAP, "${__dirname}"]
    for v in env.values():
        key = v[len("${user_config."):-1]
        assert key in m["user_config"]


def test_the_bundle_runs_standalone_against_a_server(tmp_path, server):
    out = build_mcpb.build(tmp_path / "labape.mcpb")
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["README.txt", "manifest.json", "server/labape_mcp.py", "server/main.py"]
        z.extractall(tmp_path / "unpacked")
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "console_targets"}})]
    r = subprocess.run([sys.executable, "-I", str(tmp_path / "unpacked" / "server" / "main.py")], input="\n".join(lines) + "\n",
                       capture_output=True, text=True, timeout=60, cwd=tmp_path,
                       env={"LABAPE_URL": server, "LABAPE_TOKEN": "lpat_bundle", "LABAPE_CA_FILE": "${user_config.ca_file}",
                            "LABAPE_INSECURE": "false", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    got = [json.loads(x) for x in r.stdout.splitlines()]
    assert r.returncode == 0, r.stderr
    assert [g["id"] for g in got] == [1, 2, 3] and got[0]["result"]["serverInfo"]["name"] == "labape"
    assert {t["name"] for t in got[1]["result"]["tools"]} == {t.name for t in mcp.TOOLS if "console" in t.roles}
    assert "environments" in got[2]["result"]["content"][0]["text"]
    assert all(s[2] == "Bearer lpat_bundle" for s in Fake.seen)


def _session(argv, server, tmp_path, extra_env=None):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "console_targets"}})]
    env = {"LABAPE_URL": server, "LABAPE_TOKEN": "lpat_boot", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
           **(extra_env or {})}
    return subprocess.run(argv, input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)


def test_the_bootstrap_finds_the_extension_where_the_store_version_put_it(tmp_path, server):
    """Claude Desktop from the Store hands the server a path that exists only inside its sandbox."""
    manifest = build_mcpb.manifest()
    boot = manifest["server"]["mcp_config"]["args"][1]
    out = build_mcpb.build(tmp_path / "labape.mcpb")
    name = "local.mcpb.labape.labape"
    store = tmp_path / "local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "Claude Extensions" / name
    with zipfile.ZipFile(out) as z:
        z.extractall(store)
    wrong = tmp_path / "AppData" / "Roaming" / "Claude" / "Claude Extensions" / name           # not on disk
    r = _session([sys.executable, "-I", "-c", boot, str(wrong)], server, tmp_path, {"LOCALAPPDATA": str(tmp_path / "local")})
    got = [json.loads(x) for x in r.stdout.splitlines()]
    assert r.returncode == 0, r.stderr
    assert got[0]["result"]["serverInfo"]["name"] == "labape" and "environments" in got[1]["result"]["content"][0]["text"]
    # the path Desktop gives is used when it exists, and a clear message when the files are nowhere
    r = _session([sys.executable, "-I", "-c", boot, str(store)], server, tmp_path, {"LOCALAPPDATA": str(tmp_path / "nowhere")})
    assert r.returncode == 0 and json.loads(r.stdout.splitlines()[0])["id"] == 1
    r = _session([sys.executable, "-I", "-c", boot, str(wrong)], server, tmp_path, {"LOCALAPPDATA": str(tmp_path / "nowhere")})
    assert r.returncode != 0 and "cannot find the extension files" in r.stderr


def test_the_skill_names_every_mcp_tool_and_role():
    """skills/labape/SKILL.md must stay in step with the MCP server: every tool, and every role a tool needs."""
    from pathlib import Path

    from labape import mcp
    text = (Path(__file__).resolve().parents[2] / "skills" / "labape" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: labape\n") or text.startswith("---\r\nname: labape")
    missing = [t.name for t in mcp.TOOLS if f"`{t.name}`" not in text]
    assert not missing, f"SKILL.md doesn't mention {missing}"
    roles = {r for t in mcp.TOOLS for r in t.roles}
    assert not [r for r in roles if f"`{r}`" not in text], "SKILL.md doesn't name every role the tools use"
