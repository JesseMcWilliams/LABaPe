"""A redeploy's plan preview (design §56.1): the plan summary, the job and its result."""
import importlib.util
import json
from pathlib import Path

from labape.db import session_factory
from labape.engine import planpreview
from labape.engine.runner import _finish, command_for
from labape.models import Environment, Job

from test_api import _admin_session, _host
from test_networks import _env, _group

ROOT = Path(__file__).resolve().parents[2]


def _summary_module():
    spec = importlib.util.spec_from_file_location("plan_summary", ROOT / "scripts" / "lib" / "plan_summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vm(key, actions, before=None, after=None, unknown=None, kind="vm_iso_direct"):
    return {"address": f'module.vm["{key}"].null_resource.{kind}[0]', "module_address": f'module.vm["{key}"]',
            "type": "null_resource", "name": kind, "index": 0,
            "change": {"actions": actions, "before": {"triggers": before} if before is not None else None,
                       "after": {"triggers": after} if after is not None else None,
                       "after_unknown": {"triggers": unknown} if unknown is not None else {}}}


TRIG = {"name": "app1", "libvirt_uri": "qemu:///system", "iso_host_path": "/iso/r9.iso", "bridge_device": "br1",
        "cpu_count": "2", "memory_mb": "2048", "disk_gb": "40", "vm_storage_path": "/data", "answer_file_md5": "aaa"}


def test_plan_summary_reads_each_vm():
    mod = _summary_module()
    plan = {"resource_changes": [
        _vm("app1", ["delete", "create"], TRIG, {**TRIG, "cpu_count": "4", "answer_file_md5": "bbb"}),
        _vm("app2", ["no-op"], {**TRIG, "name": "app2"}, {**TRIG, "name": "app2"}),
        _vm("app3", ["create"], None, {**TRIG, "name": "app3"}),
        _vm("app4", ["delete"], {**TRIG, "name": "app4"}, None),
        _vm("cl1", ["create", "delete"], {"name": "cl1", "seed_md5": "x"}, {"name": "cl1"}, {"seed_md5": True},
            kind="vm_from_template"),
        {"address": 'module.vm["app1"].local_file.kickstart[0]', "type": "local_file", "name": "kickstart",
         "change": {"actions": ["delete", "create"]}},
        {"address": "module.network.terraform_data.x", "type": "terraform_data", "name": "x",
         "change": {"actions": ["no-op"]}},
    ]}
    out = mod.summarize(plan)
    by = {v["name"]: v for v in out["vms"]}
    assert by["app1"] == {"name": "app1", "action": "replace", "changed": ["answer_file_md5", "cpu_count"]}
    assert by["app2"]["action"] == "none" and by["app3"]["action"] == "create" and by["app4"]["action"] == "delete"
    assert by["cl1"] == {"name": "cl1", "action": "replace", "changed": ["seed_md5"]}     # unknown until apply
    assert out["other_changes"] == 1                       # the answer file; no-ops don't count
    assert "aaa" not in json.dumps(out) and "bbb" not in json.dumps(out)      # names only, never values


def test_deploy_sh_stops_before_applying():
    text = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "--plan-only)" in text
    assert text.index('if [ -n "$PLAN_OUT" ]') < text.index("tofu apply -input=false")


def test_reasons_in_words():
    assert planpreview.reasons(["answer_file_md5", "seed_md5", "cpu_count", "odd"]) == \
        ["install answers", "CPU", "odd"]


def test_plan_job_previews_without_changing_the_environment(client, tmp_path):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json=_env("prev", [_group("pv")]))
    env_id, deploy_id = r.json()["environment"]["id"], r.json()["job_id"]
    assert client.get(f"/api/environments/{env_id}/plan").json() == {}
    assert client.post(f"/api/environments/{env_id}/plan").status_code == 409       # deploying: busy / not deployed
    with session_factory()() as db:
        job = db.get(Job, deploy_id)
        job.state = "running"
        db.commit()
        _finish(db, job, db.get(Environment, env_id), 0)
    r = client.post(f"/api/environments/{env_id}/plan")
    assert r.status_code == 200, r.text
    plan_id = r.json()["job_id"]
    with session_factory()() as db:
        job, env = db.get(Job, plan_id), db.get(Environment, env_id)
        assert job.type == planpreview.JOB_TYPE and env.status == "deployed"
        cmd = command_for(job, env, Path("/w"))
        assert cmd[1].endswith("deploy.sh") and cmd[-2:] == ["--plan-only", f"/w/{planpreview.PLAN_FILE}"]
        # Two hosts' parts, as for a spread environment.
        for i, vms in enumerate(([{"name": "pv1", "action": "replace", "changed": ["answer_file_md5"]}],
                                 [{"name": "pv2", "action": "none", "changed": []}])):
            (tmp_path / f"p{i}" / "engine").mkdir(parents=True)
            (tmp_path / f"p{i}" / "engine" / planpreview.PLAN_FILE).write_text(
                json.dumps({"vms": vms, "other_changes": i}))
        planpreview.store(db, job, [tmp_path / "p0" / "engine", tmp_path / "p1" / "engine"])
        job.state = "running"
        db.commit()
        _finish(db, job, env, 0)
        assert db.get(Environment, env_id).status == "deployed"
    got = client.get(f"/api/environments/{env_id}/plan").json()
    assert got["job_id"] == plan_id and got["state"] == "succeeded" and got["stale"] is False
    assert got["plan"]["vms"] == [{"name": "pv1", "action": "replace", "reasons": ["install answers"]},
                                  {"name": "pv2", "action": "none", "reasons": []}]
    assert got["plan"]["counts"]["replace"] == 1 and got["plan"]["other_changes"] == 1
    assert not (tmp_path / "p0" / "engine" / planpreview.PLAN_FILE).exists()
    # A failed plan leaves the environment as it was too; a deploy after the plan makes it stale.
    r = client.post(f"/api/environments/{env_id}/plan")
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        job.state = "running"
        db.commit()
        _finish(db, job, db.get(Environment, env_id), 1)
        assert db.get(Environment, env_id).status == "deployed"
    assert client.get(f"/api/environments/{env_id}/plan").json()["state"] == "failed"
    client.post(f"/api/environments/{env_id}/deploy")
    assert client.get(f"/api/environments/{env_id}/plan").json()["stale"] is True


def test_missing_part_plan_fails_the_merge(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="plan is missing"):
        planpreview.merge([tmp_path / "engine"])
