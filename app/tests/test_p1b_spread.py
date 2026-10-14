"""Batch P1b (design §45): one environment over several KVM hosts; each host's part runs its own
OpenTofu workspace there, then Ansible once from the environment's own host."""
import json

from labape import spread
from labape.db import session_factory
from labape.engine import power, runner
from labape.models import Environment, Job, KvmHost

from test_api import _admin_session, _host

DC = {"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"], "cpu_count": 2, "memory_mb": 4096}
WEB = {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "cpu_count": 2, "memory_mb": 2048}


def _env(**kw):
    return Environment(id=5, name="lab5", kvm_host_id=1, created_by="t",
                       spec={"host_groups": [dict(DC), {**WEB, "kvm_host_id": 2}]}, **kw)


def test_the_helpers():
    e = _env()
    assert spread.host_ids(e) == [1, 2] and spread.is_spread(e)
    assert spread.vm_host_ids(e) == {"dc1": 1, "web1": 2, "web2": 2}
    assert spread.workspace(e, 1) == "lab5" and spread.workspace(e, 2) == "lab5--h2"
    assert [g["name"] for g in spread.groups_on(e, 2)] == ["web"]
    one = Environment(id=6, name="lab6", kvm_host_id=1, created_by="t", spec={"host_groups": [dict(WEB)]})
    assert spread.host_ids(one) == [1] and not spread.is_spread(one)
    away = Environment(id=7, name="lab7", kvm_host_id=1, created_by="t",
                       spec={"host_groups": [{**WEB, "kvm_host_id": 2}]})
    assert spread.host_ids(away) == [2] and spread.is_spread(away)     # the home only runs Ansible


def test_create_keeps_a_group_on_its_host_and_names_are_per_host(client):
    _admin_session(client)
    a = _host(client, name="kvm1")
    b = _host(client, name="kvm2")
    r = client.post("/api/environments", json={"name": "spread1", "kvm_host_id": a, "host_groups": [
        {**WEB, "count": 1, "kvm_host_id": b}, {"name": "app", "os": "rocky9", "roles": ["linux_server"],
                                                 "kvm_host_id": a}]})
    assert r.status_code == 201, r.text
    groups = r.json()["environment"]["spec"]["host_groups"]
    assert groups[0]["kvm_host_id"] == b and "kvm_host_id" not in groups[1]       # the home drops it
    # web1 is taken on kvm2, not on kvm1.
    r = client.post("/api/environments", json={"name": "other1", "kvm_host_id": b, "host_groups": [
        {**WEB, "count": 1}]})
    assert r.status_code == 422 and "web1" in r.text
    r = client.post("/api/environments", json={"name": "other2", "kvm_host_id": a, "host_groups": [
        {**WEB, "count": 1}]})
    assert r.status_code == 201, r.text
    # A group on a disabled host is refused.
    client.put(f"/api/hosts/{b}", json={"name": "kvm2", "enabled": False})
    r = client.post("/api/environments", json={"name": "other3", "kvm_host_id": a, "host_groups": [
        {**WEB, "name": "db", "count": 1, "kvm_host_id": b}]})
    assert r.status_code == 422 and "host_groups.0.kvm_host_id" in r.text


def _hosts_rows(db):
    for hid, name, uri in ((1, "kvm1", "qemu:///system"), (2, "kvm2", "qemu+ssh://labape@10.0.0.2/system")):
        db.merge(KvmHost(id=hid, name=name, libvirt_uri=uri, vm_storage_path=f"/data/{name}",
                         template_storage_path=f"/data/{name}/t", bridge="br1", concurrency_limit=2, enabled=True))
    db.commit()


def _engine(tmp_root):
    engine = tmp_root / "engine"
    for d in ("scripts", "tofu/environments", "tofu/backends/libvirt", "ansible"):
        (engine / d).mkdir(parents=True, exist_ok=True)
    cfg = tmp_root / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "environment.yml").write_text("vm_storage_path: /old\n")


def test_parts_of_a_spread_job(tmp_root, db_reset):
    _engine(tmp_root)
    with session_factory()() as db:
        _hosts_rows(db)
        e = _env()
        parts = runner.plan_parts(db, Job(type="environment.deploy"), e)
        assert [p.host_id for p in parts] == [1, 2, 1]
        assert parts[0].cmd[3] == "lab5" and "--no-ansible" in parts[0].cmd and "--hosts-out" in parts[0].cmd
        assert parts[1].cmd[3] == "lab5--h2" and parts[1].work.name == "engine-h2"
        assert [g["name"] for g in parts[1].groups] == ["web"] and parts[1].installers is False
        profile = (parts[1].work / "tofu" / "environments" / f"{runner.PROFILE}.tfvars").read_text()
        assert '"web"' in profile and '"dc"' not in profile and "kvm_host_id" not in profile
        final = parts[2]
        assert "--hosts-from" in final.cmd and "--no-ansible" not in final.cmd and final.work.name == "engine"
        # The parts' VMs are merged before Ansible.
        (parts[0].work / runner.HOSTS_PART).write_text(json.dumps({"dc1": {"ip_address": "10.0.0.5"}}))
        (parts[1].work / runner.HOSTS_PART).write_text(json.dumps({"web1": {}, "web2": {}}))
        final.before()
        assert sorted(json.loads((final.work / runner.HOSTS_MERGED).read_text())) == ["dc1", "web1", "web2"]

        destroy = runner.plan_parts(db, Job(type="environment.destroy"), e)
        assert [p.host_id for p in destroy] == [2, 1] and all(p.keep_going for p in destroy)
        assert destroy[0].cmd[3] == "lab5--h2" and destroy[0].cmd[-2:] == ["--yes", "--delete-workspace"]

        refresh = runner.plan_parts(db, Job(type="environment.refresh_addresses"), e)
        assert [p.host_id for p in refresh] == [1, 2, 1] and refresh[-1].cmd[-1] == "--no-ansible"

        one = Environment(id=6, name="lab6", kvm_host_id=1, created_by="t", spec={"host_groups": [dict(WEB)]})
        single = runner.plan_parts(db, Job(type="environment.deploy"), one)
        assert len(single) == 1 and "--hosts-out" not in single[0].cmd and single[0].groups is None


def test_a_missing_part_stops_the_merge(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="didn't finish"):
        runner.merge_hosts([tmp_path / "engine-h2" / runner.HOSTS_PART], tmp_path / "m.json")


def test_power_reaches_each_vms_host():
    calls = []

    def run(uri, *args, **kw):
        import subprocess
        calls.append((uri, args[0], args[1]))
        return subprocess.CompletedProcess(args, 0, "running", "")
    vms = power.Vms("qemu:///system", run, {"web1": "qemu+ssh://labape@10.0.0.2/system"})
    vms.state("dc1")
    vms.state("web1")
    assert calls == [("qemu:///system", "domstate", "dc1"), ("qemu+ssh://labape@10.0.0.2/system", "domstate", "web1")]
