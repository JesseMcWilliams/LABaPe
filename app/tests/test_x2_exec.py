"""Batch X2 (design §59.2): run a command on a VM through the API."""
import sys

import pytest

from labape import console_access, console_transcripts, tokens
from labape.db import session_factory
from labape.engine import vmexec
from labape.models import AuditLog, Environment, KvmHost, RemoteSession, User
from labape.security import current_principal

from test_a1_tokens import _admin_session, _bearer, _no_session


@pytest.fixture(autouse=True)
def _fresh_limits():
    console_access._calls.clear()          # token ids repeat between tests


def _env(db, name="x2env", vms=("lin1",)):
    h = KvmHost(name="k-" + name, libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t")
    db.add(h)
    db.flush()
    e = Environment(name=name, kvm_host_id=h.id, created_by="alice",
                    spec={"host_groups": [{"name": "g", "count": len(vms), "names": list(vms), "os": "debian12"}]})
    db.add(e)
    db.commit()
    return e.id


def _token(client, **body):
    _admin_session(client)
    r = client.post("/api/tokens", json={"name": "ai", "roles": ["console"], **body})
    assert r.status_code == 200, r.text
    _no_session(client)
    return r.json()["secret"]


# --- the runner ------------------------------------------------------------------------------

def test_a_process_returns_its_exit_code_and_output():
    r = vmexec.run_process([sys.executable, "-c", "import sys;print('out');sys.stderr.write('err');sys.exit(3)"],
                           timeout=20, cap=1000)
    assert (r.exit_code, r.stdout.strip(), r.stderr.strip(), r.truncated, r.timed_out) == (3, "out", "err", False, False)


def test_output_past_the_cap_stops_the_process():
    r = vmexec.run_process([sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x'*1000); sys.stdout.flush()"],
                           timeout=20, cap=5000)
    assert r.truncated and r.exit_code is None and 5000 <= len(r.stdout) <= 5000 and not r.timed_out


def test_a_timeout_kills_it():
    r = vmexec.run_process([sys.executable, "-c", "import time;time.sleep(30)"], timeout=1, cap=1000)
    assert r.timed_out and r.exit_code is None and r.duration < 10


def test_this_calls_credentials_are_masked():
    assert vmexec.mask("pw=Hunter2!x and Hunter2!x", ["Hunter2!x", "", "ab"]) == "pw=*** and ***"


def test_ssh_uses_the_key_or_askpass_and_the_host_for_remote_vms(tmp_path):
    key = {"hostname": "10.0.0.5", "port": "22", "username": "labape", "private-key": "KEYTEXT"}
    argv, extra, secrets = vmexec._ssh_argv(key, "id", tmp_path, None)
    assert argv[-2:] == ["labape@10.0.0.5", "id"] and "-i" in argv and "BatchMode=yes" in argv
    assert (tmp_path / "key").read_text().strip() == "KEYTEXT" and extra == {} and secrets == ["KEYTEXT"]
    pw = {"hostname": "10.0.0.5", "port": "22", "username": "bob", "password": "Pw-1234"}
    argv, extra, secrets = vmexec._ssh_argv(pw, "id", tmp_path, None)
    assert "Pw-1234" not in argv and extra["LABAPE_EXEC_PW"] == "Pw-1234" and extra["SSH_ASKPASS_REQUIRE"] == "force"
    assert "PubkeyAuthentication=no" in argv and secrets == ["Pw-1234"]
    via = KvmHost(name="h", ssh_address="192.0.2.9", ssh_port=22, ssh_user="localadmin01", access_status="ready")
    argv, _, _ = vmexec._ssh_argv(key, "id", tmp_path, via)
    proxy = next(a for a in argv if a.startswith("ProxyCommand="))
    assert "-W %h:%p" in proxy and "localadmin01@192.0.2.9" in proxy


# --- the endpoint ----------------------------------------------------------------------------

def _fake(monkeypatch, result=None, seen=None):
    def run(db, env, vm, command, *, account="", timeout=60, cap=0):
        if seen is not None:
            seen.update(vm=vm, command=command, account=account, timeout=timeout, cap=cap)
        return result or vmexec.Result(0, "hello\n", "", False, False, 0.25)
    monkeypatch.setattr(vmexec, "run", run)


def test_a_domain_login_goes_over_ntlm_a_local_one_keeps_the_inventorys(monkeypatch):
    seen = []

    class Resp:
        status_code, std_out, std_err = 0, b"ok", b""

    class FakeSession:
        def __init__(self, url, auth, transport, **kw):
            seen.append((auth[0], transport))

        def run_ps(self, command):
            return Resp()

    monkeypatch.setitem(sys.modules, "winrm", type(sys)("winrm"))
    sys.modules["winrm"].Session = FakeSession
    monkeypatch.setattr(vmexec, "_winrm_pin", lambda *a: "00" * 32)       # the certificate pin (question 198) has its own tests
    monkeypatch.setattr(vmexec, "_pin_winrm", lambda *a: None)
    host = {"ansible_winrm_transport": "basic"}
    vmexec._run_windows(host, {"hostname": "h", "username": "jesse", "domain": "LABAPE", "password": "p"}, "x", 5, 100)
    vmexec._run_windows(host, {"hostname": "h", "username": "jesse@labape.test", "password": "p"}, "x", 5, 100)
    vmexec._run_windows(host, {"hostname": "h", "username": "Administrator", "domain": "", "password": "p"}, "x", 5, 100)
    assert seen == [("LABAPE\\jesse", "ntlm"), ("jesse@labape.test", "ntlm"), ("Administrator", "basic")]


def test_exec_runs_audits_and_logs_the_session(client, monkeypatch):
    with session_factory()() as db:
        env_id = _env(db)
    secret = _token(client, environments=[env_id], require_account=False, max_output_kb=8)
    seen = {}
    _fake(monkeypatch, seen=seen)
    r = client.post(f"/api/environments/{env_id}/vms/lin1/exec", json={"command": "echo hello", "timeout": 3600},
                    headers=_bearer(secret))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["exit_code"] == 0 and j["stdout"] == "hello\n" and j["timed_out"] is False and j["session"]
    assert seen["cap"] == 8 * 1024 and seen["timeout"] == 30 * 60 and seen["vm"] == "lin1"      # the token's limits apply
    with session_factory()() as db:
        s = db.get(RemoteSession, j["session"])
        assert (s.protocol, s.state, s.target, s.username) == ("exec", "closed", "lin1", "alice")
        a = db.query(AuditLog).filter_by(action="console.exec").one()
        assert a.detail["command"] == "echo hello" and a.detail["exit_code"] == 0 and a.detail["token"] == "ai"
        assert "stdout" not in a.detail and "hello\\n" not in str(a.detail)   # output is never in the audit event


def test_exec_refuses_what_the_token_cant_reach(client, monkeypatch):
    with session_factory()() as db:
        a, b = _env(db, "x2a"), _env(db, "x2b")
    secret = _token(client, environments=[a])
    _fake(monkeypatch)
    url = lambda e, vm="lin1": f"/api/environments/{e}/vms/{vm}/exec"      # noqa: E731
    assert client.post(url(b), json={"command": "id", "account": "x"}, headers=_bearer(secret)).status_code == 404
    assert client.post(url(a, "nope"), json={"command": "id", "account": "x"}, headers=_bearer(secret)).status_code == 404
    r = client.post(url(a), json={"command": "id"}, headers=_bearer(secret))
    assert r.status_code == 400 and "account" in r.json()["detail"]       # the token requires one
    assert client.post(url(a), json={"command": "id", "account": "x"}, headers=_bearer(secret)).status_code == 200
    # a browser session and a token without the role are refused
    _admin_session(client)
    assert client.post(url(a), json={"command": "id", "account": "x"}).status_code == 403
    _no_session(client)
    plain = client.post("/api/tokens", json={"name": "p", "roles": ["admin"]}, headers=None)
    assert plain.status_code in (401, 403)


def test_exec_is_rate_limited_and_reports_failures(client, monkeypatch):
    with session_factory()() as db:
        env_id = _env(db, "x2rate")
    secret = _token(client, environments=[env_id], require_account=False, rate_per_minute=2)
    _fake(monkeypatch)
    url = f"/api/environments/{env_id}/vms/lin1/exec"
    assert [client.post(url, json={"command": "id"}, headers=_bearer(secret)).status_code for _ in range(3)] == [200, 200, 429]

    def boom(*a, **k):
        raise vmexec.ExecError("lin1 has no address yet")
    secret2 = _token(client, environments=[env_id], require_account=False)
    monkeypatch.setattr(vmexec, "run", boom)
    r = client.post(url, json={"command": "id"}, headers=_bearer(secret2))
    assert r.status_code == 400 and "no address" in r.json()["detail"]
    with session_factory()() as db:
        assert db.query(RemoteSession).filter_by(protocol="exec", state="failed").count() == 1
        assert db.query(AuditLog).filter_by(action="console.exec", outcome="failed").count() == 1


def test_a_transcript_is_kept_only_when_recording_is_on(client, monkeypatch, tmp_path):
    from labape import appsettings
    monkeypatch.setattr(console_transcripts, "DIR", tmp_path)
    with session_factory()() as db:
        env_id = _env(db, "x2rec")
    secret = _token(client, environments=[env_id], require_account=False)
    _fake(monkeypatch)
    url = f"/api/environments/{env_id}/vms/lin1/exec"
    sid = client.post(url, json={"command": "echo hello"}, headers=_bearer(secret)).json()["session"]
    assert not (tmp_path / f"session-{sid}.txt").exists()
    with session_factory()() as db:
        cfg = appsettings.get(db, "remote")
        cfg.record_sessions = True
        appsettings.put(db, "remote", cfg)
    sid = client.post(url, json={"command": "echo hello"}, headers=_bearer(secret)).json()["session"]
    text = (tmp_path / f"session-{sid}.txt").read_text()
    assert text.startswith("$ echo hello\nhello\n") and "[exit 0]" in text
    with session_factory()() as db:
        assert db.get(RemoteSession, sid).recorded is True
