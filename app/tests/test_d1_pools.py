"""Batch D1 (design §53.4): drive pools of a host."""
import subprocess

import pytest

from labape.db import session_factory
from labape.engine import hostadmin, hostpools
from labape.models import HostFacts, KvmHost

from test_api import _admin_session, _host

ZPOOL = """  pool: tank
 state: DEGRADED
status: One or more devices could not be opened.
  scan: resilver in progress since Mon Oct 12 09:00:00 2026
	1.20T scanned at 400M/s, 600G issued at 200M/s, 3.00T total
	300G resilvered, 34.50% done, 03:10:00 to go
config:

	NAME                    STATE     READ WRITE CKSUM
	tank                    DEGRADED     0     0     0
	  mirror-0              DEGRADED     0     0     0
	    /dev/sda            ONLINE       0     0     0
	    /dev/sdb            UNAVAIL      0     0     0
	  mirror-1              ONLINE       0     0     0
	    /dev/sdc            ONLINE       0     0     2
	    /dev/sdd            ONLINE       0     0     0

errors: No known data errors
"""

DETAIL = """/dev/md0:
           Version : 1.2
        Raid Level : raid1
             State : clean, degraded
              Name : host:0  (local to host host)
              UUID : 1b2c3d4e:5f60718a:9b0c1d2e:3f405162
"""


