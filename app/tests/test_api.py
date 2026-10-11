"""API behaviour: break-glass, hosts, environments, jobs, permissions."""
import datetime as dt

from labape.auth import breakglass
from labape.db import session_factory
from labape.models import BreakglassGrant, Job, KvmHost, User
from labape.security import Principal, current_principal


def _bg_token(minutes=5, local_only=False):
    with session_factory()() as db:
        return breakglass.issue(db, "pytest", minutes, local_only)[0]


def _admin_session(client):
    r = client.post("/api/auth/breakglass", json={"token": _bg_token()})
    assert r.status_code == 200, r.text


VLAN48 = {"name": "vlan48", "cidr": "172.21.48.0/22", "gateway": "172.21.48.1", "dns_servers": ["172.21.48.1"],
          "addressing": ["static", "dhcp"], "static_pools": ["172.21.50.1-172.21.51.254"],
          "dhcp_ranges": ["172.21.48.16-172.21.49.254"], "reserved": ["172.21.48.2-172.21.48.15"]}


def _host(client, attach=True, **kw):
    body = {"name": "kvm1", **kw}
    r = client.post("/api/hosts", json=body)
    assert r.status_code == 201, r.text
    host_id = r.json()["id"]
    if attach:
        if client.get("/api/networks").json() == []:
            assert client.post("/api/networks", json=VLAN48).status_code == 201
        r = client.put(f"/api/hosts/{host_id}/networks",
                       json=[{"network": "vlan48", "bridge": "br1", "is_default": True}])
        assert r.status_code == 200, r.text
    return host_id


ENV = {"name": "lab2",
       "host_groups": [{"name": "app", "os": "rocky9", "roles": ["linux_server"],
                        "image_source": "packer_template", "template": "rocky9-base"}]}


def test_unauthenticated(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/environments").status_code == 401


def test_providers_hide_breakglass(client):
    assert all(p["name"] != "breakglass" for p in client.get("/api/auth/providers").json())


def test_breakglass_one_time(client):
    token = _bg_token()
    assert client.post("/api/auth/breakglass", json={"token": token}).status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["breakglass"] and "admin" in me["roles"]
    client.post("/api/auth/logout")
    r = client.post("/api/auth/breakglass", json={"token": token})
    assert r.status_code == 401 and "already used" in r.text


def test_breakglass_expired_and_unknown(client):
    token = _bg_token()
    with session_factory()() as db:
        g = db.query(BreakglassGrant).one()
        g.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    assert "expired" in client.post("/api/auth/breakglass", json={"token": token}).text
    assert "unknown" in client.post("/api/auth/breakglass", json={"token": "nope"}).text


def test_breakglass_local_only(client):
    token = _bg_token(local_only=True)
    r = client.post("/api/auth/breakglass", json={"token": token})
    assert r.status_code == 401 and "host-only" in r.text
    r = client.post("/api/auth/breakglass", json={"token": token}, headers={"X-LABaPe-Local": "1"})
    assert r.status_code == 200


def test_cross_origin_write_refused(client):
    r = client.post("/api/auth/breakglass", json={"token": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_hosts(client, monkeypatch):
    _admin_session(client)
    _host(client)
    r = client.post("/api/hosts", json={"name": "kvm2", "libvirt_uri": "qemu+ssh://root@x/system"})
    assert r.status_code == 422
    from labape.config import get_settings
    monkeypatch.setattr(get_settings(), "max_kvm_hosts", 1)
    assert client.post("/api/hosts", json={"name": "kvm3"}).status_code == 409


def test_environment_lifecycle(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id})
    assert r.status_code == 201, r.text
    env_id, job_id = r.json()["environment"]["id"], r.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == "queued"
    # One job per environment at a time.
    assert client.post(f"/api/environments/{env_id}/destroy").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "cancelled"
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert client.get(f"/api/environments/{env_id}/credentials").status_code == 200
    actions = [a["action"] for a in client.get("/api/admin/audit").json()]
    assert "environment.create" in actions and "job.retry" in actions


def test_environment_validation(client):
    _admin_session(client)
    host_id = _host(client)
    bad = {**ENV, "kvm_host_id": host_id, "name": "test-x"}
    assert client.post("/api/environments", json=bad).status_code == 422
    groups = [ENV["host_groups"][0], ENV["host_groups"][0]]
    assert client.post("/api/environments", json={**ENV, "kvm_host_id": host_id,
                                                  "host_groups": groups}).status_code == 422


def test_non_owner_cannot_see(app, client):
    _admin_session(client)
    host_id = _host(client)
    env_id = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id}).json()["environment"]["id"]
    with session_factory()() as db:
        u = User(provider="oidc", subject="s1", username="dev1")
        db.add(u)
        db.commit()
        uid = u.id
    app.dependency_overrides[current_principal] = lambda: Principal(uid, "dev1", "dev1", [], {"deployer"})
    try:
        assert client.get("/api/environments").json() == []
        assert client.get(f"/api/environments/{env_id}").status_code == 404
        assert client.post("/api/hosts", json={"name": "kvm9"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_claim_respects_host_limit(client):
    from labape.engine.runner import claim
    _admin_session(client)
    host_id = _host(client, concurrency_limit=1)
    for n in ("aaa", "bbb"):
        r = client.post("/api/environments", json={**ENV, "name": n, "kvm_host_id": host_id,
                                                   "host_groups": [{**ENV["host_groups"][0], "name": n}]})
        assert r.status_code == 201, r.text
    with session_factory()() as db:
        first = claim(db, "w")
        assert first is not None and first.state == "running"
        assert claim(db, "w") is None
        assert db.query(Job).filter(Job.state == "queued").count() == 1
        db.get(KvmHost, host_id).concurrency_limit = 2
        db.commit()
        assert claim(db, "w") is not None
