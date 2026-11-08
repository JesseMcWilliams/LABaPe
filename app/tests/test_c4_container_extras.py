"""Batch C4 (plan: Planning_Disk-Health-Notifications.md): the unused-images alert, a whole compose project,
and the weekly newer-image check."""
import datetime as dt

from labape.db import session_factory
from labape.engine import containerupdates as CU, hostadmin, hostcontainers, notifications
from labape.models import HostContainers, KvmHost, utcnow

from test_api import _admin_session
from test_c1_containers import FakeSsh, _ready_host, c, data


def big_images():
    return [{"id": "aaaaaaaaaaaa", "repository": "<none>", "tag": "<none>", "size": "30GB", "created": "1 year ago", "dangling": True},
            {"id": "bbbbbbbbbbbb", "repository": "old", "tag": "1", "size": "10GB", "created": "1 year ago", "dangling": False}]


def test_the_unused_images_alert_follows_the_limit():
    h = KvmHost(name="k", container_alerts=True, container_level="off")
    row = HostContainers(kvm_host_id=1, data=data(c("db"), images=big_images()), collected_at=utcnow())
    got = hostcontainers.alerts(h, row, utcnow(), False, reclaim_gb=20)
    assert [(lv, kind) for lv, _, kind in got] == [("warning", "reclaim")] and "40 GB" in got[0][1]
    assert hostcontainers.alerts(h, row, utcnow(), False, reclaim_gb=50) == []
    assert hostcontainers.alerts(h, row, utcnow(), False, reclaim_gb=0) == []             # off
    assert notifications.category("host1:reclaim:about # GB", "") == "container"


def test_verdicts_and_refs():
    raw = {"nginx:1": {"local": ["sha256:a"], "remote": "sha256:b", "error": ""},
           "redis:7": {"local": ["sha256:c"], "remote": "sha256:c", "error": ""},
           "mine:1": {"local": [], "remote": "", "error": "denied"}}
    assert CU.verdicts(raw) == {"nginx:1": "newer", "redis:7": "current", "mine:1": "unknown"}
    row = HostContainers(kvm_host_id=1, data=data(c("a", image="nginx:1"), c("b", image="sha256:111111111111"), c("d", image="x@sha256:abc"),
                                                  c("e", image="redis:7", state="exited"), c("f", image="nginx:1")), collected_at=utcnow())
    assert CU.update_refs(row) == ["nginx:1"]


def test_the_check_is_weekly_and_only_when_switched_on(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    ssh = FakeSsh(payload={"nginx:1": {"local": ["sha256:a"], "remote": "sha256:b", "error": ""}})
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    now = utcnow()
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=now, data=data(c("web", image="nginx:1"))))
        db.commit()
        h = db.get(KvmHost, host_id)
        CU.due(db, h, now)
        assert not ssh.calls                                                           # off by default
        h.container_updates = True
        db.commit()
        CU.due(db, h, now)
        CU.due(db, h, now + dt.timedelta(days=3))
        assert len(ssh.calls) == 1
        CU.due(db, h, now + dt.timedelta(days=8))
        assert len(ssh.calls) == 2
        page = hostcontainers.build(db.get(HostContainers, host_id), h)
        assert page["containers"][0]["image_update"] == "newer" and page["summary"]["newer_images"] == 1
        assert page["updates"]["enabled"] is True
        assert db.get(HostContainers, host_id).data["updates"]["at"]
    assert client.get(f"/api/hosts/{host_id}/containers").json()["updates"]["enabled"] is True


def test_collecting_keeps_the_last_check(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    fresh = data(c("web"))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh(payload=fresh))
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=data(c("web"), updates={"at": "2026-11-01T00:00:00+00:00", "images": {"nginx:1": "newer"}})))
        db.commit()
        hostcontainers.collect(db, db.get(KvmHost, host_id))
        assert db.get(HostContainers, host_id).data["updates"]["images"] == {"nginx:1": "newer"}


def test_a_project_update_runs_one_compose_pull_and_up(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    ssh = FakeSsh(text=b"done\n")
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    monkeypatch.setattr(hostcontainers, "collect", lambda db, h, now=None: None)
    web = dict(compose_dir="/srv/web", compose_files="/srv/web/compose.yaml")
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=data(
            c("web-1", **web), c("db-1", service="db", **web), c("other", project="solo", compose_dir="/srv/solo"), c("labape-api", project="labape", compose_dir="/x"))))
        db.commit()
    post = lambda project: client.post(f"/api/hosts/{host_id}/containers/project", json={"project": project})        # noqa: E731
    assert post("web").status_code == 422 and not ssh.calls                           # level off
    client.post(f"/api/hosts/{host_id}/containers/level", json={"level": "all"})
    assert post("labape").status_code == 422                                         # the stack's own
    assert post("nope").status_code == 422
    assert post("web").status_code == 200
    cmd = ssh.calls[-1]
    assert "cd /srv/web" in cmd and "docker compose -p web -f /srv/web/compose.yaml pull;" in cmd and cmd.rstrip("'").endswith("up -d")
    log = [a for a in client.get("/api/admin/audit").json() if a["action"] == "host.container.project"]
    assert log[0]["detail"]["project"] == "web" or log[1]["detail"]["project"] == "web"


def test_the_switch_and_check_endpoints(client, monkeypatch):
    _admin_session(client)
    host_id = _ready_host(client)
    ssh = FakeSsh(payload={})
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    with session_factory()() as db:
        db.add(HostContainers(kvm_host_id=host_id, collected_at=utcnow(), data=data(c("web"))))
        db.commit()
    assert client.post(f"/api/hosts/{host_id}/containers/updates/switch", json={"enabled": True}).json() == {"enabled": True}
    assert client.post(f"/api/hosts/{host_id}/containers/updates/check").json()["updates"]["at"]
    actions = {a["action"] for a in client.get("/api/admin/audit").json()}
    assert {"host.container.updates_switch", "host.container.updates_check"} <= actions