def facts(**kw):
    base = {
        "md": {"md0": {"level": "raid1", "array_state": "active", "sync_action": "idle", "sync_completed": "none",
                       "degraded": "0", "raid_disks": "2", "sync_speed": "none", "size": "1953525168", "detail": DETAIL,
                       "members": {"sda1": {"state": "in_sync", "slot": "0", "errors": "0"},
                                   "sdb1": {"state": "in_sync", "slot": "1", "errors": "0"}}}},
        "filesystems": [{"device": "/dev/md0", "type": "ext4", "size": 1000, "used": 400, "avail": 600, "mount": "/data"}],
        "disks": [{"name": "sda", "path": "/dev/sda", "type": "disk", "size": "1000", "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "size": "999", "fstype": "linux_raid_member"}]},
                  {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": "2000", "model": "SPARE-1", "fstype": None, "mountpoints": [None]},
                  {"name": "sdd", "path": "/dev/sdd", "type": "disk", "size": "2000", "fstype": "ext4", "mountpoints": ["/mnt"]}],
        "zfs": [], "lvm": {},
    }
    base.update(kw)
    return base


def test_a_healthy_mirror():
    (p,) = hostpools.build(facts())
    assert (p["id"], p["type"], p["level"], p["state"]) == ("md:md0", "mdadm", "raid1", "healthy")
    assert p["mounts"] == ["/data"] and (p["used"], p["free"]) == (400, 600) and p["size"] == 1953525168 * 512
    assert p["uuid"] == "1b2c3d4e:5f60718a:9b0c1d2e:3f405162"
    assert [m["role"] for m in p["members"]] == ["active", "active"]
    assert set(p["actions"]) == {"check", "stop-sync", "resync", "fail-remove", "add-spare"}
    assert hostpools.warnings(facts()) == []


def test_a_degraded_then_rebuilding_array():
    f = facts()
    f["md"]["md0"].update(degraded="1", members={"sda1": {"state": "in_sync", "slot": "0", "errors": "0"},
                                                  "sdb1": {"state": "faulty", "slot": "1", "errors": "9"}})
    (p,) = hostpools.build(f)
    assert p["state"] == "degraded" and p["errors"] == 9 and p["members"][1]["role"] == "faulty"
    assert hostpools.warnings(f) == [{"level": "error", "message": "RAID array md0 is degraded (1 of 2 drives missing)"}]
    f["md"]["md0"].update(sync_action="recover", sync_completed="500 / 2000")
    (p,) = hostpools.build(f)
    assert p["state"] == "rebuilding" and p["sync"]["percent"] == 25.0
    assert hostpools.warnings(f)[0]["message"] == "RAID array md0 is rebuilding (25% done)"
    f["md"]["md0"].update(degraded="0", sync_action="check", sync_completed="1 / 4")
    assert hostpools.build(f)[0]["state"] == "scrubbing"
    f["md"]["md0"].update(array_state="inactive")
    assert hostpools.build(f)[0]["state"] == "failed"


def test_zfs_status_is_parsed():
    st = hostpools.parse_zpool_status(ZPOOL)
    assert st["state"] == "DEGRADED" and st["resilvering"] and st["scan_percent"] == 34.5
    assert [(m["name"], m["state"], m["cksum"], m["depth"]) for m in st["members"]][:4] == [
        ("tank", "DEGRADED", 0, 0), ("mirror-0", "DEGRADED", 0, 1), ("/dev/sda", "ONLINE", 0, 2), ("/dev/sdb", "UNAVAIL", 0, 2)]
    f = facts(md={}, zfs=[{"name": "tank", "size": "3000", "alloc": "1000", "free": "2000", "health": "DEGRADED"}],
              zpool_status={"tank": ZPOOL})
    (p,) = hostpools.build(f)
    assert p["state"] == "rebuilding" and p["errors"] == 2 and p["sync"]["action"] == "resilver"
    assert {"scrub", "offline", "add-spare"} <= set(p["actions"])


def test_lvm_is_listed_and_read_only():
    f = facts(md={}, lvm={"vgs": [{"vg_name": "vg0", "vg_attr": "wz--n-", "vg_size": "100", "vg_free": "40", "vg_uuid": "u"}],
                          "pvs": [{"pv_name": "/dev/sde1", "vg_name": "vg0", "pv_attr": "a--"}],
                          "lvs": [{"vg_name": "vg0", "lv_name": "root", "lv_size": "60"}]})
    (p,) = hostpools.build(f)
    assert p["type"] == "lvm" and p["state"] == "healthy" and p["used"] == 60 and p["actions"] == []
    f["lvm"]["pvs"][0]["pv_attr"] = "a-m"
    assert hostpools.build(f)[0]["state"] == "degraded"
    with pytest.raises(hostadmin.AdminError):
        hostpools.action_script(hostpools.build(f)[0], "check", None)


def test_unused_disks_leave_out_what_is_in_use():
    assert [d["path"] for d in hostpools.unused_disks(facts())] == ["/dev/sdc"]


def test_action_scripts_are_checked():
    (p,) = hostpools.build(facts())
    assert hostpools.action_script(p, "check", None) == "echo check > /sys/block/md0/md/sync_action"
    assert "mdadm /dev/md0 --fail /dev/sdb1" in hostpools.action_script(p, "fail-remove", "/dev/sdb1")
    for bad in ("sdb1", "/dev/sdb1; reboot", "/dev/sdb1 /dev/sdc", "$(id)", ""):
        with pytest.raises(hostadmin.AdminError):
            hostpools.action_script(p, "add-spare", bad)
    with pytest.raises(hostadmin.AdminError):
        hostpools.action_script(p, "destroy", None)
    evil = dict(p, name="md0; reboot")
    with pytest.raises(hostadmin.AdminError):
        hostpools.action_script(evil, "check", None)


class FakeSsh:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def run(self, command, input=None, timeout=60):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.rc, b"done\n", b"" if self.rc == 0 else b"nope")


def test_the_pool_action_api(client, monkeypatch):
    _admin_session(client)
    host_id = _host(client)
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        h.access_status, h.ssh_address, h.ssh_user = "ready", "kvm1.lab", "localadmin01"
        db.add(HostFacts(kvm_host_id=host_id, facts=facts(), smart={}))
        db.commit()
    fake = FakeSsh()
    monkeypatch.setattr(hostadmin, "for_host", lambda h: fake)
    monkeypatch.setattr(hostadmin, "collect_facts", lambda db, h, smart=False: None)
    view = client.get(f"/api/hosts/{host_id}/admin").json()
    assert view["pools"][0]["id"] == "md:md0" and view["pool_candidates"][0]["path"] == "/dev/sdc"
    post = lambda **b: client.post(f"/api/hosts/{host_id}/pools/action", json=b)         # noqa: E731
    assert post(pool="md:md0", op="check").status_code == 200
    assert "sync_action" in fake.calls[-1] and fake.calls[-1].startswith("sudo -n bash -c")
    assert post(pool="md:md9", op="check").status_code == 404
    assert post(pool="md:md0", op="fail-remove", device="/dev/sdz9").status_code == 422      # not in the pool
    assert post(pool="md:md0", op="add-spare", device="/dev/sdd").status_code == 422         # mounted
    assert post(pool="md:md0", op="add-spare", device="/dev/sdc").status_code == 200
    assert "--add /dev/sdc" in fake.calls[-1] and "findmnt" in fake.calls[-1]
    actions = [a["action"] for a in client.get("/api/admin/audit").json()]
    assert "host.pool.check" in actions and "host.pool.add-spare" in actions
    fake.rc = 1
    assert post(pool="md:md0", op="stop-sync").status_code == 422
