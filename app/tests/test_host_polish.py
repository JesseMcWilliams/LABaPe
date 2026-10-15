"""Host polish (2026-10-13): the install copies only what the kvm profile reads and prunes
older copies; disabled hosts stay off the dashboard; SMART tools come with host setup."""
import subprocess
import sys
from types import SimpleNamespace

from labape.db import session_factory
from labape.engine import hostadmin, hostio
from labape.models import KvmHost

from test_api import _admin_session, _host


def test_prune_keeps_state_and_config_only(tmp_path):
    root = tmp_path / "labape-kvm"
    for rel in ("deploy/state/progress.json", "deploy/logs/install-1.log", "deploy/answers.web.json",
                "deploy/install-labape.py",
                "deploy/update-agent/install.sh", "secrets.vault.yml", "tofu/environment.yml",
                "tofu/modules/vm/main.tf", "ansible/software-manifest.yml", "ansible/roles/x/tasks/main.yml",
                "packer/rocky9.pkr.hcl", "iso/make.sh"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    r = subprocess.run([sys.executable, "-", str(root), *hostadmin.INSTALL_KEEP], input=hostadmin.PRUNE,
                       capture_output=True, text=True, check=True)
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert left == ["ansible/software-manifest.yml", "deploy/answers.web.json", "deploy/logs/install-1.log",
                    "deploy/state/progress.json",
                    "secrets.vault.yml", "tofu/environment.yml"]
    assert "removed packer" in r.stdout and "removed tofu/modules" in r.stdout
    # A host that was never installed: nothing to do.
    assert subprocess.run([sys.executable, "-", str(tmp_path / "none")], input=hostadmin.PRUNE,
                          capture_output=True, text=True).returncode == 0


def test_install_copies_only_the_installer_and_examples(client, tmp_path, monkeypatch):
    seen = {}

    class FakeSsh:
        def base(self):
            return ["ssh", "kvm2"]

        def run(self, command, input=None, timeout=60):
            seen.setdefault("runs", []).append(command)
            return subprocess.CompletedProcess(command, 0, b"removed packer\nremoved deploy/install-labape.py\n"
                                                         b"removed tofu/environment.example.yml\n", b"")

    class FakeTar:
        stdout = SimpleNamespace(close=lambda: None)
        returncode = 0

        def wait(self):
            return 0

    def fake_popen(argv, **kw):
        seen["tar"] = argv
        return FakeTar()

    def fake_run(argv, **kw):                       # the copy over SSH: stop the job there
        return subprocess.CompletedProcess(argv, 1, b"", b"stop here")

    engine = tmp_path / "engine"
    for rel in ("deploy/install-labape.py", "secrets.vault.example.yml", "tofu/environment.example.yml",
                "tofu/modules/vm/main.tf", "scripts/lib/labape_networks.py", "packer/x.pkr.hcl", "iso/make.sh"):
        (engine / rel).parent.mkdir(parents=True, exist_ok=True)
        (engine / rel).write_text("x")
    import labape.config
    monkeypatch.setattr(labape.config, "get_settings", lambda: SimpleNamespace(engine_dir=engine))
    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh())
    monkeypatch.setattr(hostio, "ensure_key", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(hostadmin.subprocess, "run", fake_run)
    with session_factory()() as db:
        h = KvmHost(name="kvm2", libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t",
                    bridge="br0", ssh_user="localadmin01", ssh_address="10.0.0.2", access_status="ready")
        db.add(h)
        db.commit()
        log = tmp_path / "job.log"
        job = SimpleNamespace(id=1, kvm_host_id=h.id, params={"answers": {}}, log_path=str(log))
        assert hostadmin.run_install(db, job, popen=fake_popen) == 1
    tar = seen["tar"]
    members = [a for a in tar if not a.startswith("-") and a not in ("tar",)][1:]   # after -C <engine>
    assert members and set(members) <= set(hostadmin.INSTALL_FILES)
    assert "--exclude=deploy/update-agent" in tar and not {"packer", "iso", "tofu", "ansible"} & set(members)
    assert seen["runs"][0].startswith("command -v tar") and "dnf install -y -q tar" in seen["runs"][0]
    assert seen["runs"][1].startswith("sudo -n python3 - /opt/labape-kvm deploy/state")
    assert "removed what older installs left in /opt/labape-kvm: packer\n" in log.read_text()   # not the copy's own files


def test_dashboard_leaves_out_disabled_hosts(client):
    _admin_session(client)
    _host(client)
    assert [h["name"] for h in client.get("/api/dashboard").json()["hosts"]]
    with session_factory()() as db:
        for h in db.query(KvmHost):
            h.enabled = False
        db.commit()
    assert client.get("/api/dashboard").json()["hosts"] == []


def test_setup_script_installs_smart_tools():
    assert "smartctl:smartmontools:smartmontools" in hostadmin.SETUP
    assert "sensors:lm-sensors:lm_sensors" in hostadmin.SETUP          # temperatures too (D1)
    # The script is %-formatted: it must still format cleanly.
    assert "smartctl" in hostadmin.SETUP % {"account": "localadmin01", "key": "ssh-ed25519 AAAA k"}


def test_deploy_starts_vms_that_are_off(tmp_path, monkeypatch, capsys):
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "start_stopped_vms", Path(__file__).resolve().parents[2] / "scripts" / "lib" / "start_stopped_vms.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    states = {"l2dc1": "shut off", "l2lin1": "shut off", "l2win1": "running"}
    started = []

    def fake_virsh(*args):
        if args[0] == "domstate":
            return subprocess.CompletedProcess(args, 0, states[args[1]] + "\n", "")
        started.append(args[1])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mod, "virsh", fake_virsh)
    hosts = tmp_path / "hosts.json"
    hosts.write_text(json.dumps({"l2lin1": {"roles": ["linux_server"]}, "l2win1": {"roles": []},
                                 "l2dc1": {"roles": ["domain_controller"]}}))
    monkeypatch.setattr(sys, "argv", ["x", str(hosts)])
    assert mod.main() == 0
    assert started == ["l2dc1", "l2lin1"]                     # the domain controller first; running ones left alone
    assert "l2dc1 was shut off; started it" in capsys.readouterr().err


