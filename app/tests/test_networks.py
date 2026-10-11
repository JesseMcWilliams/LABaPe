"""Network catalog, host attachments, allocation and the runner's rendering (design §20)."""
from pathlib import Path

import yaml

from labape.db import session_factory
from labape.engine.runner import PROFILE, prepare
from labape.models import Environment, IpAllocation, KvmHost, User
from labape.security import Principal, current_principal

from test_api import VLAN48, _admin_session, _host


def _env(name, groups):
    return {"name": name, "kvm_host_id": 1, "host_groups": groups}


def _group(name, **kw):
    return {"name": name, "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template",
            "template": "rocky9-base", **kw}


def test_catalog_validation(client):
    _admin_session(client)
    bad = [
        {**VLAN48, "gateway": "10.0.0.1"},                                   # gateway outside
        {**VLAN48, "static_pools": ["172.21.49.200-172.21.50.10"]},          # overlaps DHCP scope
        {**VLAN48, "static_pools": ["10.0.0.1-10.0.0.5"]},                   # pool outside
        {**VLAN48, "static_pools": []},                                      # static without a pool
        {**VLAN48, "allowed_roles": ["superuser"]},                          # unknown role
    ]
    for body in bad:
        assert client.post("/api/networks", json=body).status_code == 422, body
    assert client.post("/api/networks", json=VLAN48).status_code == 201
    overlap = {**VLAN48, "name": "other", "cidr": "172.21.50.0/24", "gateway": "172.21.50.1",
               "static_pools": ["172.21.50.10-172.21.50.20"], "dhcp_ranges": [], "reserved": []}
    assert client.post("/api/networks", json=overlap).status_code == 422


def test_host_attachment_rules(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.put(f"/api/hosts/{host_id}/networks",
                   json=[{"network": "vlan48", "bridge": "br1", "static_pool": ["172.21.48.20-172.21.48.30"]}])
    assert r.status_code == 422 and "isn't inside a static pool" in r.text
    r = client.put(f"/api/hosts/{host_id}/networks", json=[{"network": "nope", "bridge": "br1"}])
    assert r.status_code == 422


def test_static_allocation_and_release(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("netenv", [_group("app", count=2), _group("dh", addressing="dhcp")]))
    assert r.status_code == 201, r.text
    env = r.json()["environment"]
    assert env["addresses"] == {"app1": "172.21.50.1", "app2": "172.21.50.2"}
    assert [g["network"] for g in env["spec"]["host_groups"]] == ["vlan48", "vlan48"]
    # A second environment gets the next free addresses.
    r = client.post("/api/environments", json=_env("netenv2", [_group("web")]))
    assert r.json()["environment"]["addresses"] == {"web1": "172.21.50.3"}
    # Redeploy keeps the same addresses.
    from labape.engine.runner import _finish
    for jid in [j["id"] for j in client.get("/api/jobs").json()]:
        client.post(f"/api/jobs/{jid}/cancel")
    r = client.post(f"/api/environments/{env['id']}/deploy")
    assert r.status_code == 200
    assert client.get(f"/api/environments/{env['id']}").json()["addresses"]["app1"] == "172.21.50.1"
    # A successful destroy releases them.
    with session_factory()() as db:
        from labape.models import Job
        job = db.get(Job, r.json()["job_id"])
        job.state, job.type = "running", "environment.destroy"
        db.commit()
        _finish(db, job, db.get(Environment, env["id"]), 0)
        assert db.query(IpAllocation).filter(IpAllocation.environment_id == env["id"]).count() == 0


def test_dc_needs_static_and_mode_must_be_allowed(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("dcenv", [{**_group("dc", addressing="dhcp"),
                                                               "roles": ["domain_controller"]}]))
    assert r.status_code == 422 and "static" in r.text
    client.put("/api/networks/1", json={**VLAN48, "addressing": ["static"]})
    r = client.post("/api/environments", json=_env("dhenv", [_group("dh", addressing="dhcp")]))
    assert r.status_code == 422 and "doesn't allow dhcp" in r.text
    assert client.get("/api/environments").json() == []   # nothing half-created


def test_pool_exhaustion_leaves_nothing_behind(client):
    _admin_session(client)
    _host(client)
    client.put("/api/networks/1", json={**VLAN48, "static_pools": ["172.21.50.1-172.21.50.2"]})
    r = client.post("/api/environments", json=_env("big", [_group("app", count=3)]))
    assert r.status_code == 422 and "no free static addresses" in r.text
    assert client.get("/api/environments").json() == []
    with session_factory()() as db:
        assert db.query(IpAllocation).count() == 0


def test_network_limited_to_group(app, client):
    _admin_session(client)
    _host(client)
    client.put("/api/networks/1", json={**VLAN48, "allowed_groups": ["lab-network-users"]})
    with session_factory()() as db:
        u = User(provider="oidc", subject="s9", username="dev9")
        db.add(u)
        db.commit()
        uid = u.id
    app.dependency_overrides[current_principal] = lambda: Principal(uid, "dev9", "dev9", [], {"deployer"})
    try:
        assert client.get("/api/networks?kvm_host_id=1").json() == []
        r = client.post("/api/environments", json=_env("denied", [_group("app")]))
        assert r.status_code == 422 and "aren't allowed" in r.text
        app.dependency_overrides[current_principal] = lambda: Principal(
            uid, "dev9", "dev9", ["lab-network-users"], {"deployer"})
        assert [n["name"] for n in client.get("/api/networks?kvm_host_id=1").json()] == ["vlan48"]
        assert client.post("/api/environments", json=_env("allowed", [_group("app")])).status_code == 201
    finally:
        app.dependency_overrides.clear()


def test_runner_renders_catalog_and_addresses(client, tmp_root):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("rend", [_group("app", count=2), _group("dh", addressing="dhcp")]))
    env_id = r.json()["environment"]["id"]
    for d in ("scripts", "tofu/environments", "tofu/backends/libvirt", "ansible"):
        (tmp_root / "engine" / d).mkdir(parents=True, exist_ok=True)
    (tmp_root / "config").mkdir(exist_ok=True)
    (tmp_root / "config" / "environment.yml").write_text("network: {network_address: 10.0.0.0, subnet_mask: 255.0.0.0}\n")
    with session_factory()() as db:
        work = prepare(db, db.get(Environment, env_id), db.get(KvmHost, 1))
    env_yml = yaml.safe_load((work / "environment.yml").read_text())
    assert "network" not in env_yml and env_yml["default_network"] == "vlan48"
    assert env_yml["networks"]["vlan48"]["bridge"] == "br1"
    assert env_yml["networks"]["vlan48"]["static_pools"] == ["172.21.50.1-172.21.51.254"]
    profile = (work / "tofu" / "environments" / f"{PROFILE}.tfvars").read_text()
    assert '"addresses": ["172.21.50.1", "172.21.50.2"]' in profile
    assert "static_ip_offset_start" not in profile
    assert '"addressing": "dhcp"' in profile


