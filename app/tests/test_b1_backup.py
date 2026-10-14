"""Batch B1 (design §32): the sealed full-stack backup, its schedule and its API."""
import datetime as dt
import io
import json
import os

import pytest

from labape import appsettings, seal, update
from labape.db import session_factory

from test_api import _admin_session


def _roundtrip(data: bytes, key: bytes) -> bytes:
    sealed = io.BytesIO()
    assert seal.seal(io.BytesIO(data), sealed, key) == len(data)
    out = io.BytesIO()
    seal.unseal(io.BytesIO(sealed.getvalue()), out, key)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, seal.CHUNK - 1, seal.CHUNK, seal.CHUNK * 2 + 17])
def test_seal_round_trips(size):
    key = seal.parse_key(seal.new_key())
    data = os.urandom(size)
    assert _roundtrip(data, key) == data


def test_seal_refuses_a_wrong_key_and_damage():
    key = seal.parse_key(seal.new_key())
    data = os.urandom(seal.CHUNK * 2 + 5)
    sealed = io.BytesIO()
    seal.seal(io.BytesIO(data), sealed, key)
    blob = sealed.getvalue()
    with pytest.raises(seal.SealError, match="wrong backup key"):
        seal.unseal(io.BytesIO(blob), io.BytesIO(), seal.parse_key(seal.new_key()))
    flipped = bytearray(blob)
    flipped[-10] ^= 1
    with pytest.raises(seal.SealError, match="damaged"):
        seal.unseal(io.BytesIO(bytes(flipped)), io.BytesIO(), key)
    # Cut after a whole chunk: the last one is missing, so it isn't a complete stack.
    first = len(seal.MAGIC) + 24 + 4 + seal.CHUNK + 16
    with pytest.raises(seal.SealError, match="cut short"):
        seal.unseal(io.BytesIO(blob[:first]), io.BytesIO(), key)
    with pytest.raises(seal.SealError, match="after the end"):
        seal.unseal(io.BytesIO(blob + b"x"), io.BytesIO(), key)
    with pytest.raises(seal.SealError, match="not a sealed"):
        seal.unseal(io.BytesIO(b"PK\x03\x04"), io.BytesIO(), key)


def test_keys_are_written_in_groups_and_parse_back():
    k = seal.new_key()
    assert len(k.replace("-", "")) == 52 and k.count("-") == 12
    assert seal.parse_key(k) == seal.parse_key(k.lower().replace("-", " "))
    with pytest.raises(seal.SealError):
        seal.parse_key("ABCD-EFGH")


def test_the_cli_seals_stdin(tmp_path, monkeypatch, capsysbinary):
    from labape import cli
    kf = tmp_path / "k"
    kf.write_text(seal.new_key() + "\n")
    src = tmp_path / "in"
    src.write_bytes(b"stack" * 1000)

    class Std:
        def __init__(self, b):
            self.buffer = b
    sealed = io.BytesIO()
    monkeypatch.setattr("sys.stdin", Std(io.BytesIO(src.read_bytes())))
    monkeypatch.setattr("sys.stdout", Std(sealed))
    assert cli.main(["seal", "--key-file", str(kf)]) == 0
    out = io.BytesIO()
    monkeypatch.setattr("sys.stdin", Std(io.BytesIO(sealed.getvalue())))
    monkeypatch.setattr("sys.stdout", Std(out))
    assert cli.main(["seal", "--open", "--key-file", str(kf)]) == 0
    assert out.getvalue() == src.read_bytes()


def _agent(tmp_path, monkeypatch):
    d = tmp_path / "update"
    d.mkdir()
    (d / "agent.json").write_text(json.dumps({"user": "labape", "home": "/home/labape"}))
    monkeypatch.setattr(update, "folder", lambda: d)
    return d


def _archive(d, name, created, ok=True):
    (d / name).write_bytes(b"sealed")
    (d / f"{name}.json").write_text(json.dumps({"created_at": created, "version": "v0.2.0", "file_count": 35,
                                                "check": {"ok": ok}, "key_file": "/home/labape/.config/labape/backup.key",
                                                "service_user": "labape"}))


