"""Dashboard: libvirt parsing, per-VM sampling, what each user sees, admin health, release check."""
import datetime as dt

import httpx

from labape import version
from labape.db import session_factory
from labape.engine import hoststats
from labape.models import HostStats, KvmHost, MetricSample, utcnow
from labape.security import Principal, current_principal

from test_api import _admin_session, _host

DOMSTATS = """Domain: 'web1'
  state.state=1
  cpu.time=2000000000000
  balloon.current=2097152
  balloon.maximum=2097152
  balloon.unused=1048576
  balloon.available=1572864
  vcpu.maximum=2
  block.count=2
  block.0.name=vda
  block.0.path=/data/VMs/web1.qcow2
  block.0.rd.bytes=1000000
  block.0.wr.bytes=2000000
  block.0.allocation=5368709120
  block.0.capacity=42949672960
  block.1.name=sda
  block.1.path=/data/isos/seed.iso
  block.1.capacity=1000

Domain: 'old1'
  state.state=5
  balloon.maximum=1048576
  vcpu.maximum=1
  block.count=0
"""

GUEST = """fs.count             : 3
fs.0.name            : vda1
fs.0.mountpoint      : /
fs.0.total-bytes     : 21474836480
fs.0.used-bytes      : 5368709120
fs.1.name            : vda1
fs.1.mountpoint      : /var/lib/containers
fs.1.total-bytes     : 21474836480
fs.1.used-bytes      : 5368709120
fs.2.name            : vda2
fs.2.mountpoint      : /boot
fs.2.total-bytes     : 1073741824
fs.2.used-bytes      : 0
"""


def test_parsers():
    d = hoststats.parse_domstats(DOMSTATS)
    assert d["web1"]["block.0.wr.bytes"] == "2000000" and d["old1"]["state.state"] == "5"
    assert hoststats.parse_guestinfo(GUEST) == {"total_gb": 21.0, "free_gb": 16.0}   # vda1 counted once
    assert hoststats.parse_guestinfo("error") == {}


def _fake_virsh(uri, *args):
    if args[0] == "nodeinfo":
        return "CPU(s): 8\nCore(s) per socket: 4\nCPU socket(s): 1\nNUMA cell(s): 1\n"
    if args[0] == "nodememstats":
        return "total  : 16777216 KiB\nfree   : 8388608 KiB\nbuffers: 0 KiB\ncached : 0 KiB\n"
    return DOMSTATS


def test_collect_rates_and_samples(client, monkeypatch, tmp_root):
    _admin_session(client)
    _host(client, vm_storage_path=str(tmp_root), template_storage_path=str(tmp_root))
    monkeypatch.setattr(hoststats, "_virsh", _fake_virsh)
    monkeypatch.setattr(hoststats, "_guest", lambda uri, name: (True, {"total_gb": 40.0, "free_gb": 30.0}))
    with session_factory()() as db:
        host = db.get(KvmHost, 1)
        first = hoststats.collect(host)
        web = next(v for v in first["vms"] if v["name"] == "web1")
        assert web["online"] and web["cpu_pct"] is None and web["memory_used_mb"] == 512
        assert web["disk_capacity_gb"] == 40.0 and web["guest_disk"]["free_gb"] == 30.0
        old = next(v for v in first["vms"] if v["name"] == "old1")
        assert not old["online"] and old["state"] == "shut off" and first["vms_running"] == 1
        # 60 s later web1 used 30 s of CPU on 2 vCPUs (25%) and wrote 6 MB (100 kB/s).
        first["_counters"]["t"] -= 60
        first["_counters"]["vms"]["web1"] = {"cpu": 2000000000000 - 30 * 10**9, "rd": 1000000, "wr": 2000000 - 6000000}
        second = hoststats.collect(host, first)
        web = next(v for v in second["vms"] if v["name"] == "web1")
        assert abs(web["cpu_pct"] - 25.0) < 0.5 and abs(web["disk_write_bps"] - 100000) < 2000
        hoststats.collect_all(db)
        db.add(MetricSample(at=utcnow() - dt.timedelta(hours=25), kvm_host_id=1, vm="", data={}))
        db.commit()
        hoststats.collect_all(db)
        assert db.query(MetricSample).filter(MetricSample.vm == "web1").count() == 2
        assert db.query(MetricSample).filter(MetricSample.at < utcnow() - dt.timedelta(hours=24)).count() == 0


