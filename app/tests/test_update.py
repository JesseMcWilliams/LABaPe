"""Updates (design §23.2): choices, requests to the host agent, CLI commands the agent uses."""
import json
import os
import shutil

import pytest

from labape import cli, update, version
from labape.config import get_settings

from test_api import _admin_session

RELEASES = [
    {"tag": "v0.4.0-rc.1", "name": "v0.4.0-rc.1", "prerelease": True, "url": "https://x/rc1", "published_at": "2026-10-12"},
    {"tag": "v0.3.0", "name": "v0.3.0", "prerelease": False, "url": "https://x/030", "published_at": "2026-10-11"},
    {"tag": "v0.2.0", "name": "v0.2.0", "prerelease": False, "url": "https://x/020", "published_at": "2026-10-10"},
]


def _setup(monkeypatch, tmp_root, current="v0.2.0-5-gabc", installed=True):
    d = tmp_root / "update-dir"
    d.mkdir(exist_ok=True)
    for f in d.iterdir():
        f.unlink()
    monkeypatch.setattr(get_settings(), "update_dir", d)
    monkeypatch.setenv("LABAPE_VERSION", current)
    monkeypatch.setattr(update, "releases", lambda repo, timeout=15: RELEASES)
    if installed:
        (d / "agent.json").write_text("{}")
    return d


def test_choices_and_request(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _setup(monkeypatch, tmp_root)
    u = client.get("/api/admin/update").json()
    assert u["release"]["tag"] == "v0.3.0" and u["prerelease"]["tag"] == "v0.4.0-rc.1" and u["previous"] is None
    assert u["agent"]["installed"] and u["agent"]["state"] == "idle"
    assert client.post("/api/admin/update", json={"target": "v0.1.0"}).status_code == 422       # not on offer
    r = client.post("/api/admin/update", json={"target": "v0.3.0"})
    assert r.status_code == 200
    req = json.loads((d / "request.json").read_text())
    assert req["action"] == "update" and req["target"] == "v0.3.0"
    assert client.post("/api/admin/update", json={"target": "v0.3.0"}).status_code == 409       # already pending
    assert client.post("/api/admin/update/rollback").status_code == 409                        # nothing on standby


def test_rollback_and_no_agent(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _setup(monkeypatch, tmp_root, current="v0.3.0")
    u = client.get("/api/admin/update").json()
    assert u["release"] is None and u["prerelease"]["tag"] == "v0.4.0-rc.1"                     # only the preview is newer
    (d / "previous.json").write_text(json.dumps({"version": "v0.2.0", "db_revision": "0001_baseline", "ref": "abc"}))
    (d / "status.json").write_text(json.dumps({"state": "succeeded", "step": "done", "message": "updated"}))
    r = client.post("/api/admin/update/rollback")
    assert r.status_code == 200 and json.loads((d / "request.json").read_text())["action"] == "rollback"
    d2 = _setup(monkeypatch, tmp_root, installed=False)
    assert client.post("/api/admin/update", json={"target": "v0.3.0"}).status_code == 409       # no agent
    assert update.manual_commands("v0.3.0")[3] == "git checkout v0.3.0"
    assert not (d2 / "request.json").exists()


@pytest.mark.skipif(bool(os.environ.get("LABAPE_TEST_DATABASE_URL")) and not shutil.which("pg_dump"),
                    reason="the backup step runs pg_dump, which this machine doesn't have (PostgreSQL run)")
def test_cli_commands(client, capsys, tmp_root):
    _admin_session(client)
    assert cli.main(["maintenance", "on", "--reason", "agent test", "--source", "update"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert cli.main(["maintenance", "off", "--source", "update"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is False
    assert cli.main(["health"]) == 0
    assert json.loads(capsys.readouterr().out)["checks"]["database"]["ok"]
    assert cli.main(["health", "--deep"]) == 1                     # no worker heartbeat in tests
    capsys.readouterr()
    client.put("/api/admin/settings/backup", json={**client.get("/api/admin/settings").json()["backup"],
                                                   "directory": str(tmp_root / "cli-backups")})
    from labape import appsettings
    appsettings._cache.clear()
    assert cli.main(["backup", "--label", "pre-update-v0.2.0"]) == 0
    path = capsys.readouterr().out.strip()
    assert path.endswith("-pre-update-v0.2.0.dump")
    assert cli.main(["restore", path]) == 2                        # needs --yes
    assert version.newer("v0.4.0-rc.1", "v0.3.0")


def test_the_log_endpoint_returns_the_latest_runs_tail(client, monkeypatch, tmp_root):
    _admin_session(client)
    d = _setup(monkeypatch, tmp_root)
    assert client.get("/api/admin/update/log").json() == {"lines": []}                 # no log yet
    (d / "agent.log").write_bytes(b"=== 2026-11-12T09:19:00Z first run\nold line\n"
                                  b"=== 2026-11-12T23:57:09Z second run\n\x1b[31mred\x1b[0m line\r\nprogress 1\rprogress 2\n"
                                  + b"".join(b"filler %d\n" % i for i in range(300)) + b"[failed] restore\n")
    lines = client.get("/api/admin/update/log").json()["lines"]
    assert lines[-1] == "[failed] restore" and len(lines) == 200
    assert not any("first run" in x or "old line" in x for x in lines)               # only the latest run
    (d / "agent.log").write_bytes(b"=== run\n\x1b[31mred\x1b[0m line\r\nprogress 1\rprogress 2\n")
    assert client.get("/api/admin/update/log").json()["lines"] == ["=== run", "red line", "progress 1", "progress 2"]
    client.post("/api/auth/logout")
    assert client.get("/api/admin/update/log").status_code in (401, 403)
