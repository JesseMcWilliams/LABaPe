"""Batch X4 (design §59.5): the MCP server, over stdio and over HTTP."""
import asyncio
import io
import json

import pytest

from labape import console_access, mcp
from labape.db import session_factory
from labape.engine import vmexec
from labape.models import Environment, KvmHost

from test_a1_tokens import _admin_session, _bearer, _no_session


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh():
    console_access._calls.clear()


def _fake_send(roles, calls=None, status=200, body=None):
    async def send(method, path, params, json_body):
        if path == "/api/auth/me":
            return 200, {"roles": roles}
        if calls is not None:
            calls.append((method, path, params, json_body))
        return status, body if body is not None else {"ok": True}
    return send


def _rpc(session, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return run(session.handle(msg))


# --- the protocol and the tools ---------------------------------------------------------------------

def test_initialize_ping_and_notifications():
    s = mcp.Session(_fake_send(["console"]))
    r = _rpc(s, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
    assert r["protocolVersion"] == "2025-03-26" and r["serverInfo"]["name"] == "labape" and "tools" in r["capabilities"]
    assert _rpc(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]
    assert _rpc(s, "ping")["result"] == {}
    assert run(s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None
    assert _rpc(s, "nope")["error"]["code"] == -32601
    assert run(s.handle({"id": 1}))["error"]["code"] == -32600


def test_tools_are_offered_by_role():
    names = lambda roles: {t["name"] for t in _rpc(mcp.Session(_fake_send(roles)), "tools/list")["result"]["tools"]}  # noqa: E731
    console = names(["console"])
    assert {"run_command", "open_session", "console_targets"} <= console and "deploy_environment" not in console
    assert "list_environments" not in console                                    # reads need a read role
    viewer = names(["viewer"])
    assert "list_environments" in viewer and "run_command" not in viewer and "deploy_environment" not in viewer
    assert names(["deployer"]) >= {"deploy_environment", "destroy_environment"}
    admin = names(["admin"])
    assert admin == {t.name for t in mcp.TOOLS}
    t = {x["name"]: x for x in _rpc(mcp.Session(_fake_send(["admin"])), "tools/list")["result"]["tools"]}
    assert t["destroy_environment"]["annotations"]["destructiveHint"] and t["list_jobs"]["annotations"]["readOnlyHint"]
    assert t["run_command"]["inputSchema"]["required"] == ["environment_id", "vm", "command"]


def test_a_call_becomes_one_rest_request():
    calls = []
    s = mcp.Session(_fake_send(["admin"], calls, body={"exit_code": 0, "stdout": "hi\n"}))
    r = _rpc(s, "tools/call", {"name": "run_command", "arguments": {"environment_id": 3, "vm": "lin1", "command": "id", "timeout": 5}})
    assert calls == [("POST", "/api/environments/3/vms/lin1/exec", {}, {"command": "id", "timeout": 5})]
    assert "isError" not in r["result"]
    assert json.loads(r["result"]["content"][0]["text"])["stdout"] == "hi\n"
    calls.clear()
    _rpc(s, "tools/call", {"name": "session_output", "arguments": {"session_id": 9, "after": 4, "wait": 2}})
    _rpc(s, "tools/call", {"name": "list_jobs", "arguments": {"state": "failed", "limit": 5}})
    _rpc(s, "tools/call", {"name": "close_session", "arguments": {"session_id": 9}})
    assert calls == [("GET", "/api/console/sessions/9/output", {"after": 4, "wait": 2}, None),
                     ("GET", "/api/jobs", {"state": "failed", "limit": 5}, None),
                     ("DELETE", "/api/console/sessions/9", {}, None)]


def test_bad_calls_and_rest_errors_are_tool_errors():
    s = mcp.Session(_fake_send(["console"], status=403, body={"detail": "Not permitted"}))
    text = lambda r: r["result"]["content"][0]["text"]                           # noqa: E731
    r = _rpc(s, "tools/call", {"name": "deploy_environment", "arguments": {"environment_id": 1}})
    assert r["result"]["isError"] and "No such tool" in text(r)                  # not offered to this token
    r = _rpc(s, "tools/call", {"name": "run_command", "arguments": {"vm": "x"}})
    assert r["result"]["isError"] and "missing argument" in text(r)
    r = _rpc(s, "tools/call", {"name": "run_command", "arguments": {"environment_id": 1, "vm": "x", "command": "id", "bogus": 1}})
    assert r["result"]["isError"] and "unknown argument" in text(r)
    r = _rpc(s, "tools/call", {"name": "run_command", "arguments": {"environment_id": 1, "vm": "x", "command": "id"}})
    assert r["result"]["isError"] and text(r) == "HTTP 403: Not permitted"
    assert _rpc(s, "tools/call", {})["error"]["code"] == -32602


def test_a_token_that_cant_be_read_says_why():
    async def refused(method, path, params, body):
        return 401, {"detail": "Invalid, expired or revoked API token"}
    s = mcp.Session(refused)
    assert _rpc(s, "tools/list")["result"]["tools"] == []
    r = _rpc(s, "tools/call", {"name": "run_command", "arguments": {"environment_id": 1, "vm": "x", "command": "id"}})
    assert r["result"]["isError"] and "HTTP 401" in r["result"]["content"][0]["text"] and "revoked" in r["result"]["content"][0]["text"]

    async def down(method, path, params, body):
        return 502, "Can't reach LABaPe at https://x: connection refused"
    r = _rpc(mcp.Session(down), "tools/call", {"name": "console_targets"})
    assert "Can't reach LABaPe" in r["result"]["content"][0]["text"]


def test_stdio_answers_one_line_per_request():
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
    out = io.StringIO()
    run(mcp.serve_stdio(_fake_send(["console"]), io.StringIO("\n".join(lines) + "\n"), out))
    got = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [g.get("id") for g in got] == [1, None, 2] and got[1]["error"]["code"] == -32700
    assert any(t["name"] == "run_command" for t in got[2]["result"]["tools"])


# --- over HTTP, through the real API -----------------------------------------------------------------

def _env(db, name):
    h = KvmHost(name="k-" + name, libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t")
    db.add(h)
    db.flush()
    e = Environment(name=name, kvm_host_id=h.id, created_by="alice",
                    spec={"host_groups": [{"name": "g", "count": 1, "names": ["lin1"], "os": "debian12"}]})
    db.add(e)
    db.commit()
    return e.id


def _token(client, **body):
    _admin_session(client)
    r = client.post("/api/tokens", json={"name": "ai", "roles": ["console"], "require_account": False, **body})
    assert r.status_code == 200, r.text
    _no_session(client)
    return r.json()["secret"]


def _call(client, secret, method, params=None, id_=1):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=_bearer(secret))
    assert r.status_code == 200, r.text
    return r.json()


def test_mcp_over_http_uses_the_callers_token(client, monkeypatch):
    with session_factory()() as db:
        a, b = _env(db, "x4a"), _env(db, "x4b")
    secret = _token(client, environments=[a])
    assert client.post("/mcp", json={}).status_code == 401
    assert client.get("/mcp").status_code == 405
    assert _call(client, secret, "initialize", {"protocolVersion": "2025-03-26"})["result"]["serverInfo"]["name"] == "labape"
    tools = {t["name"] for t in _call(client, secret, "tools/list")["result"]["tools"]}
    assert "run_command" in tools and "deploy_environment" not in tools
    targets = json.loads(_call(client, secret, "tools/call", {"name": "console_targets"})["result"]["content"][0]["text"])
    assert [e["id"] for e in targets["environments"]] == [a] and targets["environments"][0]["vms"][0]["name"] == "lin1"
    monkeypatch.setattr(vmexec, "run", lambda *a, **k: vmexec.Result(0, "uid=0\n", "", False, False, 0.1))
    ok = _call(client, secret, "tools/call", {"name": "run_command", "arguments": {"environment_id": a, "vm": "lin1", "command": "id"}})
    assert "isError" not in ok["result"] and json.loads(ok["result"]["content"][0]["text"])["stdout"] == "uid=0\n"
    off = _call(client, secret, "tools/call", {"name": "run_command", "arguments": {"environment_id": b, "vm": "lin1", "command": "id"}})
    assert off["result"]["isError"] and "HTTP 404" in off["result"]["content"][0]["text"]       # the token's scope holds
    # notifications only: accepted, no body
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=_bearer(secret))
    assert r.status_code == 202
    # a bad token is the REST API's 401, reported as a tool error
    bad = _call(client, "lpat_nope", "tools/list")
    assert bad["result"]["tools"] == []
