"""Secret rotation (R1): envelope encryption, data-key rotation, policies, scheduling, failures, directory token."""
import datetime as dt

import httpx

from labape import appsettings
from labape.db import session_factory
from labape.engine import rotation
from labape.engine.runner import claim, run
from labape.models import Job, Secret, SecretDataKey, SecretPolicy, utcnow
from labape.security import _fernet, get_secret, put_secret

from test_api import _admin_session


def _policy(db, kind):
    return db.query(SecretPolicy).filter(SecretPolicy.kind == kind).one()


def test_envelope_and_data_key_rotation(client):
    _admin_session(client)
    with session_factory()() as db:
        put_secret(db, "test/a", "alpha")
        db.add(Secret(name="test/legacy", value=_fernet().encrypt(b"old style").decode()))   # pre-v2 value
        db.commit()
        assert db.get(Secret, "test/a").value.startswith("v2:")
        assert get_secret(db, "test/legacy") == "old style"
        rotation.ensure_policies(db)
        before = {k.id for k in db.query(SecretDataKey).filter(SecretDataKey.active.is_(True))}
        summary = rotation.rotate_data_key(db, _policy(db, "builtin_data_key"), None)
        assert "re-encrypted" in summary
        after = db.query(SecretDataKey).filter(SecretDataKey.active.is_(True)).one()
        assert after.id not in before
        assert get_secret(db, "test/a") == "alpha" and get_secret(db, "test/legacy") == "old style"
        assert db.get(Secret, "test/legacy").value.startswith(f"v2:{after.id}:")       # converted


def test_policies_api_and_rotate_now(client):
    _admin_session(client)
    r = client.get("/api/admin/secrets").json()
    kinds = {p["kind"]: p for p in r["policies"]}
    assert set(rotation.PLATFORM) <= set(kinds)
    assert kinds["postgres_password"]["needs_restart"] and kinds["postgres_password"]["interval_hours"] == 168
    assert kinds["directory_token"]["secret_class"] == "read_only" and kinds["directory_token"]["interval_hours"] == 168
    assert kinds["smtp_password"]["status"] == "manual" and not kinds["smtp_password"]["automatic"]
    pg = kinds["postgres_password"]
    body = {"interval_hours": 24, "enabled": True, "managed_externally": False, "store": "builtin", "elevated_ref": "",
            "params": {}, "notes": ""}
    r = client.put(f"/api/admin/secrets/{pg['id']}", json=body)
    assert r.status_code == 422 and "maintenance window" in r.json()["errors"][0]["message"]
    assert client.post(f"/api/admin/secrets/{kinds['smtp_password']['id']}/rotate").status_code == 422
    dk = kinds["builtin_data_key"]
    job_id = client.post(f"/api/admin/secrets/{dk['id']}/rotate").json()["job_id"]
    assert client.post(f"/api/admin/secrets/{dk['id']}/rotate").status_code == 409        # already queued
    with session_factory()() as db:
        assert claim(db, "test").id == job_id
    run(job_id, "test")
    with session_factory()() as db:
        assert db.get(Job, job_id).state == "succeeded"
        p = _policy(db, "builtin_data_key")
        assert p.status == "ok" and p.last_rotated_at and p.next_due_at
    assert client.post("/api/admin/secrets/elevated", json={"name": "authentik-admin", "value": "short"}).status_code == 422
    assert client.post("/api/admin/secrets/elevated", json={"name": "authentik-admin",
                                                            "value": "ThisIsMy_FAKE_Password6!"}).status_code == 201
    assert client.get("/api/admin/secrets").json()["elevated"] == ["authentik-admin"]


def test_schedule_rules(client):
    _admin_session(client)
    now = utcnow()
    with session_factory()() as db:
        rotation.ensure_policies(db)
        for p in db.query(SecretPolicy):
            p.next_due_at = now - dt.timedelta(minutes=1)
        _policy(db, "worker_ssh_key").managed_externally = True
        db.commit()
        kinds = {p.kind for p in rotation.due(db, now)}
        assert "builtin_data_key" in kinds and "directory_token" in kinds
        assert "worker_ssh_key" not in kinds                    # its store rotates it
        assert "smtp_password" not in kinds                     # reminder only
        first = rotation.schedule(db, now)
        assert first and rotation.schedule(db, now) == []       # no duplicates while queued
        _policy(db, "builtin_data_key").next_due_at = now + dt.timedelta(hours=5)
        db.commit()
        assert "builtin_data_key" not in {p.kind for p in rotation.due(db, now)}


def test_failure_keeps_old_secret_and_retries(client):
    _admin_session(client)
    with session_factory()() as db:
        rotation.ensure_policies(db)
        put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, "old-token-value")
        pid = _policy(db, "directory_token").id
        job = Job(type="secret.rotate", params={"policy_id": pid}, requested_by="admin")
        db.add(job)
        db.commit()
    with session_factory()() as db:
        claim(db, "test")
    run(job.id, "test")
    with session_factory()() as db:
        p = db.get(SecretPolicy, pid)
        assert db.get(Job, job.id).state == "failed" and p.status == "failed" and "elevated" in p.last_error
        nxt = p.next_due_at if p.next_due_at.tzinfo else p.next_due_at.replace(tzinfo=dt.timezone.utc)
        assert nxt - utcnow() < dt.timedelta(hours=1, minutes=1)
        assert get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) == "old-token-value"


def test_directory_token_rotation(client, monkeypatch):
    _admin_session(client)
    calls = []

    def fake_post(url, json, headers, timeout, verify):
        calls.append((url, json["key"], headers["Authorization"]))
        return httpx.Response(204, request=httpx.Request("POST", url))
    monkeypatch.setattr(rotation.httpx, "post", fake_post)
    from labape.api import directory
    seen = []
    monkeypatch.setattr(directory, "authentik_groups", lambda cfg, token, q: seen.append(token) or ["g"])
    with session_factory()() as db:
        appsettings.put(db, "directory", appsettings.DirectorySettings(enabled=True, api_url="https://ak.example.test"))
        put_secret(db, appsettings.DIRECTORY_TOKEN_SECRET, "old-token-value")
        put_secret(db, rotation.ELEVATED_PREFIX + "ak-admin", "ThisIsMy_FAKE_Password6!")
        rotation.ensure_policies(db)
        p = _policy(db, "directory_token")
        p.elevated_ref = "ak-admin"
        db.commit()
        assert "new key" in rotation.rotate_directory_token(db, p, None)
        new = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET)
    assert new != "old-token-value" and seen == [new]
    assert calls == [("https://ak.example.test/api/v3/core/tokens/labape-directory/set_key/", new,
                      "Bearer ThisIsMy_FAKE_Password6!")]
