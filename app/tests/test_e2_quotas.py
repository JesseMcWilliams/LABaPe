"""Batch E2 (design §41): quotas per user, group and default."""
from labape import quotas
from labape.db import session_factory
from labape.models import Environment, Quota
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

GROUPS = [{"name": "web", "count": 2, "cpu_count": 4, "memory_mb": 8192, "disk_gb": 50}]   # 8 vCPUs, 16 GB, 100 GB


def _p(name="dev", groups=("lab-users",), admin=False):
    return Principal(1, name, name, list(groups), {"admin"} if admin else {"deployer"})


def test_which_quota_applies(client):
    with session_factory()() as db:
        assert quotas.limits_for(db, _p()) is None                              # none set: no limit
        db.add(Quota(principal_type="default", principal="", max_vcpus=4))
        db.add(Quota(principal_type="group", principal="lab-users", max_vcpus=16, max_environments=None))
        db.add(Quota(principal_type="group", principal="testers", max_vcpus=8, max_environments=2))
        db.commit()
        assert quotas.limits_for(db, _p(groups=()))["max_vcpus"] == 4              # the default
        both = quotas.limits_for(db, _p(groups=("lab-users", "testers")))
        assert both["max_vcpus"] == 16 and both["max_environments"] is None      # the most generous, per limit
        db.add(Quota(principal_type="user", principal="dev", max_vcpus=2))
        db.commit()
        assert quotas.limits_for(db, _p())["max_vcpus"] == 2 and quotas.limits_for(db, _p())["source"] == "user dev"
        assert quotas.limits_for(db, _p(admin=True)) is None                      # admins: none


def test_usage_and_the_check(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        db.add(Quota(principal_type="user", principal="dev", max_vcpus=12, max_environments=2))
        db.add(Environment(name="a", kvm_host_id=1, created_by="dev", status="deployed", spec={"host_groups": GROUPS}))
        db.add(Environment(name="b", kvm_host_id=1, created_by="dev", status="destroyed", spec={"host_groups": GROUPS}))
        db.commit()
        u = quotas.usage(db, "dev")
        assert u == {"vcpus": 8, "memory_mb": 16384, "disk_gb": 100, "environments": 1}   # destroyed ones don't count
        small = [{"name": "x", "count": 1, "cpu_count": 2}]
        quotas.check(db, _p(), small)                                            # 10 of 12
        try:
            quotas.check(db, _p(), GROUPS)
        except quotas.QuotaError as exc:
            assert "16 of 12 vCPUs" in str(exc) and "user dev" in str(exc)
        else:
            raise AssertionError("expected a quota error")


def test_the_api_and_create_refusal(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/admin/quotas", json={"principal_type": "user", "principal": "dev", "max_environments": 0})
    assert r.status_code == 200 and r.json()["max_environments"] == 0
    assert client.post("/api/admin/quotas", json={"principal_type": "group"}).status_code == 422     # needs a name
    assert client.post("/api/admin/quotas", json={"principal_type": "default", "principal": "x", "max_vcpus": 64}).json()["principal"] == ""
    assert len(client.get("/api/admin/quotas").json()) == 2
    client.app.dependency_overrides[current_principal] = lambda: _p()
    me = client.get("/api/quotas/me").json()
    assert me["limits"]["max_environments"] == 0 and me["usage"]["environments"] == 0
    body = {"name": "devlab", "kvm_host_id": 1, "host_groups": [{"name": "web", "os": "rocky9", "roles": ["linux_server"]}]}
    r = client.post("/api/environments", json=body)
    assert r.status_code == 403 and "Over your quota" in r.text
    client.app.dependency_overrides.clear()
    _admin_session(client)
    qid = next(q["id"] for q in client.get("/api/admin/quotas").json() if q["principal"] == "dev")
    assert client.delete(f"/api/admin/quotas/{qid}").json()["ok"]
