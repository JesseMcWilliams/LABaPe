"""D186 (question 186): a local account can be bound to chosen VMs of its roles, not every VM of them."""
import importlib.util
from pathlib import Path

import pytest
import yaml

from labape import env_accounts as EA
from labape import vm_passwords
from labape.models import Environment

SPEC = {"host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 3}]}


def _env():
    return Environment(name="lab9", kvm_host_id=1, created_by="admin", status="deployed", spec=SPEC)


def test_vms_narrow_the_roles_and_default_to_all():
    e = _env()
    out = EA.validate(e, [{"name": "all1", "kind": "local", "hosts": ["linux_server"]},
                          {"name": "two", "kind": "local", "hosts": ["linux_server"], "vms": ["web3", "web1", ""]}])
    assert "vms" not in out[0] and out[1]["vms"] == ["web1", "web3"]
    assert EA.account_vms(e, out[0]) == ["web1", "web2", "web3"] and EA.account_vms(e, out[1]) == ["web1", "web3"]
    assert vm_passwords.local_account_vms(e, out[1]) == ["web1", "web3"]
    with pytest.raises(EA.AccountError, match="isn't a VM of the chosen roles"):
        EA.validate(e, [{"name": "bad", "kind": "local", "hosts": ["linux_server"], "vms": ["dc1"]}])


def test_the_manifest_carries_vms(tmp_path):
    e = _env()
    e.spec = {**SPEC, "accounts": EA.validate(e, [{"name": "two", "kind": "local", "hosts": ["linux_server"], "vms": ["web2"]}])}
    m = tmp_path / "ansible" / "directory-manifest.yml"
    m.parent.mkdir()
    EA.merge_manifest(m, e, "")
    assert yaml.safe_load(m.read_text())["local_users"][0]["vms"] == ["web2"]


def test_the_ansible_filter_honours_vms():
    path = Path(__file__).resolve().parents[2] / "ansible" / "filter_plugins" / "directory_objects.py"
    spec = importlib.util.spec_from_file_location("directory_objects", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    items = [{"name": "a", "hosts": ["linux_server"]}, {"name": "b", "hosts": ["linux_server"], "vms": ["web2"]}]
    names = lambda host: [i["name"] for i in mod.for_host_group(items, ["linux_server"], host)]  # noqa: E731
    assert names("web1") == ["a"] and names("web2") == ["a", "b"]
    assert [i["name"] for i in mod.for_host_group(items, ["linux_server"])] == ["a", "b"]        # no host: roles only
