"""Batch X3 (design §59.4): interactive text sessions for console tokens."""
import sys
import time

import pytest

from labape import console_access, console_sessions, console_transcripts
from labape.api import console_text
from labape.db import session_factory
from labape.engine import vmexec
from labape.models import AuditLog, Environment, KvmHost, RemoteSession

from test_a1_tokens import _admin_session, _bearer, _no_session

ECHO = [sys.executable, "-u", "-c",
        "import sys\nfor l in sys.stdin:\n    print('echo:' + l.strip(), flush=True)\n    if l.strip() == 'bye': break"]


@pytest.fixture(autouse=True)
def _fresh():
    console_access._calls.clear()
    for s in list(console_sessions._sessions.values()):
        s.close("test over")
    console_sessions._sessions.clear()
    yield
    for s in list(console_sessions._sessions.values()):
        s.close("test over")
    console_sessions._sessions.clear()


def _open(sid=1, token=1, target="t", cap=1000, seconds=30, idle=30, argv=None):
    return console_sessions.open_session(sid, token, target, argv or ECHO, {}, console_sessions.make_tmp(),
                                         buffer_cap=cap, seconds=seconds, idle_seconds=idle)


def _wait(s, text, after=0, tries=50):
    for _ in range(tries):
        r = s.read(after, 0.2)
        if text in r["data"]:
            return r
    raise AssertionError(f"never saw {text!r}: {r}")


# --- the session ---------------------------------------------------------------------------------

def test_input_comes_back_and_a_long_poll_waits_for_it():
    s = _open()
    s.write("hello\n")
    r = _wait(s, "echo:hello")
    assert r["open"] and r["lost"] == 0 and r["next"] == s.end
    assert s.read(r["next"], 0)["data"] == ""                       # nothing new
    t0 = time.monotonic()
    assert s.read(r["next"], 0.3)["data"] == "" and time.monotonic() - t0 >= 0.25
    s.write("bye\n")
    r2 = _wait(s, "echo:bye", r["next"])
    for _ in range(50):                                             # the program exits after "bye"
        if not s.read(r2["next"], 0.2)["open"]:
            break
    assert "exit 0" in s.read(r2["next"])["reason"]


def test_the_buffer_keeps_the_newest_bytes_and_says_what_was_lost():
    s = _open(cap=50)
    for i in range(10):
        s.write(f"line-number-{i}\n")
    _wait(s, "echo:line-number-9", s.end)
    late = s.read(0)
    assert late["lost"] > 0 and late["next"] == s.end and len(late["data"].encode()) <= 50


def test_one_session_per_token_and_target_and_sweeping():
    s = _open(sid=1, token=7, target="a", seconds=0.2)
    with pytest.raises(console_sessions.SessionError):
        _open(sid=2, token=7, target="a")
    _open(sid=3, token=7, target="b")                               # another target is fine
    _open(sid=4, token=8, target="a")                               # another token is fine
    time.sleep(0.3)
    done = console_sessions.sweep()
    assert s in done and "session length" in s.closed_reason and not s.open
    assert console_sessions.sweep() == []                           # finalized once
    idle = _open(sid=5, token=9, target="c", idle=0.05)
    time.sleep(0.1)
    assert idle in console_sessions.sweep() and idle.closed_reason == "idle"
    with pytest.raises(console_sessions.SessionError):
        idle.write("x\n")


# --- the API ---------------------------------------------------------------------------------------

def _env(db, name):
    h = KvmHost(name="k-" + name, libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t",
                ssh_address="192.0.2.9", ssh_port=22, ssh_user="localadmin01", access_status="ready")
    db.add(h)
    db.flush()
    e = Environment(name=name, kvm_host_id=h.id, created_by="alice",
                    spec={"host_groups": [{"name": "g", "count": 1, "names": ["lin1"], "os": "debian12"}]})
    db.add(e)
    db.commit()
    return e.id, h.id


def _token(client, **body):
    _admin_session(client)
    r = client.post("/api/tokens", json={"name": "ai", "roles": ["console"], "require_account": False, **body})
    assert r.status_code == 200, r.text
    _no_session(client)
    return r.json()["secret"]


def _fake_reach(monkeypatch, windows=False):
    plan = vmexec.Plan({"protocol": "ssh", "address": "10.0.0.5",
                        "params": {"hostname": "10.0.0.5", "port": "22", "username": "labape", "private-key": "KEYTEXT"}},
                       {}, None, windows)
    monkeypatch.setattr(vmexec, "plan", lambda db, env, vm, account="": plan)
    monkeypatch.setattr(vmexec, "_ssh_argv", lambda params, command, tmp, via, tty=False: (ECHO, {}, ["KEYTEXT"]))


