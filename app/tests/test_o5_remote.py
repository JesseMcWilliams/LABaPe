"""Batch O5 (design §28.5): Guacamole protocol, handshake with a fake guacd, session
permissions, the tunnel, .rdp files, the auditor role, recordings purge."""
import asyncio
import os
import time

import pytest

from labape import remote
from labape.db import session_factory
from labape.engine import maintenance
from labape.models import Environment, RemoteSession
from labape.security import Principal, current_principal

from test_api import _admin_session, _host


def test_encode_and_parse_handle_partial_and_unicode_instructions():
    a = remote.encode("size", "1024", "768")
    b = remote.encode("name", "Ünïcødé; ok")
    assert a == "4.size,4.1024,3.768;"
    whole, rest = remote.complete_prefix(a + b + "5.mou")
    assert whole == a + b and rest == "5.mou"
    instrs, rest = remote.parse(a + b)
    assert instrs == [["size", "1024", "768"], ["name", "Ünïcødé; ok"]] and rest == ""
    assert remote.encode("", "abc") == "0.,3.abc;"
    with pytest.raises(remote.RemoteError):
        remote.parse("x.size;")


def test_handshake_with_a_fake_guacd():
    got = {}

    async def fake_guacd(reader, writer):
        buf = ""
        while ";" not in buf:
            buf += (await reader.read(1024)).decode()
        got["select"] = remote.parse(buf)[0][0]
        writer.write(remote.encode("args", "VERSION_1_5_0", "hostname", "port", "username", "password").encode())
        await writer.drain()
        buf = ""
        while "7.connect" not in buf or not buf.endswith(";"):
            buf += (await reader.read(4096)).decode()
        got["instrs"] = remote.parse(buf)[0]
        writer.write(remote.encode("ready", "$abc").encode() + remote.encode("sync", "1").encode())
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    async def go():
        server = await asyncio.start_server(fake_guacd, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        target = {"protocol": "rdp", "params": {"hostname": "10.0.0.5", "port": "3389", "username": "Administrator",
                                                "password": "ThisIsMy_FAKE_Password6!"}}
        reader, writer, leftover = await remote.open_guacd(target, 1024, 768, 96, ("127.0.0.1", port))
        writer.close()
        server.close()
        return leftover

    leftover = asyncio.run(go())
    assert got["select"] == ["select", "rdp"]
    connect = [i for i in got["instrs"] if i[0] == "connect"][0]
    assert connect == ["connect", "VERSION_1_5_0", "10.0.0.5", "3389", "Administrator", "ThisIsMy_FAKE_Password6!"]
    assert ["size", "1024", "768", "96"] in got["instrs"]
    assert leftover == remote.encode("sync", "1")


INV = """all:
  hosts:
    win1: {ansible_host: 172.21.50.9, ansible_user: Administrator, ansible_password: ThisIsMy_FAKE_Password6!}
    dc1: {ansible_host: 172.21.50.8, ansible_user: "LAB\\\\Administrator", ansible_password: x}
    lin1: {ansible_host: 172.21.50.10, ansible_user: labape, ansible_ssh_private_key_file: /x}
"""


def _env(client):
    _host(client)
    groups = [{"name": "win", "os": "windows_server_2022", "roles": ["windows_server"], "image_source": "packer_template",
               "template": "w", "names": ["win1"]},
              {"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"], "image_source": "packer_template",
               "template": "w", "names": ["dc1"]},
              {"name": "lin", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template",
               "template": "r", "names": ["lin1"]}]
    r = client.post("/api/environments", json={"name": "remenv", "kvm_host_id": 1, "host_groups": groups})
    assert r.status_code == 201, r.text
    eid = r.json()["environment"]["id"]
    from labape.engine import engine_secrets
    from labape.security import put_secret
    with session_factory()() as db:
        db.get(Environment, eid).status = "deployed"
        put_secret(db, f"environment/{eid}/inventory", INV)
        put_secret(db, engine_secrets.SSH_KEY, "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n")
        db.commit()
    return eid


