"""Batch O3 (design §28.3): VM power and start order, with a fake virsh."""
import subprocess

from labape.db import session_factory
from labape.engine import power
from labape.models import Environment, Job
from labape.security import Principal, current_principal

from test_api import _admin_session, _host


class FakeVirsh:
    """Domains with a power state; a started VM's agent answers after `boot_polls` pings;
    VMs in `stuck` ignore a clean shutdown; `no_agent` VMs have no agent channel."""

    def __init__(self, states, boot_polls=2, stuck=(), no_agent=()):
        self.states, self.boot_polls = dict(states), boot_polls
        self.stuck, self.no_agent = set(stuck), set(no_agent)
        self.pings, self.calls = {}, []

    def __call__(self, uri, *args, timeout=60):
        cmd, name = args[0], args[1]
        self.calls.append((cmd, name))
        ok = lambda out="": subprocess.CompletedProcess(args, 0, out, "")          # noqa: E731
        err = lambda msg: subprocess.CompletedProcess(args, 1, "", msg)             # noqa: E731
        if name not in self.states:
            return err(f"error: failed to get domain '{name}'")
        if cmd == "domstate":
            return ok(self.states[name] + "\n")
        if cmd == "start":
            if self.states[name] == "running":
                return err("error: Domain is already active")
            self.states[name], self.pings[name] = "running", 0
            return ok()
        if cmd == "qemu-agent-command":
            if name in self.no_agent:
                return err("error: argument unsupported: QEMU guest agent is not configured")
            if self.states[name] != "running":
                return err("error: Guest agent is not responding")
            self.pings[name] = self.pings.get(name, 0) + 1
            return ok("{}") if self.pings[name] > self.boot_polls else err("error: Guest agent is not responding")
        if cmd == "shutdown":
            if name not in self.stuck:
                self.states[name] = "shut off"
            return ok()
        if cmd == "destroy":
            self.states[name] = "shut off"
            return ok()
        return err(f"unexpected {cmd}")

    def order(self, cmd):
        return [n for c, n in self.calls if c == cmd]


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


ENV_SPEC = {"host_groups": [
    {"name": "app", "count": 2, "os": "rocky9", "roles": ["linux_server"]},
    {"name": "dc", "count": 1, "os": "windows_server_2022", "roles": ["domain_controller"], "start_delay_seconds": 30},
    {"name": "web", "count": 1, "os": "rocky9", "roles": ["linux_server"], "start_order": 3, "start_wait": "none"},
]}


def _env():
    return Environment(name="pw", kvm_host_id=1, spec=ENV_SPEC, created_by="t")


def test_plan_orders_servers_before_clients():
    steps = power.plan(_env())
    assert [(o, d, w, v) for o, d, w, v in steps] == [
        (1, 30, "agent", ["dc1"]), (2, 0, "agent", ["app1", "app2"]), (3, 0, "none", ["web1"])]
    assert power.plan(_env(), ["web1", "dc1"]) == [(1, 30, "agent", ["dc1"]), (3, 0, "none", ["web1"])]


def test_start_waits_for_each_group_then_delays():
    v = FakeVirsh({"dc1": "shut off", "app1": "shut off", "app2": "running", "web1": "shut off"})
    clock, log = Clock(), []
    power.start_all(power.Vms("qemu:///system", v), power.plan(_env()), log.append, 300, clock.sleep, clock.now)
    assert v.order("start") == ["dc1", "app1", "web1"]                      # app2 was already running
    # dc1's agent answered before anything of order 2 started, and the 30 s delay came next.
    first_app = v.calls.index(("start", "app1"))
    assert ("qemu-agent-command", "dc1") in v.calls[:first_app] and 30 in clock.sleeps
    assert ("qemu-agent-command", "web1") not in v.calls                    # start_wait: none
    assert any("dc1 is up" in m for m in log) and any("app2 is already running" in m for m in log)


