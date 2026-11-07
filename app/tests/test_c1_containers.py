"""Batch C1 (plan: Planning_Container-Management.md): the Containers page of a host, read-only."""
import datetime as dt
import subprocess

from labape.db import session_factory
from labape.engine import hostadmin, hostcontainers, stackmon
from labape.models import HostContainers, KvmHost, utcnow

from test_api import _admin_session, _host


def c(name, **kw):
    base = {"name": name, "id": "abc123", "image": "nginx:1", "image_id": "sha256:111111111111", "project": "web", "service": name,
            "state": "running", "health": "", "exit_code": 0, "oom": False, "restarts": 0, "ports": [], "mounts": [],
            "networks": ["bridge"], "stats": None}
    base.update(kw)
    return base


def data(*containers, **kw):
    return {"runtime": "docker", "version": "27.0", "containers": list(containers),
            "images": [{"id": "111111111111", "repository": "nginx", "tag": "1", "size": "190MB", "created": "2 weeks ago", "dangling": False},
                       {"id": "222222222222", "repository": "<none>", "tag": "<none>", "size": "5MB", "created": "1 year ago", "dangling": True}],
            "volumes": [{"name": "pgdata", "driver": "local", "size": "1.2GB"}, {"name": "old", "driver": "local", "size": None}],
            "restart_seen": {}, **kw}


def test_what_needs_a_look():
    assert hostcontainers.attention(c("a")) == ""
    assert hostcontainers.attention(c("a", state="exited", exit_code=0)) == ""            # finished
    assert hostcontainers.attention(c("a", state="exited", exit_code=143)) == ""          # stopped on purpose
    assert hostcontainers.attention(c("a", state="exited", exit_code=1)) == "exited with code 1"
    assert hostcontainers.attention(c("a", state="exited", exit_code=137, oom=True)) == "ran out of memory and was killed"
    assert hostcontainers.attention(c("a", state="restarting")) == "is restarting"
    assert hostcontainers.attention(c("a", health="unhealthy")) == "is unhealthy"
    assert hostcontainers.attention(c("a", state="exited", exit_code=0, health="unhealthy")) == ""        # stale health of a stopped one


def test_the_page_marks_the_stacks_own_containers_and_what_uses_what():
    row = HostContainers(kvm_host_id=1, data=data(
        c("db", mounts=[{"type": "volume", "source": "pgdata", "target": "/var/lib/postgresql", "rw": True}]),
        c("labape-api", project="labape"), c("job", state="exited", exit_code=2)), collected_at=utcnow())
    page = hostcontainers.build(row)
    assert [x["stack_own"] for x in page["containers"]] == [False, True, False]
    assert page["summary"] == {"total": 3, "running": 2, "attention": 1}
    assert [(i["id"], i["in_use"], i["dangling"]) for i in page["images"]] == [("111111111111", True, False), ("222222222222", False, True)]
    assert [(v["name"], v["used_by"]) for v in page["volumes"]] == [("pgdata", ["db"]), ("old", [])]
    assert hostcontainers.build(None)["runtime"] == "" and hostcontainers.summary(None)["total"] == 0


def test_secrets_in_logs_are_masked():
    text = "DB_PASSWORD=hunter2 ok\ntoken: abc123\napi_key = \"x y\"\nplain line\nSECRET_KEY='s3'"
    out = hostcontainers.mask_secrets(text)
    assert "hunter2" not in out and "abc123" not in out and "x y" not in out and "s3" not in out
    assert "DB_PASSWORD=***" in out and "plain line" in out


class FakeSsh:
    def __init__(self, payload=None, text=b"", rc=0):
        self.payload, self.text, self.rc, self.calls = payload, text, rc, []

    def python(self, script, args, sudo=True, timeout=120):
        self.calls.append(("python", args))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def run(self, command, input=None, timeout=60):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.rc, self.text, b"")


def _ready_host(client):
    host_id = _host(client)
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        h.access_status, h.ssh_address, h.ssh_user = "ready", "kvm1.lab", "localadmin01"
        db.commit()
    return host_id


