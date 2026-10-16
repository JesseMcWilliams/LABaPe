"""Deleting a KVM host (design §55): hidden now, restorable, purged after the retention period."""
import datetime as dt

from sqlalchemy import select

from labape import appsettings
from labape.db import session_factory
from labape.engine import hostdelete, maintenance
from labape.models import Environment, HostFacts, HostNetwork, Job, KvmHost, Template, utcnow

from test_api import ENV, _admin_session, _host


def _names(client):
    return [h["name"] for h in client.get("/api/hosts").json()]


def test_delete_hides_the_host_and_restore_brings_it_back(client):
    _admin_session(client)
    keep = _host(client)
    other = client.post("/api/hosts", json={"name": "kvm2"}).json()["id"]
    r = client.delete(f"/api/hosts/{other}")
    assert r.status_code == 200 and r.json()["retention_days"] == 30
    assert _names(client) == ["kvm1"]
    assert client.get(f"/api/hosts/{other}").status_code == 404
    deleted = client.get("/api/hosts/deleted").json()
    assert [d["name"] for d in deleted] == ["kvm2"] and deleted[0]["deleted_by"] and deleted[0]["purge_on"]
    assert "host.delete" in [a["action"] for a in client.get("/api/admin/audit").json()]
    assert client.post(f"/api/hosts/{other}/restore").status_code == 200
    assert sorted(_names(client)) == ["kvm1", "kvm2"] and client.get("/api/hosts/deleted").json() == []
    with session_factory()() as db:
        assert db.get(KvmHost, other).enabled is False                 # back, but turned off
    assert client.post(f"/api/hosts/{other}/restore").status_code == 404
    assert keep


def test_a_host_that_is_in_use_is_not_deleted(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "name": "aaa", "kvm_host_id": host_id,
                                               "host_groups": [{**ENV["host_groups"][0], "name": "aaa"}]})
    assert r.status_code == 201, r.text
    d = client.delete(f"/api/hosts/{host_id}")
    assert d.status_code == 409 and "environments use it: aaa" in d.text
    with session_factory()() as db:
        db.query(Environment).update({"status": "destroyed"})
        db.add(Job(type="host.update", requested_by="t", kvm_host_id=host_id, state="queued"))
        db.commit()
    assert "queued or running" in client.delete(f"/api/hosts/{host_id}").text
    with session_factory()() as db:
        db.query(Job).delete()
        h = db.get(KvmHost, host_id)
        h.is_primary = True
        db.commit()
    assert "primary file host" in client.delete(f"/api/hosts/{host_id}").text
    assert _names(client) == ["kvm1"]


def test_purge_after_the_retention_period(client):
    _admin_session(client)
    _host(client)                                                     # the first host is the primary file host
    host_id = _host(client, name="kvm9")
    with session_factory()() as db:
        db.add_all([HostFacts(kvm_host_id=host_id, facts={}, smart={}), Template(kvm_host_id=host_id, name="t1", os_key="x"),
                    Job(type="host.update", requested_by="t", kvm_host_id=host_id, state="succeeded")])
        db.commit()
        assert db.query(HostNetwork).filter_by(kvm_host_id=host_id).count() == 1
    assert client.delete(f"/api/hosts/{host_id}").status_code == 200
    with session_factory()() as db:
        assert hostdelete.purge_due(db) == []                                             # inside the 30 days
        h = db.scalar(select(KvmHost).where(KvmHost.id == host_id), execution_options=hostdelete.ALL)
        h.deleted_at = utcnow() - dt.timedelta(days=31)
        db.commit()
        assert maintenance.purge_history(db)["deleted_hosts"] == ["kvm9: removed"]
        assert db.scalar(select(KvmHost).where(KvmHost.id == host_id), execution_options=hostdelete.ALL) is None
        assert db.query(HostFacts).count() == 0 and db.query(Template).count() == 0
        assert db.query(HostNetwork).filter_by(kvm_host_id=host_id).count() == 0
        assert db.query(Job).filter(Job.type == "host.update").one().kvm_host_id is None   # the job's history stays


def test_a_host_an_environment_still_points_at_is_kept_as_a_hidden_record(client):
    _admin_session(client)
    _host(client)
    host_id = _host(client, name="kvm8")
    r = client.post("/api/environments", json={**ENV, "name": "bbb", "kvm_host_id": host_id,
                                               "host_groups": [{**ENV["host_groups"][0], "name": "bbb"}]})
    assert r.status_code == 201
    with session_factory()() as db:
        db.query(Environment).update({"status": "destroyed"})
        db.query(Job).update({"state": "succeeded"})                  # the deploy queued when it was created
        db.commit()
    assert client.delete(f"/api/hosts/{host_id}").status_code == 200
    with session_factory()() as db:
        h = db.scalar(select(KvmHost).where(KvmHost.id == host_id), execution_options=hostdelete.ALL)
        h.deleted_at = utcnow() - dt.timedelta(days=40)
        db.commit()
        assert hostdelete.purge_due(db) == ["kvm8: kept as a hidden record (environments still point at it)"]
        h = db.scalar(select(KvmHost).where(KvmHost.id == host_id), execution_options=hostdelete.ALL)
        assert h.name == f"deleted-{host_id}" and h.purged and h.ssh_address == ""
        assert hostdelete.deleted_hosts(db) == [] and hostdelete.purge_due(db) == []
    assert client.get("/api/hosts/deleted").json() == []
    assert client.post("/api/hosts", json={"name": "kvm8"}).status_code == 201             # the name is free again


def test_the_retention_is_a_setting(client):
    _admin_session(client)
    with session_factory()() as db:
        assert appsettings.get(db, "retention").deleted_hosts_days == 30
    r = client.get("/api/admin/settings").json()["retention"]
    assert r["deleted_hosts_days"] == 30
    assert client.put("/api/admin/settings/retention", json={**r, "deleted_hosts_days": 7}).status_code == 200
    _host(client)
    host_id = _host(client, name="kvm7")
    assert client.delete(f"/api/hosts/{host_id}").json()["retention_days"] == 7


def test_a_deleted_host_does_not_count_against_the_host_limit(client):
    _admin_session(client)
    ids = [_host(client, name=f"limit{i}") for i in range(4)]
    assert client.post("/api/hosts", json={"name": "limit5"}).status_code == 409           # at the limit
    assert client.delete(f"/api/hosts/{ids[3]}").status_code == 200
    assert client.post("/api/hosts", json={"name": "limit5"}).status_code == 201
