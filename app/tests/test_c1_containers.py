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
    post("logs", name="db", since="2d")
    assert "--since 48h" in ssh.calls[-1]                                    # the runtimes take no "d"
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


# --- batch C2: the guarded actions ---------------------------------------------------------------------

def host(level="off", known=(), at=None):
    return KvmHost(name="h", libvirt_uri="x", container_level=level, container_known=list(known), container_level_at=at)


def test_who_may_be_acted_on():
    db_, web, api = c("db"), c("web", created="2026-11-07T00:00:00.1Z"), c("labape-api", project="labape")
    assert hostcontainers.actionable(host("off"), db_) == (False, "actions are off for this host")
    assert hostcontainers.actionable(host("known", ["db"]), db_) == (True, "")
    assert hostcontainers.actionable(host("known", ["db"]), web) == (False, "not marked known")
    since = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
    assert hostcontainers.actionable(host("new", [], since), web)[0] is True                      # created after the level was set
    assert hostcontainers.actionable(host("new", [], since), c("old", created="2026-10-01T00:00:00Z"))[0] is False
    assert hostcontainers.actionable(host("new", ["old"], since), c("old", created="2026-10-01T00:00:00Z"))[0] is True
    assert hostcontainers.actionable(host("all"), db_) == (True, "")
    for level in hostcontainers.LEVELS:
        assert hostcontainers.actionable(host(level, ["labape-api"]), api) == (False, "part of this LABaPe stack")


def test_which_actions_fit_the_state_and_the_scripts():
    run = c("web", compose_dir="/srv/web", compose_files="/srv/web/compose.yaml,/srv/web/extra.yaml")
    assert hostcontainers.ops_for("docker", run) == ["stop", "restart", "recreate"]
    assert hostcontainers.ops_for("docker", c("web", state="exited", project="")) == ["start", "restart"]
    assert hostcontainers.ops_for("podman", run) == ["stop", "restart"]                      # recreate needs docker compose
    assert hostcontainers.action_script("docker", run, "restart") == "docker restart web"
    s = hostcontainers.action_script("docker", run, "recreate")
    assert s == ("set -e; cd /srv/web; docker compose -p web -f /srv/web/compose.yaml -f /srv/web/extra.yaml pull web; "
                 "docker compose -p web -f /srv/web/compose.yaml -f /srv/web/extra.yaml up -d --no-deps web")
    q = c("app", quadlet=True, unit="app.service")
    assert hostcontainers.action_script("podman", q, "restart") == "systemctl restart app.service"      # question 204
    for bad in (dict(unit="app.service; reboot"), dict(unit="../x")):
        try:
            hostcontainers.action_script("podman", c("app", quadlet=True, **bad), "stop")
        except hostadmin.AdminError:
            pass
        else:
            raise AssertionError(bad)
    for bad in (dict(compose_dir="/srv/web; reboot"), dict(project="web;x"), dict(compose_files="/a b")):
        try:
            hostcontainers.action_script("docker", c("web", **{"compose_dir": "/srv/web", **bad}), "recreate")
        except hostadmin.AdminError:
            pass
        else:
            raise AssertionError(bad)


def test_the_action_api_follows_the_level(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=data(
            c("web", compose_dir="/srv/web"), c("old", state="exited"), c("labape-api", project="labape"))))
        db.commit()
    ssh = FakeSsh(text=b"ok\n")
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    monkeypatch.setattr(hostcontainers, "collect", lambda db, h, now=None: None)
    act = lambda name, op: client.post(f"/api/hosts/{host_id}/containers/action", json={"name": name, "op": op})        # noqa: E731
    level = lambda lv: client.post(f"/api/hosts/{host_id}/containers/level", json={"level": lv})                      # noqa: E731
    mark = lambda name, known=True: client.post(f"/api/hosts/{host_id}/containers/known", json={"name": name, "known": known})   # noqa: E731
    page = client.get(f"/api/hosts/{host_id}/containers").json()
    assert page["level"] == "off" and not any(x["actionable"] for x in page["containers"])
    assert act("web", "restart").status_code == 422 and not ssh.calls                          # off: nothing
    assert level("everything").status_code == 422
    assert level("known").json() == {"level": "known", "known": []}
    assert act("web", "restart").status_code == 422                                           # not marked
    assert mark("nope").status_code == 422 and mark("web").json()["known"] == ["web"]
    page = client.get(f"/api/hosts/{host_id}/containers").json()
    assert {x["name"]: x["ops"] for x in page["containers"]}["web"] == ["stop", "restart", "recreate"]
    assert act("web", "restart").status_code == 200 and ssh.calls[-1].startswith("sudo -n bash -c") and "docker restart web" in ssh.calls[-1]
    assert act("web", "start").status_code == 422                                             # it is running
    assert act("web", "reboot").status_code == 422
    assert act("web", "recreate").status_code == 200 and "up -d --no-deps web" in ssh.calls[-1]
    assert level("all").status_code == 200
    assert act("old", "start").status_code == 200 and "docker start old" in ssh.calls[-1]
    assert act("labape-api", "restart").status_code == 422                                    # the stack's own, even at "all"
    assert mark("web", False).json()["known"] == []
    actions = [(a["action"], a["outcome"]) for a in client.get("/api/admin/audit").json() if a["action"].startswith("host.container.")]
    assert ("host.container.restart", "ok") in actions and ("host.container.restart", "failed") in actions
    assert any(a == "host.container.level" for a, _ in actions) and any(a == "host.container.known" for a, _ in actions)
    ssh.rc = 1
    assert act("old", "restart").status_code == 422