def test_hosts_never_sleep():
    # Prepare/setup masks the sleep targets (a desktop's login screen suspends after 15 idle minutes).
    text = hostadmin.SETUP % {"account": "localadmin01", "key": "ssh-ed25519 AAAA k"}
    assert "systemctl mask $UNMASKED" in text and "suspend.target" in text
    w = hostadmin.warnings({"os": {"can_sleep": ["suspend.target"]}, "tools": {"smartctl": True}}, {}, None)
    assert any("can sleep" in x["message"] and x["level"] == "warning" for x in w)
    assert not any("can sleep" in x["message"] for x in hostadmin.warnings(
        {"os": {"can_sleep": []}, "tools": {"smartctl": True}}, {}, None))


def test_monitoring_tools_everywhere():
    """Question 173: SMART, sensors and ethtool on every host LABaPe sets up, sensors probed once."""
    from labape.engine import hostadmin
    import importlib.util
    import sys
    from pathlib import Path
    assert "ethtool:ethtool:ethtool" in hostadmin.SETUP and "sensors-detect --auto" in hostadmin.SETUP
    root = Path(__file__).resolve().parents[2] / "deploy"
    sys.path.insert(0, str(root))
    try:
        from labape_deploy import steps
    finally:
        sys.path.remove(str(root))
    for pkgs in (steps.APT_PACKAGES, steps.WEB_PACKAGES):
        assert {"smartmontools", "lm-sensors", "ethtool"} <= set(pkgs)
    assert {"smartmontools", "lm_sensors", "ethtool"} <= set(steps.RPM_PACKAGES)
