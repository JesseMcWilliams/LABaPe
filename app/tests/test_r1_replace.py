"""Batch R1 (plan: Planning_Disk-Health-Notifications.md): guided drive replacement on a degraded md array."""
import pytest

from labape.db import session_factory
from labape.engine import hostadmin, hostpools
from labape.models import HostFacts, KvmHost

from test_api import _admin_session, _host
from test_d1_pools import FakeSsh, _oz_facts


def with_new_drives():
    f = _oz_facts()
    f["disks"] += [{"name": "sdg", "path": "/dev/sdg", "type": "disk", "size": "3000", "model": "NEW-3TB", "serial": "NEWSER1"},
                   {"name": "sdh", "path": "/dev/sdh", "type": "disk", "size": "100", "model": "TINY", "serial": "SMALL1"},
                   {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": "4000", "model": "NVME", "serial": "NV1"}]
    return f


def test_the_plan_names_the_drive_to_pull_and_offers_big_enough_free_disks():
    (p,) = hostpools.build(with_new_drives())
    plan = p["replace"]
    assert plan["template"] == "/dev/sdb" and plan["partition"] == 1 and plan["need"] == 3000
    assert [(d["disk"], d["serial"]) for d in plan["pull"]] == [("/dev/sdd", "P8G8JSPR")]
    assert [(c["path"], c["serial"]) for c in plan["candidates"]] == [("/dev/sdg", "NEWSER1"), ("/dev/nvme0n1", "NV1")]    # not the small one
    assert plan["at_risk"] == ["/dev/sdb1"]


def test_no_plan_for_a_healthy_array_or_whole_disk_members():
    assert all(p["replace"] is None for p in hostpools.build(hostpools_healthy()))


def hostpools_healthy():
    from test_d1_pools import facts
    return facts()


def test_partition_names_follow_the_kernel():
    assert hostpools.part_name("/dev/sdg", 1) == "/dev/sdg1"
    assert hostpools.part_name("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"


def test_the_script_copies_the_table_without_ids_then_adds_the_partition():
    (p,) = hostpools.build(with_new_drives())
    s = hostpools.replace_script(p, "/dev/sdg")
    assert "sfdisk -d /dev/sdb | grep -v '^label-id:' | sed -E 's/, *uuid=[^,]+//' | sfdisk /dev/sdg" in s
    assert s.endswith("mdadm /dev/md10 --add /dev/sdg1") and "wipefs -n /dev/sdg" in s and "findmnt" in s
    assert "/dev/nvme0n1p1" in hostpools.replace_script(p, "/dev/nvme0n1")
    with pytest.raises(hostadmin.AdminError):
        hostpools.replace_script(p, "/dev/sdh")                                 # smaller than the drive it replaces
    with pytest.raises(hostadmin.AdminError):
        hostpools.replace_script(p, "/dev/sdb")                                 # a member
    with pytest.raises(hostadmin.AdminError):
        hostpools.replace_script(p, "/dev/sdg; reboot")


def test_the_replace_api_needs_the_serial_typed(client, monkeypatch):
    _admin_session(client)
    host_id = _host(client)
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        h.access_status, h.ssh_address, h.ssh_user = "ready", "oz.lab", "localadmin01"
        db.add(HostFacts(kvm_host_id=host_id, facts=with_new_drives(), smart={}))
        db.commit()
    fake = FakeSsh()
    monkeypatch.setattr(hostadmin, "for_host", lambda h: fake)
    monkeypatch.setattr(hostadmin, "collect_facts", lambda db, h, smart=False: None)
    assert client.get(f"/api/hosts/{host_id}/admin").json()["pools"][0]["replace"]["candidates"][0]["serial"] == "NEWSER1"
    post = lambda **b: client.post(f"/api/hosts/{host_id}/pools/replace", json={"pool": "md:md10", **b})      # noqa: E731
    assert post(device="/dev/sdg", confirm="").status_code == 422 and not fake.calls
    assert post(device="/dev/sdg", confirm="/dev/sdg").status_code == 422                                      # the serial, not the name
    assert post(device="/dev/sdh", confirm="SMALL1").status_code == 422
    assert post(device="/dev/sdd", confirm="P8G8JSPR").status_code == 422 and not fake.calls                  # the failed drive
    assert post(device="/dev/sdg", confirm="NEWSER1").status_code == 200
    assert "mdadm /dev/md10 --add /dev/sdg1" in fake.calls[-1] and fake.calls[-1].startswith("sudo -n bash -c")
    log = [a for a in client.get("/api/admin/audit").json() if a["action"] == "host.pool.replace" and a.get("outcome") != "failed"]
    assert log and log[0]["detail"]["serial"] == "NEWSER1"
