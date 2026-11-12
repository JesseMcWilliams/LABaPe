"""Batch A2 (plan: Planning_Next-Round-2026-11-08.md): the access account's rootless Podman on the Containers page."""
import contextlib
import io
import json
import subprocess
import sys
import types

import pytest

from labape.engine import hostadmin, hostcontainers
from labape.models import HostContainers, KvmHost, utcnow

from test_c1_containers import FakeSsh, c, data

INSPECT = json.dumps([{"Name": "/web", "Id": "a" * 64, "Image": "sha256:" + "b" * 64, "Config": {"Image": "nginx:1", "Labels": {}},
                       "State": {"Status": "running"}, "HostConfig": {}, "NetworkSettings": {}, "Mounts": [
                           {"Type": "volume", "Name": "data", "Destination": "/d", "RW": True}]}])


def run_script(monkeypatch, args, user_known=True):
    """Execute the collector script with a fake runtime: root has no containers, the user's Podman has one."""
    calls = []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        as_user = argv[:2] == ["runuser", "-u"]
        cmd = argv[argv.index("podman"):] if "podman" in argv else argv
        out = ""
        if cmd[:2] == ["podman", "version"]:
            out = "5.4.1\n"
        elif as_user and cmd[:3] == ["podman", "ps", "-a"]:
            out = "web\n"
        elif as_user and cmd[:2] == ["podman", "inspect"]:
            out = INSPECT
        elif as_user and cmd[:2] == ["podman", "images"] and "--format" in cmd:
            out = json.dumps({"ID": "b" * 12, "Repository": "nginx", "Tag": "1", "Size": "190MB", "CreatedSince": "1 week ago"}) + "\n"
        elif as_user and cmd[:2] == ["podman", "volume"]:
            out = json.dumps({"Name": "data", "Driver": "local", "Labels": ""}) + "\n"
        return subprocess.CompletedProcess(argv, 0, out, "")
    fake_pwd = types.SimpleNamespace(getpwnam=lambda n: types.SimpleNamespace(pw_uid=1001, pw_dir="/home/svc") if user_known else (_ for _ in ()).throw(KeyError(n)))
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/podman" if name == "podman" else None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(hostcontainers.SCRIPT, {"ARGS": args})
    return json.loads(buf.getvalue()), calls


def test_the_collector_reads_the_access_accounts_podman_as_that_user(monkeypatch):
    got, calls = run_script(monkeypatch, {"rootless_user": "svc"})
    assert got["runtime"] == "podman" and got["rootless"] == {"user": "svc", "uid": 1001, "version": "5.4.1"}
    assert [(x["name"], x["owner"], x["image"]) for x in got["containers"]] == [("web", "svc", "nginx:1")]
    assert got["images"][0]["owner"] == "svc" and got["volumes"][0]["owner"] == "svc"
    user_calls = [a for a in calls if a[:2] == ["runuser", "-u"]]
    assert user_calls and all(a[2] == "svc" and "XDG_RUNTIME_DIR=/run/user/1001" in a and "HOME=/home/svc" in a for a in user_calls)
    assert not any(a[:2] == ["runuser", "-u"] for a in calls[:0])


def test_no_rootless_user_or_an_unknown_one_reads_nothing_extra(monkeypatch):
    got, calls = run_script(monkeypatch, {"rootless_user": ""})
    assert "rootless" not in got and got["containers"] == [] and not any(a[:2] == ["runuser", "-u"] for a in calls)
    got, _ = run_script(monkeypatch, {"rootless_user": "ghost"}, user_known=False)
    assert "rootless" not in got


def test_who_is_read_as_rootless():
    h = KvmHost(name="k", ssh_user="svc")
    assert hostcontainers.rootless_user(h) == "svc"
    for bad in ("", "root", "Bad User", "a;b", None):
        h.ssh_user = bad
        assert hostcontainers.rootless_user(h) == ""