def test_refresh_addresses_job(client):
    from labape.engine.runner import _finish, command_for
    from labape.models import Job
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("refr", [_group("dh", addressing="dhcp")]))
    env_id, job_id = r.json()["environment"]["id"], r.json()["job_id"]
    # Not deployed yet: refused.
    assert client.post(f"/api/environments/{env_id}/refresh-addresses").status_code == 409
    with session_factory()() as db:
        job = db.get(Job, job_id)
        job.state = "running"
        db.commit()
        _finish(db, job, db.get(Environment, env_id), 0)
    r = client.post(f"/api/environments/{env_id}/refresh-addresses")
    assert r.status_code == 200
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        env = db.get(Environment, env_id)
        assert job.type == "environment.refresh_addresses" and env.status == "deployed"
        assert command_for(job, env, Path("/w"))[1].endswith("refresh-addresses.sh")
        job.state = "running"
        db.commit()
        _finish(db, job, env, 1)            # a failed refresh leaves the environment deployed
        assert db.get(Environment, env_id).status == "deployed"


def test_bootstrap_is_idempotent(db_reset):
    from labape.bootstrap import apply
    from labape.models import HostNetwork, Network, RoleBinding
    data = {"networks": [VLAN48],
            "hosts": [{"name": "kvm1", "networks": [{"network": "vlan48", "bridge": "br1", "is_default": True}]}],
            "role_bindings": [{"principal_type": "group", "principal": "lab-admins", "role": "admin"}]}
    with session_factory()() as db:
        first = apply(db, data)
        assert any("created" in c for c in first)
        assert apply(db, data) == []                       # second run: nothing to do
        assert db.query(Network).count() == 1 and db.query(HostNetwork).count() == 1
        assert db.query(RoleBinding).count() == 1
        data["networks"][0] = {**VLAN48, "dns_servers": ["172.21.48.1", "172.21.48.2"]}
        assert apply(db, data) == ["network vlan48: dns_servers set"]