def test_the_daily_request_goes_to_the_agent_once(client, tmp_path, monkeypatch):
    from labape.engine import stackbackup
    _admin_session(client)
    d = _agent(tmp_path, monkeypatch)
    now = dt.datetime(2026, 10, 14, 2, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        appsettings.put(db, "stack_backup", appsettings.StackBackupSettings(directory=str(tmp_path / "b"),
                                                                            include_syslog=False))
        assert stackbackup.schedule(db, now) is False                         # before 03:00
        assert stackbackup.schedule(db, now + dt.timedelta(hours=2)) is True
        req = json.loads((d / "request.json").read_text())
        assert req["action"] == "full_backup" and req["dest"] == str(tmp_path / "b") and req["syslog"] is False
        (d / "request.json").unlink()
        assert stackbackup.schedule(db, now + dt.timedelta(hours=3)) is False  # already asked today
        # Tomorrow, while the agent is busy with something else: asked on a later pass.
        (d / "status.json").write_text(json.dumps({"state": "running", "action": "update"}))
        assert stackbackup.schedule(db, now + dt.timedelta(days=1, hours=2)) is False
        (d / "status.json").write_text(json.dumps({"state": "succeeded", "action": "update"}))
        assert stackbackup.schedule(db, now + dt.timedelta(days=1, hours=2, minutes=1)) is True


def test_stack_backup_api(client, tmp_path, monkeypatch):
    _admin_session(client)
    d = _agent(tmp_path, monkeypatch)
    b = tmp_path / "b"
    b.mkdir()
    _archive(b, "labape-stack-oz-20261013-030000.tar.sealed", "2026-10-13T03:00:00+00:00")
    _archive(b, "labape-stack-oz-20261014-030000.tar.sealed", "2026-10-14T03:00:00+00:00")
    (b / "labape-stack-oz-20261014-040000.tar.sealed.part").write_bytes(b"x")       # being written: not listed
    assert client.put("/api/admin/settings/stack_backup", json={"directory": str(b)}).status_code == 200
    assert client.put("/api/admin/settings/stack_backup", json={"directory": "rel/path"}).status_code == 422
    r = client.get("/api/admin/backups/stack").json()
    assert [a["archive"] for a in r["archives"]] == ["labape-stack-oz-20261014-030000.tar.sealed",
                                                     "labape-stack-oz-20261013-030000.tar.sealed"]
    assert r["key_saved"] is None and r["key_file"].endswith("backup.key") and r["agent"]["installed"]
    assert client.post("/api/admin/backups/stack").status_code == 200
    assert json.loads((d / "request.json").read_text())["action"] == "full_backup"
    assert client.post("/api/admin/backups/stack").status_code == 409                     # one at a time
    assert client.post("/api/admin/backups/stack/key-saved").json()["by"]
    assert client.get("/api/admin/backups/stack").json()["key_saved"]["by"]
    got = client.get("/api/admin/backups/stack/labape-stack-oz-20261014-030000.tar.sealed")
    assert got.status_code == 200 and got.content == b"sealed"
    assert client.get("/api/admin/backups/stack/..%2Fsecret").status_code == 404
    (d / "agent.json").unlink()
    (d / "request.json").unlink()
    assert client.post("/api/admin/backups/stack").status_code == 409                     # no agent


def test_monitoring_watches_the_full_backup(client, tmp_path, monkeypatch):
    from labape.engine import stackmon
    from labape.models import KvmHost, StackStatus
    from test_api import _host
    _admin_session(client)
    _host(client)
    now = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        h.role = "both"
        db.merge(StackStatus(kvm_host_id=1, data={"self": {"stack_backup": {
            "enabled": True, "newest": (now - dt.timedelta(hours=2)).isoformat(), "archive": "a", "ok": False}}}))
        db.commit()
        msgs = {v[2]: v[1] for v in stackmon.evaluate(db, now).values()}
        assert msgs == {"kvm1: the latest full stack backup (a) failed its check": "error"}
        msgs = {v[2] for v in stackmon.evaluate(db, now + dt.timedelta(days=2)).values()}
        assert msgs == {"kvm1: no full stack backup in the last 26 hours"}
        # The sidecars feed the self check.
        b = tmp_path / "b"
        b.mkdir()
        _archive(b, "labape-stack-oz-20261014-030000.tar.sealed", "2026-10-14T03:00:00+00:00")
        appsettings.put(db, "stack_backup", appsettings.StackBackupSettings(directory=str(b)))
        sb = stackmon.self_checks(db)["stack_backup"]
        assert sb["ok"] and sb["archive"].endswith(".tar.sealed") and sb["newest"].startswith("2026-10-14")
