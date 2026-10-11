"""Secret rotation R3: master key re-wrap, restart-needing rotations handed to the host agent."""
import json

from cryptography.fernet import Fernet

from labape import update
from labape.config import get_settings
from labape.db import session_factory
from labape.engine import rotation
from labape.models import Secret, SecretPolicy
from labape.security import get_secret, master_fernet, put_secret, rewrap_master

from test_api import _admin_session


def test_rewrap_master(client, monkeypatch):
    _admin_session(client)
    old = get_settings().secret_key
    new = "a-brand-new-master-key-for-the-test"
    with session_factory()() as db:
        put_secret(db, "test/r3", "value-one")
        db.add(Secret(name="test/r3-legacy", value=master_fernet(old).encrypt(b"legacy").decode()))
        db.commit()
        assert rewrap_master(db, old, new) >= 1
        assert db.get(Secret, "test/r3-legacy").value.startswith("v2:")
    monkeypatch.setattr(get_settings(), "secret_key", new)
    with session_factory()() as db:
        assert get_secret(db, "test/r3") == "value-one" and get_secret(db, "test/r3-legacy") == "legacy"
        rewrap_master(db, new, old)          # put it back for the other tests
    monkeypatch.setattr(get_settings(), "secret_key", old)
    with session_factory()() as db:
        assert get_secret(db, "test/r3") == "value-one"


def _agent(monkeypatch, tmp_root, installed=True):
    d = tmp_root / "r3-update"
    d.mkdir(exist_ok=True)
    for f in d.iterdir():
        f.unlink()
    monkeypatch.setattr(get_settings(), "update_dir", d)
    if installed:
        (d / "agent.json").write_text("{}")
    return d


def test_agent_rotation_round_trip(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _agent(monkeypatch, tmp_root)
    pols = {p["kind"]: p for p in client.get("/api/admin/secrets").json()["policies"]}
    pg = pols["postgres_password"]
    assert pg["automatic"] and pg["needs_restart"]
    r = client.post(f"/api/admin/secrets/{pg['id']}/rotate")
    assert r.status_code == 200 and "agent_request" in r.json()
    req = json.loads((d / "request.json").read_text())
    assert req["action"] == "rotate" and req["target"] == "postgres_password" and req["policy_id"] == pg["id"]
    assert client.post(f"/api/admin/secrets/{pols['master_key']['id']}/rotate").status_code == 409   # agent busy
    (d / "request.json").unlink()
    (d / "status.json").write_text(json.dumps({"id": req["id"], "action": "rotate", "state": "succeeded",
                                               "message": "rotated postgres_password"}))
    with session_factory()() as db:
        rotation.reconcile_agent(db)
        p = db.get(SecretPolicy, pg["id"])
        assert p.status == "ok" and p.last_rotated_at
    # A failure is recorded with the agent's message and retried later.
    r = client.post(f"/api/admin/secrets/{pols['ca_password']['id']}/rotate")
    req = json.loads((d / "request.json").read_text())
    (d / "request.json").unlink()
    (d / "status.json").write_text(json.dumps({"id": req["id"], "action": "rotate", "state": "failed",
                                               "message": "the new CA password failed the health checks"}))
    with session_factory()() as db:
        rotation.reconcile_agent(db)
        p = db.get(SecretPolicy, pols["ca_password"]["id"])
        assert p.status == "failed" and "health checks" in p.last_error


def test_no_agent(client, monkeypatch, tmp_root):
    _admin_session(client)
    _agent(monkeypatch, tmp_root, installed=False)
    mk = next(p for p in client.get("/api/admin/secrets").json()["policies"] if p["kind"] == "master_key")
    r = client.post(f"/api/admin/secrets/{mk['id']}/rotate")
    assert r.status_code == 409 and "agent" in r.json()["errors"][0]["message"]
    assert update.agent_installed() is False
    assert Fernet  # imported for parity with the store's key type


def test_provisioner_key_round_trip(tmp_root):
    import json as _json
    from labape import ca
    jwk = b'{"kty":"EC","crv":"P-256","x":"x","y":"y","d":"d"}'
    token = ca.jwe_encrypt(jwk, b"old-password", {"cty": "jwk+json", "p2c": 1000})
    assert ca.jwe_decrypt(token, b"old-password") == jwk
    cfg = tmp_root / "ca.json"
    cfg.write_text(_json.dumps({"authority": {"provisioners": [{"name": "acme", "type": "ACME"},
                                                                {"name": "labape-runner", "encryptedKey": token}]}}))
    ca.repass_provisioner(str(cfg), b"old-password", b"new-password")
    new_token = _json.loads(cfg.read_text())["authority"]["provisioners"][1]["encryptedKey"]
    assert ca.jwe_decrypt(new_token, b"new-password") == jwk
    import pytest
    with pytest.raises(Exception):
        ca.jwe_decrypt(new_token, b"old-password")
