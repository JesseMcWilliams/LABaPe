"""Batch R2 (design §62.1): rotate every overdue secret now, in order, stopping at the first failure."""
import datetime as dt
import json

from labape.config import get_settings
from labape.db import session_factory
from labape.engine import rotate_overdue, rotation
from labape.models import Job, SecretPolicy, utcnow

from test_api import _admin_session

THREE = ("builtin_data_key", "master_key", "postgres_password")           # one worker rotation, two through the agent


def _setup(monkeypatch, tmp_root, late_hours=72):
    d = tmp_root / "r2-update"
    d.mkdir(exist_ok=True)
    for f in d.iterdir():
        f.unlink()
    (d / "agent.json").write_text("{}")
    monkeypatch.setattr(get_settings(), "update_dir", d)
    with session_factory()() as db:
        rotation.ensure_policies(db)
        for p in db.query(SecretPolicy):
            p.enabled = p.kind in THREE
            p.status = "ok"
            p.next_due_at = utcnow() - dt.timedelta(hours=late_hours)
        db.commit()
    return d


def _agent_done(d, ok=True, message="rotated"):
    req = json.loads((d / "request.json").read_text())
    (d / "request.json").unlink()
    (d / "status.json").write_text(json.dumps({"id": req["id"], "action": "rotate", "state": "succeeded" if ok else "failed",
                                               "message": message}))
    with session_factory()() as db:
        rotation.reconcile_agent(db)
    return req


def test_the_plan_lists_overdue_secrets_in_a_fixed_order(client, monkeypatch, tmp_root):
    _admin_session(client)
    _setup(monkeypatch, tmp_root)
    j = client.get("/api/admin/secrets/rotate-overdue").json()
    assert [x["label"].split(" (")[0] for x in j["plan"]] == ["Built-in secret store: data key", "PostgreSQL password",
                                                              "Master key"]
    assert [x["restart"] for x in j["plan"]] == [False, True, True] and j["queue"] is None
    assert all(x["late_hours"] >= 71 for x in j["plan"])
    with session_factory()() as db:                                   # a rotation less than an hour late is not overdue
        db.query(SecretPolicy).filter_by(kind="master_key").one().next_due_at = utcnow() - dt.timedelta(minutes=30)
        db.commit()
    assert len(client.get("/api/admin/secrets/rotate-overdue").json()["plan"]) == 2


def test_starting_needs_the_restart_to_be_acknowledged(client, monkeypatch, tmp_root):
    _admin_session(client)
    _setup(monkeypatch, tmp_root)
    r = client.post("/api/admin/secrets/rotate-overdue", json={})
    assert r.status_code == 422 and "stops services" in r.json()["errors"][0]["message"]
    assert "PostgreSQL password" in r.json()["errors"][0]["message"]
    assert client.get("/api/admin/secrets/rotate-overdue").json()["queue"] is None          # nothing started
    r = client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True})
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["total"] == 3
    assert client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True}).status_code == 409
    assert client.delete("/api/admin/secrets/rotate-overdue").json()["state"] == "cancelled"
    with session_factory()() as db:
        rotate_overdue.advance(db)                                     # a cancelled queue does nothing
        assert db.query(Job).filter_by(type="secret.rotate").count() == 0


def test_the_queue_runs_one_at_a_time_and_finishes(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _setup(monkeypatch, tmp_root)
    client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True})
    with session_factory()() as db:
        rotate_overdue.advance(db)                                     # 1: the worker's own rotation becomes a job
        job = db.query(Job).filter_by(type="secret.rotate").one()
        assert db.get(SecretPolicy, job.params["policy_id"]).kind == "builtin_data_key"
        rotate_overdue.advance(db)                                     # the job has not run: wait
        assert db.query(Job).count() == 1 and not (d / "request.json").exists()
        job.state = "succeeded"
        db.commit()
        rotate_overdue.advance(db)                                     # 2: PostgreSQL, through the agent
    assert json.loads((d / "request.json").read_text())["target"] == "postgres_password"
    with session_factory()() as db:
        rotate_overdue.advance(db)                                     # the agent is working: wait
        assert rotate_overdue.status(db)["current"].startswith("PostgreSQL")
    _agent_done(d)
    with session_factory()() as db:
        rotate_overdue.advance(db)                                     # 3: the master key last
    assert json.loads((d / "request.json").read_text())["target"] == "master_key"
    _agent_done(d)
    with session_factory()() as db:
        rotate_overdue.advance(db)
        st = rotate_overdue.status(db)
        assert st["state"] == "done" and len(st["done"]) == 3 and st["left"] == []
        rotate_overdue.advance(db)                                     # nothing more happens
    assert not (d / "request.json").exists()


def test_it_stops_at_the_first_failure(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _setup(monkeypatch, tmp_root)
    client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True})
    with session_factory()() as db:
        rotate_overdue.advance(db)
        db.query(Job).one().state = "succeeded"
        db.commit()
        rotate_overdue.advance(db)
    _agent_done(d, ok=False, message="the new database password failed the health checks")
    with session_factory()() as db:
        rotate_overdue.advance(db)
        st = rotate_overdue.status(db)
    assert st["state"] == "stopped" and "PostgreSQL password failed" in st["note"] and "health checks" in st["note"]
    assert st["left"] == ["Master key (LABAPE_SECRET_KEY: sessions, wraps the data keys)"] and not (d / "request.json").exists()
    # the next start has the failed one and the master key to do again
    assert client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True}).status_code == 200


def test_nothing_overdue_and_no_agent(client, monkeypatch, tmp_root):
    _admin_session(client)
    _setup(monkeypatch, tmp_root, late_hours=0)
    assert client.get("/api/admin/secrets/rotate-overdue").json()["plan"] == []
    assert client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True}).status_code == 409
    d = _setup(monkeypatch, tmp_root)
    (d / "agent.json").unlink()                                       # no update agent: the restarting ones can't run
    client.post("/api/admin/secrets/rotate-overdue", json={"acknowledge_restart": True})
    with session_factory()() as db:
        rotate_overdue.advance(db)
        db.query(Job).one().state = "succeeded"
        db.commit()
        rotate_overdue.advance(db)
        assert rotate_overdue.status(db)["state"] == "stopped" and "agent" in rotate_overdue.status(db)["note"]
