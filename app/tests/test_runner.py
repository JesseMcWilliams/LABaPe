"""Engine-directory preparation and the commands the runner builds."""
import yaml

from labape.engine.runner import PROFILE, command_for, prepare
from labape.models import Environment, Job, KvmHost


def test_prepare_writes_inputs(tmp_root):
    engine = tmp_root / "engine"
    for d in ("scripts", "tofu/environments", "tofu/backends/libvirt", "ansible"):
        (engine / d).mkdir(parents=True, exist_ok=True)
    cfg = tmp_root / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "environment.yml").write_text("vm_storage_path: /old\nnetwork: {network_address: 10.0.0.0}\n")
    (cfg / "software-manifest.yml").write_text("software: []\n")
    host = KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system", vm_storage_path="/data/VMs/X",
                   template_storage_path="/data/VMs/X/templates", bridge="br1", concurrency_limit=2, enabled=True)
    env = Environment(id=7, name="lab9", kvm_host_id=1, created_by="t",
                      spec={"static_ip_offset_start": 130,
                            "host_groups": [{"name": "app", "os": "rocky9", "roles": ["linux_server"]}]})
    work = prepare(env, host)
    env_yml = yaml.safe_load((work / "environment.yml").read_text())
    assert env_yml["vm_storage_path"] == "/data/VMs/X"
    assert env_yml["template_storage_path"] == "/data/VMs/X/templates"
    profile = (work / "tofu" / "environments" / f"{PROFILE}.tfvars").read_text()
    assert 'bridge_device = "br1"' in profile and "static_ip_offset_start = 130" in profile
    assert '"roles": ["linux_server"]' in profile
    assert 'backend "pg"' in (work / "tofu/backends/libvirt/labape_backend_override.tf").read_text()
    assert (work / "ansible" / "software-manifest.yml").is_file()
    cmd = command_for(Job(type="environment.destroy"), env, work)
    assert cmd[-2:] == ["--yes", "--delete-workspace"] and "lab9" in cmd