def test_open_type_read_and_close(client, monkeypatch):
    _fake_reach(monkeypatch)
    with session_factory()() as db:
        env_id, _ = _env(db, "x3env")
    secret = _token(client, environments=[env_id])
    h = _bearer(secret)
    r = client.post("/api/console/sessions", json={"environment_id": env_id, "vm": "lin1"}, headers=h)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert client.post("/api/console/sessions", json={"environment_id": env_id, "vm": "lin1"}, headers=h).status_code == 409
    assert [x["id"] for x in client.get("/api/console/sessions", headers=h).json()] == [sid]
    assert client.post(f"/api/console/sessions/{sid}/input", json={"data": "uname\n"}, headers=h).json()["bytes"] == 6
    out = {"data": "", "next": 0}
    for _ in range(30):
        out = client.get(f"/api/console/sessions/{sid}/output", params={"after": 0, "wait": 1}, headers=h).json()
        if "echo:uname" in out["data"]:
            break
    assert "echo:uname" in out["data"] and out["open"]
    assert client.delete(f"/api/console/sessions/{sid}", headers=h).json() == {"closed": True}
    assert client.get(f"/api/console/sessions/{sid}/output", headers=h).status_code == 404       # gone
    with session_factory()() as db:
        row = db.get(RemoteSession, sid)
        assert (row.protocol, row.state, row.target) == ("text", "closed", "lin1") and row.error == ""
        assert db.query(AuditLog).filter_by(action="console.session.open").count() == 1
        assert db.query(AuditLog).filter_by(action="console.session.close").count() == 1
        inp = db.query(AuditLog).filter_by(action="console.session.input").one()
        assert inp.detail == {"bytes": 6}                                   # the size, never what was typed


def test_a_session_belongs_to_its_token_and_the_scope_applies(client, monkeypatch):
    _fake_reach(monkeypatch)
    with session_factory()() as db:
        a, _ = _env(db, "x3a")
        b, _ = _env(db, "x3b")
    one, two = _token(client, environments=[a]), _token(client, environments=[a])
    sid = client.post("/api/console/sessions", json={"environment_id": a, "vm": "lin1"}, headers=_bearer(one)).json()["id"]
    assert client.get(f"/api/console/sessions/{sid}/output", headers=_bearer(two)).status_code == 404
    assert client.post("/api/console/sessions", json={"environment_id": b, "vm": "lin1"}, headers=_bearer(one)).status_code == 404
    assert client.post("/api/console/sessions", json={"vm": "lin1"}, headers=_bearer(one)).status_code == 422
    _fake_reach(monkeypatch, windows=True)
    assert client.post("/api/console/sessions", json={"environment_id": a, "vm": "lin1"}, headers=_bearer(two)).status_code == 400


def test_host_shells_need_the_host_console_flag_and_admin(client, monkeypatch):
    with session_factory()() as db:
        _, host_id = _env(db, "x3host")
    monkeypatch.setattr("labape.remote.host_target", lambda h: {"params": {"hostname": "192.0.2.9", "username": "u", "private-key": "K"}})
    monkeypatch.setattr(vmexec, "_ssh_argv", lambda params, command, tmp, via, tty=False: (ECHO, {}, []))
    plain = _token(client, roles=["console"])
    assert client.post("/api/console/sessions", json={"kvm_host_id": host_id}, headers=_bearer(plain)).status_code == 403
    allowed = _token(client, roles=["admin"], host_consoles=True)
    r = client.post("/api/console/sessions", json={"kvm_host_id": host_id}, headers=_bearer(allowed))
    assert r.status_code == 200 and r.json()["target"] == "k-x3host"


def test_the_transcript_is_written_only_when_recording_is_on(client, monkeypatch, tmp_path):
    from labape import appsettings
    _fake_reach(monkeypatch)
    monkeypatch.setattr(console_transcripts, "DIR", tmp_path)
    with session_factory()() as db:
        env_id, _ = _env(db, "x3rec")
        cfg = appsettings.get(db, "remote")
        cfg.record_sessions = True
        appsettings.put(db, "remote", cfg)
    h = _bearer(_token(client, environments=[env_id]))
    sid = client.post("/api/console/sessions", json={"environment_id": env_id, "vm": "lin1"}, headers=h).json()["id"]
    client.post(f"/api/console/sessions/{sid}/input", json={"data": "ping\n"}, headers=h)
    for _ in range(30):
        if "echo:ping" in client.get(f"/api/console/sessions/{sid}/output", params={"wait": 1}, headers=h).json()["data"]:
            break
    client.delete(f"/api/console/sessions/{sid}", headers=h)
    text = (tmp_path / f"session-{sid}.txt").read_text()
    assert text.startswith("# lin1 (shell), opened by alice") and "echo:ping" in text
    with session_factory()() as db:
        assert db.get(RemoteSession, sid).recorded is True
