"""A VM that was deploying when the VM key rotated keeps an earlier key: it is brought back onto the current one,
and a rotation waits for deploys (2026-10-23, found live with zz-t1)."""
import json
import subprocess

from labape.db import session_factory
from labape.engine import engine_secrets as es
from labape.engine import vmadmin
from labape.models import Environment
from labape.security import put_secret


def _keys(db):
    put_secret(db, es.SSH_KEY, "CURRENT-PRIVATE\n")
    put_secret(db, es.SSH_PUB, "ssh-ed25519 AAAAcurrent labape-bootstrap-new\n")
    put_secret(db, es.SSH_HISTORY, json.dumps([{"stamp": "1", "private": "OLD-PRIVATE\n", "public": "ssh-ed25519 AAAAold"}]))


class FakeSsh:
    ran: list[str] = []

    def __init__(self, address, user, key_text):
        self.key = key_text.strip()

    def run(self, command, input=None, timeout=60):
        FakeSsh.ran.append(f"{self.key}: {command}")
        return subprocess.CompletedProcess([], 0, b"", b"")

    def close(self):
        pass


def test_current_key_is_used_when_it_works(client, monkeypatch):
    monkeypatch.setattr(vmadmin, "_login", lambda a, u, k: (True, False))
    with session_factory()() as db:
        _keys(db)
        assert vmadmin.vm_key_text(db, "10.0.0.5", "labape").strip() == "CURRENT-PRIVATE"


def test_an_unreachable_vm_is_not_retried_with_old_keys(client, monkeypatch):
    seen = []
    monkeypatch.setattr(vmadmin, "_login", lambda a, u, k: (seen.append(k.strip()) or (False, False)))
    with session_factory()() as db:
        _keys(db)
        assert vmadmin.vm_key_text(db, "10.0.0.5", "labape").strip() == "CURRENT-PRIVATE"
    assert seen == ["CURRENT-PRIVATE"]


def test_a_vm_on_an_earlier_key_gets_the_current_one(client, monkeypatch):
    state = {"current_added": False}

    def login(a, u, k):
        k = k.strip()
        if k == "OLD-PRIVATE":
            return True, False
        return state["current_added"], not state["current_added"]

    class Ssh(FakeSsh):
        def run(self, command, input=None, timeout=60):
            if "authorized_keys" in command and self.key == "OLD-PRIVATE":
                state["current_added"] = True
            return super().run(command, input, timeout)

    FakeSsh.ran = []
    monkeypatch.setattr(vmadmin, "_login", login)
    monkeypatch.setattr(vmadmin, "VmSsh", Ssh)
    with session_factory()() as db:
        _keys(db)
        assert vmadmin.vm_key_text(db, "10.0.0.5", "labape").strip() == "CURRENT-PRIVATE"
    assert len(FakeSsh.ran) == 1 and "AAAAcurrent" in FakeSsh.ran[0] and FakeSsh.ran[0].startswith("OLD-PRIVATE:")


def test_the_earlier_key_is_used_when_adding_the_current_one_fails(client, monkeypatch):
    monkeypatch.setattr(vmadmin, "_login", lambda a, u, k: (k.strip() == "OLD-PRIVATE", k.strip() != "OLD-PRIVATE"))

    class Ssh(FakeSsh):
        def run(self, command, input=None, timeout=60):
            return subprocess.CompletedProcess([], 1, b"", b"read-only")

    monkeypatch.setattr(vmadmin, "VmSsh", Ssh)
    with session_factory()() as db:
        _keys(db)
        assert vmadmin.vm_key_text(db, "10.0.0.5", "labape").strip() == "OLD-PRIVATE"


class Log:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


def test_the_rotation_waits_for_a_deploy_to_finish(client, monkeypatch):
    sleeps = []
    with session_factory()() as db:
        env = Environment(name="zz-wait", kvm_host_id=1, created_by="admin", status="deploying", spec={"host_groups": []})
        db.add(env)
        db.commit()

        def sleep(n):
            sleeps.append(n)
            e = db.get(Environment, env.id)
            e.status = "deployed"
            db.commit()

        monkeypatch.setattr(es.time, "sleep", sleep)
        log = Log()
        es._wait_for_deploys(db, log)
        assert sleeps == [15] and "waiting for zz-wait" in log.text


def test_the_rotation_goes_on_after_the_longest_wait(client, monkeypatch):
    with session_factory()() as db:
        db.add(Environment(name="zz-stuck", kvm_host_id=1, created_by="admin", status="deploying", spec={"host_groups": []}))
        db.commit()
        ticks = iter([0, 0, 10_000])
        monkeypatch.setattr(es.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(es.time, "sleep", lambda n: None)
        log = Log()
        es._wait_for_deploys(db, log, minutes=1)
        assert "rotating without them" in log.text and "zz-stuck" in log.text