def test_collecting_remembers_restarts_and_keeps_volume_sizes(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    first = data(c("db", restarts=1))
    second = data(c("db", restarts=2))
    second["volumes"] = [{"name": "pgdata", "driver": "local", "size": None}]
    sshs = iter([FakeSsh(first), FakeSsh(second)])
    monkeypatch.setattr(hostadmin, "for_host", lambda h: next(sshs))
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        t0 = utcnow()
        hostcontainers.collect(db, h, t0)
        row = hostcontainers.collect(db, h, t0 + dt.timedelta(minutes=5))
        assert row.data["restart_seen"]["db"] == (t0 + dt.timedelta(minutes=5)).isoformat()
        assert row.data["volumes"][0]["size"] == "1.2GB"                 # the hourly size is kept between reads
        assert hostcontainers.collect_due(db, h, t0 + dt.timedelta(minutes=6)) is None     # not due again yet
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh(hostadmin.AdminError("docker: permission denied")))
    with session_factory()() as db:
        row = hostcontainers.collect(db, db.get(KvmHost, host_id), utcnow())
        assert "permission denied" in row.error and row.data["containers"]        # the last good reading stays


def test_alerts_skip_the_stacks_own_and_can_be_switched_off(client):
    _admin_session(client)
    host_id = _ready_host(client)
    now = utcnow()
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        db.add(HostContainers(kvm_host_id=host_id, collected_at=now, data=data(
            c("web1", state="exited", exit_code=1), c("labape-api", project="labape", state="exited", exit_code=1),
            c("db", restarts=3), restart_seen={"db": now.isoformat()})))
        db.commit()
        row = db.get(HostContainers, host_id)
        got = hostcontainers.alerts(h, row, now, stack=True)
        assert [(lv, m) for lv, m, _ in got] == [("warning", "container web1 exited with code 1"),
                                                ("warning", "container db restarted (restart count 3)")]
        assert len(hostcontainers.alerts(h, row, now, stack=False)) == 3
        found = stackmon.evaluate(db, now)
        assert any("container web1 exited with code 1" in m for _, _, m in found.values())
        h.container_alerts = False
        assert hostcontainers.alerts(h, row, now, stack=False) == []


def test_the_containers_api(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=data(c("db"))))
        db.commit()
    ssh = FakeSsh(text=b"2026-11-06T10:00:00Z DB_PASSWORD=hunter2 started\n2026-11-06T10:00:01Z ready\n")
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    page = client.get(f"/api/hosts/{host_id}/containers").json()
    assert page["runtime"] == "docker" and page["alerts"] is True and page["containers"][0]["name"] == "db"
    assert client.get(f"/api/hosts/{host_id}/admin").json()["containers"] == {"runtime": "docker", "total": 1, "attention": 0}
    post = lambda path, **b: client.post(f"/api/hosts/{host_id}/containers/{path}", json=b)         # noqa: E731
    r = post("logs", name="db", tail=50, since="10m").json()
    assert "hunter2" not in r["text"] and "DB_PASSWORD=***" in r["text"] and r["lines"] == 2
    assert "sudo -n docker logs --timestamps --tail 50 --since 10m db" in ssh.calls[-1]
    assert post("logs", name="db", filter="ready").json()["lines"] == 1
    assert post("logs", name="nope").status_code == 422                      # not a container of this host
    assert post("logs", name="db; reboot").status_code == 422
    assert post("logs", name="db", since="a week").status_code == 422
    assert post("logs", name="db", tail=999999).status_code == 422
    actions = [a["action"] for a in client.get("/api/admin/audit").json()]
    assert actions.count("host.container.logs") >= 3
    ssh.text = b'{"CPUPerc":"1.5%","MemUsage":"20MiB / 2GiB","MemPerc":"1%","PIDs":"4","NetIO":"1kB / 2kB","BlockIO":"0B / 0B"}\n'
    assert post("stats", name="db").json()["cpu"] == "1.5%"
    assert post("alerts", enabled=False).json() == {"alerts": False}
    assert client.get(f"/api/hosts/{host_id}/containers").json()["alerts"] is False