def test_start_goes_on_when_an_agent_never_answers_or_is_missing():
    v = FakeVirsh({"dc1": "shut off", "app1": "shut off", "app2": "shut off", "web1": "shut off"},
                  boot_polls=10**6, no_agent={"app1"})
    clock, log = Clock(), []
    power.start_all(power.Vms("u", v), power.plan(_env()), log.append, 60, clock.sleep, clock.now)
    assert v.order("start") == ["dc1", "app1", "app2", "web1"]
    assert any("no guest agent channel" in m and "app1" in m for m in log)
    assert any("still no guest agent after 60 s: dc1" in m for m in log)


def test_shutdown_is_reverse_order_and_stuck_vms_fail_unless_forced():
    states = {"dc1": "running", "app1": "running", "app2": "running", "web1": "running"}
    v = FakeVirsh(states)
    clock, log = Clock(), []
    left = power.shutdown_all(power.Vms("u", v), power.plan(_env()), log.append, 300, False, clock.sleep, clock.now)
    assert left == [] and v.order("shutdown") == ["web1", "app1", "app2", "dc1"]

    v = FakeVirsh(states, stuck={"app2"})
    left = power.shutdown_all(power.Vms("u", v), power.plan(_env()), log.append, 60, False, clock.sleep, clock.now)
    assert left == ["app2"] and "dc1" not in v.order("shutdown")            # the DC stays up for its clients
    assert v.order("destroy") == []

    v = FakeVirsh(states, stuck={"app2"})
    left = power.shutdown_all(power.Vms("u", v), power.plan(_env()), log.append, 60, True, clock.sleep, clock.now)
    assert left == [] and v.order("destroy") == ["app2"] and v.order("shutdown")[-1] == "dc1"


def test_power_api_permissions_and_job(client, tmp_root, monkeypatch):
    _admin_session(client)
    _host(client)
    g = {"name": "dc", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template",
         "template": "rocky9-base"}
    r = client.post("/api/environments", json={"name": "powerenv", "kvm_host_id": 1, "host_groups": [g]})
    assert r.status_code == 201, r.text
    env_id = r.json()["environment"]["id"]
    with session_factory()() as db:
        for j in db.query(Job).filter(Job.environment_id == env_id):
            j.state = "succeeded"
        db.get(Environment, env_id).status = "deployed"
        db.commit()
    assert client.post(f"/api/environments/{env_id}/power", json={"action": "start", "vms": ["nope1"]}).status_code == 422
    r = client.put(f"/api/environments/{env_id}/start-order", json={"groups": [
        {"name": "dc", "start_order": 1, "start_wait": "none", "start_delay_seconds": 5}]})
    assert r.status_code == 200 and r.json()["host_groups"][0]["start_delay_seconds"] == 5
    vms = client.get(f"/api/environments/{env_id}/vms").json()
    assert vms["vms"][0]["name"] == "dc1" and vms["vms"][0]["start_order"] == 1 and vms["can_operate"]
    # The start-order keys never reach OpenTofu.
    from labape.engine.runner import profile_host_groups
    with session_factory()() as db:
        assert "start_order" not in profile_host_groups(db, db.get(Environment, env_id))[0]

    r = client.post(f"/api/environments/{env_id}/power", json={"action": "restart", "force_after_timeout": True})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert client.post(f"/api/environments/{env_id}/power", json={"action": "start"}).status_code == 409   # busy
    v = FakeVirsh({"dc1": "running"}, boot_polls=0)
    with session_factory()() as db:
        job = db.get(Job, job_id)
        job.log_path = str(tmp_root / f"power-{job_id}.log")
        clock = Clock()
        assert power.run_job(db, job, run=v, sleep=clock.sleep, now=clock.now) == 0
    assert v.order("shutdown") == ["dc1"] and v.order("start") == ["dc1"]
    log = open(tmp_root / f"power-{job_id}.log", encoding="utf-8").read()
    assert "restart all of powerenv" in log and "labape: done" in log
    assert client.get("/api/admin/audit?action=environment.power.restart").json()

    # A deployer who isn't an owner can see nothing to operate (404 for an environment they can't see).
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username="someone", display_name="", roles={"deployer"})
    assert client.post(f"/api/environments/{env_id}/power", json={"action": "start"}).status_code == 404
    client.app.dependency_overrides.clear()
