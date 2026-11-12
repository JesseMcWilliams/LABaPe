"""Batch A1 (plan: Planning_Next-Round-2026-11-08.md): container CPU and memory history."""
import datetime as dt

from labape.db import session_factory
from labape.engine import hostadmin, hostcontainers
from labape.models import ContainerSample, KvmHost, utcnow

from test_api import _admin_session
from test_c1_containers import FakeSsh, _ready_host, c, data


def with_stats(name, cpu, mem, **kw):
    return c(name, stats={"cpu": cpu, "memory": mem, "memory_pct": "1%"}, **kw)


def test_percent_and_memory_are_read_from_the_runtimes_text():
    assert hostcontainers.parse_percent("12.34%") == 12.34 and hostcontainers.parse_percent("0.00%") == 0.0
    assert hostcontainers.parse_percent("--") is None and hostcontainers.parse_percent("") is None
    assert hostcontainers.parse_size("512MiB / 2GiB".split("/")[0]) == 512 * 1024 ** 2


def test_a_collection_keeps_a_sample_per_running_container_and_a_week(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    got = data(with_stats("web", "12.5%", "512MiB / 2GiB"), c("idle"), with_stats("odd", "--", "--"))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh(payload=got))
    now = utcnow()
    with session_factory()() as db:
        db.add(ContainerSample(kvm_host_id=host_id, name="web", at=now - dt.timedelta(days=8), cpu_pct=1.0, mem_bytes=1))
        db.add(ContainerSample(kvm_host_id=host_id, name="web", at=now - dt.timedelta(days=2), cpu_pct=2.0, mem_bytes=2))
        db.commit()
        hostcontainers.collect(db, db.get(KvmHost, host_id), now)
        rows = db.query(ContainerSample).order_by(ContainerSample.at).all()
        assert [(r.name, r.cpu_pct) for r in rows] == [("web", 2.0), ("web", 12.5)]        # the 8-day-old one is gone; idle and odd have none
        assert rows[-1].mem_bytes == 512 * 1024 ** 2


def test_history_is_thinned_and_the_api_checks_its_input(client):
    _admin_session(client)
    host_id = _ready_host(client)
    now = utcnow()
    with session_factory()() as db:
        for i in range(1500):
            db.add(ContainerSample(kvm_host_id=host_id, name="web", at=now - dt.timedelta(minutes=i), cpu_pct=float(i % 100), mem_bytes=i))
        db.add(ContainerSample(kvm_host_id=host_id, name="other", at=now, cpu_pct=1.0, mem_bytes=1))
        db.commit()
        pts = hostcontainers.history(db, host_id, "web", 48, now)
        assert 0 < len(pts) <= 700 and pts[0]["t"] < pts[-1]["t"]
        assert all(p["cpu"] is not None for p in pts)
        assert len(hostcontainers.history(db, host_id, "web", 1, now)) == 60 or len(hostcontainers.history(db, host_id, "web", 1, now)) == 61
    r = client.get(f"/api/hosts/{host_id}/containers/history?name=web&hours=24").json()
    assert r["name"] == "web" and r["points"] and set(r["points"][0]) == {"t", "cpu", "memory"}
    assert all(p["t"] > 0 for p in client.get(f"/api/hosts/{host_id}/containers/history?name=other&hours=24").json()["points"])
    assert client.get(f"/api/hosts/{host_id}/containers/history?name=web&hours=0").status_code == 422
    assert client.get(f"/api/hosts/{host_id}/containers/history?name=web&hours=999").status_code == 422
    assert client.get(f"/api/hosts/{host_id}/containers/history?name=a;b&hours=24").status_code == 422