def test_targets_sessions_rdp_file_and_permissions(client):
    _admin_session(client)
    eid = _env(client)
    with session_factory()() as db:
        env = db.get(Environment, eid)
        win = remote.vm_target(db, env, "win1")
        dc = remote.vm_target(db, env, "dc1")
        lin = remote.vm_target(db, env, "lin1")
    assert win["protocol"] == "rdp" and win["params"]["password"] == "ThisIsMy_FAKE_Password6!"
    assert dc["params"]["domain"] == "LAB" and dc["params"]["username"] == "Administrator"
    assert lin["protocol"] == "ssh" and "OPENSSH PRIVATE KEY" in lin["params"]["private-key"]

    r = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "rdp", "environment_id": eid, "vm": "win1"})
    assert r.status_code == 200 and len(r.json()["token"]) > 30
    body = r.json()
    assert "Password6" not in str(body)
    with session_factory()() as db:
        s = db.get(RemoteSession, body["id"])
        assert s.token_hash == remote.token_hash(body["token"]) and s.address == "172.21.50.9"
    bad = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "rdp", "environment_id": eid, "vm": "lin1"})
    assert bad.status_code == 422 and "SSH" in bad.json()["errors"][0]["message"]
    rdp = client.get(f"/api/remote/rdp?environment_id={eid}&vm=dc1")
    assert rdp.status_code == 200 and "full address:s:172.21.50.8" in rdp.text and "LAB\\Administrator" in rdp.text
    assert "password" not in rdp.text.lower().replace("prompt for credentials", "")
    assert client.get(f"/api/remote/rdp?environment_id={eid}&vm=lin1").status_code == 422
    assert client.get("/api/remote/sessions").json()[0]["target"] == "win1"
    assert client.get("/api/admin/audit?action=remote.session.create").json()

    # A viewer who can see nothing, and an auditor (reads the session log and the audit log, opens nothing).
    for roles, sessions, audit_log, create in (({"viewer"}, 403, 403, 404), ({"auditor"}, 200, 200, 404)):
        client.app.dependency_overrides[current_principal] = lambda roles=roles: Principal(
            user_id=99, username="someone", display_name="", roles=roles)
        assert client.get("/api/remote/sessions").status_code == sessions
        assert client.get("/api/admin/audit").status_code == audit_log
        assert client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "rdp",
                                                         "environment_id": eid, "vm": "win1"}).status_code == create
        assert client.post("/api/remote/sessions", json={"target_type": "host", "protocol": "ssh",
                                                         "kvm_host_id": 1}).status_code == 403
    client.app.dependency_overrides.clear()


def test_tunnel_refuses_bad_origin_and_reused_tokens(client):
    _admin_session(client)
    eid = _env(client)
    body = client.post("/api/remote/sessions", json={"target_type": "vm", "protocol": "ssh", "environment_id": eid,
                                                     "vm": "lin1"}).json()
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/remote/tunnel?token={body['token']}",
                                      headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/remote/tunnel?token=wrong", headers={"origin": "https://labape.local"}) as ws:
            ws.receive_text()


def test_recordings_purge(tmp_root):
    d = tmp_root / "recordings"
    d.mkdir(exist_ok=True)
    old, new = d / "session-1", d / "session-2"
    old.write_text("x")
    new.write_text("y")
    os.utime(old, (time.time() - 100 * 86400,) * 2)
    assert maintenance.purge_recordings(90, d) == 1
    assert not old.exists() and new.exists()


def test_relay_reports_a_guacd_error_as_a_failure():
    class FakeWs:
        def __init__(self):
            self.sent = []

        async def send_text(self, t):
            self.sent.append(t)

        async def receive_text(self):
            await asyncio.sleep(10)

    async def go():
        async def fake_guacd(reader, writer):
            writer.write(remote.encode("error", "Server refused connection", "519").encode())
            await writer.drain()
            await asyncio.sleep(1)
        server = await asyncio.start_server(fake_guacd, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        ws = FakeWs()
        why = await remote.relay(ws, reader, writer, "", 600)
        server.close()
        return why, ws.sent

    why, sent = asyncio.run(go())
    assert why.startswith("failed: Server refused") and any("error" in t for t in sent)
