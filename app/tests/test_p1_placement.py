"""Batch P1a (design §43): best-fit placement, and templates copied to every host ahead of need."""
import datetime as dt

from labape import appsettings
from labape.db import session_factory
from labape.engine import filedist
from labape.models import HostStats, Job, KvmHost, Setting

from test_api import _admin_session, _host

GROUP = {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "cpu_count": 2, "memory_mb": 4096}


def _stats(host_id, committed_mb, total_mb=65536, cpus=16, vcpus=8):
    return HostStats(kvm_host_id=host_id, collected_at=dt.datetime.now(dt.timezone.utc), stats={
        "cpus": cpus, "memory_total_mb": total_mb, "memory_committed_mb": committed_mb, "vcpus_allocated": vcpus,
        "storage": {"vm": {"total_gb": 1000, "free_gb": 800}}})


def test_the_least_loaded_host_wins_and_auto_create_uses_it(client):
    _admin_session(client)
    a = _host(client, name="kvm1")
    b = _host(client, name="kvm2")
    with session_factory()() as db:
        db.merge(_stats(a, committed_mb=50000))           # busy
        db.merge(_stats(b, committed_mb=8000))
        db.commit()
    r = client.post("/api/environments/placement", json={"host_groups": [GROUP]}).json()["hosts"]
    assert [h["name"] for h in r] == ["kvm2", "kvm1"] and all(h["ok"] for h in r)
    assert "of memory committed with it" in r[0]["reasons"][0]
    with session_factory()() as db:
        db.add(Job(type="environment.deploy", kvm_host_id=b, state="running", requested_by="x"))
        db.add(Job(type="environment.deploy", kvm_host_id=b, state="queued", requested_by="x"))
        db.commit()
    r = client.post("/api/environments/placement", json={"host_groups": [GROUP]}).json()["hosts"]
    assert r[0]["name"] == "kvm2" and any("2 job(s)" in x for x in r[0]["reasons"])   # still the better one
    with session_factory()() as db:
        db.merge(_stats(b, committed_mb=64000))            # now full: refused there
        db.commit()
    r = client.post("/api/environments/placement", json={"host_groups": [GROUP]}).json()["hosts"]
    assert r[0]["name"] == "kvm1" and r[1]["ok"] is False and "RAM" in r[1]["reasons"][0]
    created = client.post("/api/environments", json={"name": "autolab", "kvm_host_id": None, "host_groups": [GROUP]})
    assert created.status_code == 201, created.text
    assert created.json()["environment"]["kvm_host"] == "kvm1"


def test_no_host_fits(client):
    _admin_session(client)
    a = _host(client, name="kvm1")
    with session_factory()() as db:
        db.merge(_stats(a, committed_mb=65000))
        db.commit()
    r = client.post("/api/environments", json={"name": "nolab", "kvm_host_id": None, "host_groups": [GROUP]})
    assert r.status_code == 422 and "No KVM host can take this environment" in r.text


def test_template_sync_is_daily_and_optional(client):
    _admin_session(client)
    _host(client)
    now = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        assert filedist.schedule_template_sync(db, now) is None                  # off by default
        appsettings.put(db, "file_distribution", appsettings.FileDistributionSettings(copy_templates_to_all=True))
        jid = filedist.schedule_template_sync(db, now)
        assert jid and db.get(Job, jid).type == "files.sync_templates"
        assert filedist.schedule_template_sync(db, now + dt.timedelta(hours=1)) is None    # once a day
        db.get(Job, jid).state = "succeeded"
        db.commit()
        assert filedist.schedule_template_sync(db, now + dt.timedelta(hours=25))
        assert db.get(Setting, filedist.SYNC_KEY).value["queued"]
        assert db.get(KvmHost, 1).is_primary or filedist.ensure_primary(db) is not None