# --- batch C3: clean-up ----------------------------------------------------------------------------------

def test_sizes_are_read():
    assert hostcontainers.parse_size("190MB") == 190_000_000 and hostcontainers.parse_size("2.338kB") == 2338
    assert hostcontainers.parse_size("1.5GiB") == 1_610_612_736 and hostcontainers.parse_size("0B") == 0
    assert hostcontainers.parse_size(None) == 0 and hostcontainers.parse_size("n/a") == 0


def _cleanup_data():
    d = data(c("db", mounts=[{"type": "volume", "source": "pgdata", "target": "/x", "rw": True}]),
             c("job", state="exited", exit_code=1), c("labape-old", project="labape", state="exited"))
    d["images"].append({"id": "333333333333", "repository": "redis", "tag": "7", "size": "100MB", "created": "3 months ago", "dangling": False})
    d["volumes"] += [{"name": "labape_data", "driver": "local", "size": "1GB", "project": "labape"}]
    return d


def test_the_cleanup_candidates():
    row = HostContainers(kvm_host_id=1, data=_cleanup_data(), collected_at=utcnow())
    page = hostcontainers.build(row, host("all"))
    got = hostcontainers.cleanup_candidates(page)
    assert [x["key"] for x in got["containers"]] == ["job"]                        # the stack's own stopped one is not offered
    assert [x["key"] for x in got["dangling_images"]] == ["222222222222"]
    assert [x["key"] for x in got["unused_images"]] == ["redis:7"]                 # nginx:1 is used by db
    assert [x["key"] for x in got["volumes"]] == ["old"]                           # pgdata is used; the stack's volume is never offered
    assert got["totals"]["dangling_images"] == {"count": 1, "bytes": 5_000_000}
    assert hostcontainers.cleanup_candidates(hostcontainers.build(row, host("off")))["containers"] == []     # not allowed to touch it


def test_cleanup_scripts_are_checked():
    s = hostcontainers.cleanup_script("docker", "dangling_images", ["222222222222", "333333333333"])
    assert s.splitlines()[0] == "docker rmi 222222222222 >/dev/null 2>&1 && echo 'removed 222222222222' || echo 'kept 222222222222'"
    assert "docker volume rm old" in hostcontainers.cleanup_script("docker", "volumes", ["old"])
    for kind, bad in (("containers", "a; reboot"), ("dangling_images", "redis:7"), ("unused_images", "x y"), ("volumes", "-f")):
        try:
            hostcontainers.cleanup_script("docker", kind, [bad])
        except hostadmin.AdminError:
            pass
        else:
            raise AssertionError((kind, bad))


def test_the_cleanup_api(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=_cleanup_data()))
        db.commit()
    ssh = FakeSsh(text=b"removed 222222222222\n")
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    monkeypatch.setattr(hostcontainers, "collect", lambda db, h, now=None: None)
    url = f"/api/hosts/{host_id}/containers/cleanup"
    go = lambda kind, keys, confirm="": client.post(url, json={"kind": kind, "keys": keys, "confirm": confirm})        # noqa: E731
    assert client.get(url).json()["totals"]["dangling_images"]["count"] == 1
    assert go("dangling_images", ["222222222222"]).status_code == 422 and not ssh.calls         # the level is off
    client.post(f"/api/hosts/{host_id}/containers/level", json={"level": "known"})
    assert go("dangling_images", ["222222222222"]).json() == {"removed": ["222222222222"], "kept": []}
    assert "docker rmi 222222222222" in ssh.calls[-1]
    assert go("dangling_images", ["999999999999"]).status_code == 422                            # not a candidate
    assert go("unused_images", ["nginx:1"]).status_code == 422                                   # in use
    assert go("containers", ["job"]).status_code == 422                                          # known level: job isn't marked
    assert go("volumes", ["old"]).status_code == 422                                             # needs the word
    assert go("volumes", ["labape_data"], "delete").status_code == 422                           # the stack's own volume
    n = len(ssh.calls)
    assert go("volumes", ["old"], "delete").status_code == 200 and len(ssh.calls) == n + 1 and "docker volume rm old" in ssh.calls[-1]
    assert go("sandwiches", ["x"]).status_code == 422
    log = [a for a in client.get("/api/admin/audit").json() if a["action"] == "host.container.cleanup"]
    assert any(a["outcome"] == "ok" and a["detail"]["kind"] == "volumes" for a in log)
