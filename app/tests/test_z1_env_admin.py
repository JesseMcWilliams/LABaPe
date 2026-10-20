"""2026-10-20 round: an OS's disk floor is checked at creation; environment records can be deleted;
a Linux environment account can have passwordless sudo."""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from labape import ostargets
from labape.api.environments import HostGroup

TOFU = Path(__file__).resolve().parents[2] / "tofu" / "backends" / "libvirt" / "main.tf"


def _group(**kw):
    base = {"name": "w", "os": "windows_11", "roles": ["windows_workstation"], "disk_gb": 64}
    return HostGroup(**{**base, **kw})


def test_a_disk_below_the_os_floor_is_refused_at_creation():
    assert _group().disk_gb == 64
    with pytest.raises(ValidationError, match="below the 64 GB Windows 11 needs"):
        _group(disk_gb=60)
    assert _group(os="rocky9", roles=["linux_server"], disk_gb=20).disk_gb == 20     # no floor


def test_the_floors_match_the_opentofu_catalog():
    text = TOFU.read_text(encoding="utf-8")
    tofu = {k: int(v) for k, v in re.findall(r"^\s*(\w+)\s*=\s*\{[^\n]*min_disk_gb\s*=\s*(\d+)", text, re.M)}
    assert tofu == ostargets.MIN_DISK_GB


def test_a_destroyed_environments_record_can_be_deleted(client):
    from labape.db import session_factory
    from labape.models import Environment, Grant, Job, Secret, SecretPolicy
    from labape.security import put_secret
    from test_api import ENV, _admin_session, _host
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id})
    env_id, job_id = r.json()["environment"]["id"], r.json()["job_id"]
    r = client.delete(f"/api/environments/{env_id}")                  # queued deploy, not destroyed
    assert r.status_code == 409 and "destroy it first" in r.text and f"job {job_id}" in r.text
    client.post(f"/api/jobs/{job_id}/cancel")
    with session_factory()() as db:
        e = db.get(Environment, env_id)
        e.status = "destroyed"
        put_secret(db, f"environment/{env_id}/account/bob", "ThisIsMy_FAKE_Password6!")
        db.add(SecretPolicy(name=f"env{env_id}.account.bob", kind="env_account",
                            params={"environment_id": env_id, "account": "bob"}))
        db.commit()
    r = client.delete(f"/api/environments/{env_id}")
    assert r.status_code == 200, r.text
    assert r.json()["secrets"] >= 1 and r.json()["policies"] == 1 and r.json()["grants"] >= 1
    with session_factory()() as db:
        assert db.get(Environment, env_id) is None
        assert db.get(Job, job_id).environment_id is None                    # the job's row stays
        assert not db.query(Secret).filter(Secret.name.startswith(f"environment/{env_id}/")).count()
        assert not db.query(SecretPolicy).filter(SecretPolicy.name == f"env{env_id}.account.bob").count()
        assert not db.query(Grant).filter(Grant.object_type == "environment", Grant.object_id == env_id).count()
    assert client.get(f"/api/environments/{env_id}").status_code == 404
    assert "environment.delete" in [a["action"] for a in client.get("/api/admin/audit").json()]
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id})   # the name is free again
    assert r.status_code == 201, r.text


def test_passwordless_sudo_is_a_linux_local_accounts_option(client, tmp_path):
    import yaml
    from labape import env_accounts as EA
    from labape.db import session_factory
    from labape.models import Environment
    from test_api import _admin_session, _host
    _admin_session(client)
    _host(client)
    spec = {"host_groups": [{"name": "web", "os": "rocky9", "count": 1, "roles": ["linux_server"]},
                            {"name": "app", "os": "windows_server_2022", "count": 1, "roles": ["windows_server"]}]}
    with session_factory()() as db:
        e = Environment(name="sudo1", kvm_host_id=1, created_by="admin", status="deployed", spec=spec)
        db.add(e)
        db.commit()
        with pytest.raises(EA.AccountError, match="Linux hosts"):
            EA.validate(e, [{"name": "win", "kind": "local", "hosts": ["windows_server"], "sudo_nopasswd": True}])
        items = EA.set_accounts(db, e, [{"name": "inst", "kind": "local", "hosts": ["linux_server"], "sudo_nopasswd": True},
                                        {"name": "plain", "kind": "local", "hosts": ["linux_server"]}])
        assert items[0]["sudo_nopasswd"] is True and "sudo_nopasswd" not in items[1]
        m = tmp_path / "directory-manifest.yml"
        EA.merge_manifest(m, e, "")
        users = {u["name"]: u for u in yaml.safe_load(m.read_text())["local_users"]}
        assert users["inst"]["sudo_nopasswd"] is True and "sudo_nopasswd" not in users["plain"]
        env_id = e.id
    r = client.put(f"/api/environments/{env_id}/accounts",
                   json={"accounts": [{"name": "inst", "kind": "local", "hosts": ["linux_server"], "sudo_nopasswd": True}]})
    assert r.status_code == 200 and r.json()["accounts"][0]["sudo_nopasswd"] is True


def test_the_sudoers_task_writes_one_file_and_removes_it_when_empty():
    text = (Path(__file__).resolve().parents[2] / "ansible" / "roles" / "linux_common" / "tasks" / "main.yml").read_text(encoding="utf-8")
    assert "/etc/sudoers.d/90-labape-accounts" in text and "visudo -cf %s" in text and "state: absent" in text
