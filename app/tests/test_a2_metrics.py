"""Batch A2 (design §38): Prometheus metrics, read with a service token."""
import re

from labape.db import session_factory
from labape.models import HostStats, StackLink

from test_a1_tokens import _admin_session, _bearer, _no_session
from test_api import _host

VAL = r'"(?:[^"\\]|\\.)*"'                   # a label value: escaped quotes and backslashes allowed
LINE = re.compile(rf'^[a-z_][a-z0-9_]*(\{{[a-z_]+={VAL}(,[a-z_]+={VAL})*\}})? -?[0-9.e+-]+$')


def test_metrics_need_an_auditor_token_and_parse(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        db.merge(HostStats(kvm_host_id=1, stats={"cpus": 8, "cpu_pct": 12.5, "memory_total_mb": 1024, "memory_available_mb": 256,
                                                 "storage": {"vm": {"total_gb": 100, "free_gb": 40}}, "vms_running": 2}))
        db.add(StackLink(name='pi"lab', url="https://pi.lab", ca_pem="x", ca_fingerprint="0" * 64, inbound_hash="h"))
        db.commit()
    viewer = client.post("/api/tokens", json={"name": "v", "kind": "service", "roles": ["viewer"]}).json()["secret"]
    scraper = client.post("/api/tokens", json={"name": "prometheus", "kind": "service", "roles": ["auditor"]}).json()["secret"]
    _no_session(client)
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers=_bearer(viewer)).status_code == 403
    r = client.get("/api/metrics", headers=_bearer(scraper))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = r.text
    for line in body.splitlines():
        assert line.startswith("# ") or LINE.match(line), line
    assert 'labape_host_cpus{host="kvm1",role="kvm"} 8' in body
    assert 'labape_host_memory_used_bytes{host="kvm1",role="kvm"} 805306368' in body
    assert 'labape_host_vm_disk_free_bytes{host="kvm1",role="kvm"} 42949672960' in body
    assert 'labape_link_up{stack="pi\\"lab"} 0' in body
    assert body.count("# TYPE labape_host_cpus gauge") == 1
    assert 'labape_jobs{state="running"} 0' in body and "labape_maintenance 0" in body