def _stats(db):
    db.merge(HostStats(kvm_host_id=1, collected_at=utcnow(), error="", stats={
        "cpus": 8, "cpu_pct": 12.5, "memory_total_mb": 16384, "memory_available_mb": 8192, "load": [0.5, 0.4, 0.3],
        "disk_io": {"read_bps": 10.0, "write_bps": 20.0}, "vms_running": 1, "vms_defined": 2,
        "storage": {"vm": {"total_gb": 100, "free_gb": 60}},
        "vms": [{"name": "dash1", "state": "running", "online": True, "responding": False, "vcpus": 2, "cpu_pct": 40.0,
                 "memory_mb": 2048, "memory_used_mb": 1024, "disk_capacity_gb": 40, "disk_allocated_gb": 6,
                 "disk_read_bps": 1.0, "disk_write_bps": 2.0, "guest_disk": {}}]}))
    for minutes, cpu in ((5, 10.0), (15, 30.0)):
        db.add(MetricSample(at=utcnow() - dt.timedelta(minutes=minutes), kvm_host_id=1, vm="", data={"cpu_pct": cpu}))
        db.add(MetricSample(at=utcnow() - dt.timedelta(minutes=minutes), kvm_host_id=1, vm="dash1",
                            data={"cpu_pct": cpu, "disk_write_bps": 5.0}))
    db.commit()


def test_dashboard_visibility(client):
    _admin_session(client)
    _host(client)
    g = {"name": "dash", "os": "rocky9", "roles": ["linux_server"], "count": 2}
    r = client.post("/api/environments", json={"name": "dashenv", "kvm_host_id": 1, "host_groups": [g]})
    assert r.status_code == 201, r.text
    with session_factory()() as db:
        _stats(db)
    d = client.get("/api/dashboard").json()
    assert d["is_admin"] and [h["name"] for h in d["hosts"]] == ["kvm1"]
    h = d["hosts"][0]
    assert h["memory_used_mb"] == 8192 and h["cpu_pct"] == 12.5 and len(h["series"]["t"]) >= 1
    env = d["environments"][0]
    assert env["host_count"] == 2 and env["online"] == 1 and env["not_responding"] == 1
    assert [v["state"] for v in env["vms"]] == ["running", "missing"] and env["series"]["disk_write_bps"]
    # A viewer with no grants sees neither the host nor the environment; a deployer sees the host.
    for roles, hosts, envs in (({"viewer"}, 0, 0), ({"deployer"}, 1, 0)):
        client.app.dependency_overrides[current_principal] = lambda roles=roles: Principal(
            user_id=99, username="someone", display_name="", roles=roles)
        d = client.get("/api/dashboard").json()
        assert (len(d["hosts"]), len(d["environments"]), d["is_admin"]) == (hosts, envs, False)
        assert client.get("/api/dashboard/admin").status_code == 403
    client.app.dependency_overrides.clear()


def test_admin_health_and_release_check(client, monkeypatch):
    _admin_session(client)
    monkeypatch.setenv("LABAPE_VERSION", "v0.2.0-3-gabc1234")
    h = client.get("/api/dashboard/admin").json()
    names = [a["name"] for a in h["auth"]]
    assert "breakglass" in names and "directory" in names
    assert h["secrets"][0]["name"] == "builtin" and h["secrets"][0]["status"] == "ok"
    assert h["secrets"][1]["status"] == "error"          # no engine vault in the test setup
    assert {n["name"] for n in h["notifications"]} == {"smtp", "syslog"}
    assert h["version"]["current"] == "v0.2.0-3-gabc1234" and h["version"]["update_available"] is False

    def fake_get(url, timeout, headers):
        assert url == "https://api.github.com/repos/JesseMcWilliams/LABaPe/releases/latest"
        return httpx.Response(200, json={"tag_name": "v0.3.0", "html_url": "https://example.test/r"},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(version.httpx, "get", fake_get)
    v = client.post("/api/dashboard/admin/check-updates").json()
    assert v["latest"] == "v0.3.0" and v["update_available"] is True
    monkeypatch.setattr(version.httpx, "get", lambda url, **kw: httpx.Response(404, request=httpx.Request("GET", url)))
    v = client.post("/api/dashboard/admin/check-updates").json()
    assert v["error"] == "No releases published yet." and v["update_available"] is False
    assert version.newer("v0.10.0", "v0.9.9") and not version.newer("v0.2.0", "v0.2.0-5-gdeadbee")