def rootless_row():
    d = data(c("db"), {**c("web", image="nginx:1"), "owner": "svc"}, c("job", state="exited", exit_code=0))
    d["containers"][1]["image_id"] = "sha256:" + "b" * 19
    d["images"] = [{"id": "111111111111", "repository": "nginx", "tag": "1", "size": "190MB", "created": "x", "dangling": False, "owner": ""},
                   {"id": "bbbbbbbbbbbb", "repository": "nginx", "tag": "1", "size": "190MB", "created": "x", "dangling": False, "owner": "svc"},
                   {"id": "cccccccccccc", "repository": "old", "tag": "1", "size": "50MB", "created": "x", "dangling": False, "owner": "svc"}]
    d["volumes"] = [{"name": "data", "driver": "local", "size": "1GB", "owner": "svc"}, {"name": "pgdata", "driver": "local", "size": "1GB", "owner": ""}]
    d["rootless"] = {"user": "svc", "uid": 1001, "version": "5.4.1"}
    return HostContainers(kvm_host_id=1, data=d, collected_at=utcnow())


def test_a_rootless_container_is_shown_but_never_acted_on_or_offered_for_clean_up():
    h = KvmHost(name="k", container_level="all")
    page = hostcontainers.build(rootless_row(), h)
    web = next(x for x in page["containers"] if x["name"] == "web")
    assert web["owner"] == "svc" and web["actionable"] is False and "rootless" in web["why_not"] and web["ops"] == []
    assert page["rootless"] == "svc"
    assert next(x for x in page["containers"] if x["name"] == "db")["actionable"] is True               # root's: unchanged
    by = {(i["id"], i["owner"]): i["in_use"] for i in page["images"]}
    assert by[("bbbbbbbbbbbb", "svc")] is True and by[("cccccccccccc", "svc")] is False               # per owner
    cand = hostcontainers.cleanup_candidates(page)
    assert [x["key"] for x in cand["containers"]] == ["job"]                                         # root's stopped one only
    assert cand["unused_images"] == [] and cand["dangling_images"] == []                              # the user's old image: not offered
    assert [x["key"] for x in cand["volumes"]] == ["pgdata"] or cand["volumes"] == []                # and not the user's volume
    assert all(x["key"] != "data" for x in cand["volumes"])
    with pytest.raises(hostadmin.AdminError, match="rootless"):
        hostcontainers.run_action(FakeSsh(), h, rootless_row(), "web", "stop")


def test_logs_and_stats_of_a_rootless_container_run_as_its_owner():
    row = rootless_row()
    ssh = FakeSsh(text=b"2026-11-12T10:00:00Z hello\n")
    out = hostcontainers.logs(ssh, row, "web", tail=10)
    assert out["lines"] == 1
    assert ssh.calls[-1].startswith("sudo -n runuser -u svc -- env XDG_RUNTIME_DIR=/run/user/1001 podman logs")
    ssh = FakeSsh(text=json.dumps({"CPUPerc": "1%", "MemUsage": "1MiB / 2GiB"}).encode())
    hostcontainers.live_stats(ssh, row, "web")
    assert ssh.calls[-1].startswith("sudo -n runuser -u svc -- env XDG_RUNTIME_DIR=/run/user/1001 podman stats")
    # a root container's commands are unchanged
    ssh = FakeSsh(text=b"x\n")
    hostcontainers.logs(ssh, row, "db", tail=5)
    assert ssh.calls[-1].startswith("sudo -n docker logs")
    row.data["rootless"] = {}
    with pytest.raises(hostadmin.AdminError, match="owner is unknown"):
        hostcontainers.logs(FakeSsh(), row, "web")


def test_the_newer_image_check_leaves_rootless_images_out():
    from labape.engine import containerupdates
    row = rootless_row()
    row.data["containers"] = [{**c("web", image="only-rootless:1"), "owner": "svc"}]
    assert containerupdates.update_refs(row) == []
