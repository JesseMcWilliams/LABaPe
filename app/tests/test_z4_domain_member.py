"""D184 (question 184): a host group can stay out of the environment's domain."""
import pytest
import yaml
from pydantic import ValidationError

from labape import domain_member
from labape.api.environments import HostGroup
from labape.models import Environment

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 2, "domain_member": False},
                        {"name": "db", "os": "rocky9", "roles": ["linux_server"]}]}


def test_standalone_vms_and_their_host_vars(tmp_path):
    e = Environment(name="lab7", status="deployed", kvm_host_id=1, created_by="admin", spec=SPEC)
    assert domain_member.standalone_vms(e) == ["web1", "web2"]
    assert domain_member.write_host_vars(e, tmp_path) == 2
    hv = tmp_path / "ansible" / "inventory" / "host_vars"
    assert yaml.safe_load((hv / "web1.yml").read_text()) == {"labape_domain_member": False}
    assert not (hv / "db1.yml").exists() and not (hv / "dc1.yml").exists()


def test_a_domain_controller_cannot_be_standalone():
    base = {"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]}
    assert HostGroup(**base).domain_member is True
    with pytest.raises(ValidationError):
        HostGroup(**base, domain_member=False)
    assert HostGroup(name="web", os="rocky9", roles=["linux_server"], domain_member=False).domain_member is False
