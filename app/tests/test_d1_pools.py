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


DETAIL10 = """/dev/md10:
        Raid Level : raid10
             State : clean, degraded
            Layout : near=2
              UUID : 8beabe67:a8e57c79:437836f4:ec479ec0

    Number   Major   Minor   RaidDevice State
       0       8       17        0      active sync set-A   /dev/sdb1
       -       0        0        1      removed
       2       8       33        2      active sync set-A   /dev/sdc1
       3       8       65        3      active sync set-B   /dev/sde1
"""


def _disk(n, serial, uuid="8beabe67:a8e57c79:437836f4:ec479ec0"):
    return {"name": f"sd{n}", "path": f"/dev/sd{n}", "type": "disk", "size": "3000", "model": "HITACHI", "serial": serial,
            "children": [{"name": f"sd{n}1", "path": f"/dev/sd{n}1", "type": "part", "size": "2999",
                          "fstype": "linux_raid_member", "uuid": uuid}]}


def test_a_missing_drive_is_named_by_its_serial():
    """OZ, 2026-11-06: slot 1 of a RAID 10 was empty; the drive that was failed out still carried the array."""
    f = facts(md={"md10": {"level": "raid10", "array_state": "clean", "sync_action": "idle", "degraded": "1", "raid_disks": "4",
                           "size": "11720531968", "detail": DETAIL10,
                           "members": {"sdb1": {"state": "in_sync", "slot": "0", "errors": "0"},
                                       "sdc1": {"state": "in_sync", "slot": "2", "errors": "0"},
                                       "sde1": {"state": "in_sync", "slot": "3", "errors": "0"}}}},
              disks=[_disk("b", "SERB"), _disk("c", "SERC"), _disk("d", "P8G8JSPR", uuid="8beabe67-a8e5-7c79-4378-36f4ec479ec0"),
                     _disk("e", "SERE"), _disk("f", "OTHER", uuid="ffffffff:ffffffff:ffffffff:ffffffff")])
    (p,) = hostpools.build(f)
    assert p["missing"] == [{"slot": 1, "state": "removed", "partner": "/dev/sdb1"}]
    assert p["at_risk"] == ["/dev/sdb1"]
    assert [(m["name"], m["disk"], m["serial"]) for m in p["members"]][0] == ("/dev/sdb1", "/dev/sdb", "SERB")
    assert [(m["name"], m["disk"], m["serial"]) for m in p["former_members"]] == [("/dev/sdd1", "/dev/sdd", "P8G8JSPR")]
    flag = [w for w in hostpools.warnings(f) if w.get("disk")]
    assert [(w["disk"], w["level"]) for w in flag] == [("/dev/sdd", "warning")] and "serial P8G8JSPR" in flag[0]["message"]
    msg = hostpools.warnings(f)[0]["message"]
    assert "degraded (1 of 4 drives missing)" in msg and "/dev/sdd1 (serial P8G8JSPR)" in msg and "only copy of its data is on /dev/sdb1" in msg


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


def test_a_zfs_member_disk_offers_no_free_partition():
    # ZFS on a whole disk makes a data partition (1) and an empty 8 MB partition (9); found live on nkvm1, 2026-10-19.
    zdisk = lambda d: {"name": d, "path": f"/dev/{d}", "type": "disk", "size": "2147483648", "children": [
        {"name": f"{d}1", "path": f"/dev/{d}1", "type": "part", "size": "2136997888", "fstype": "zfs_member"},
        {"name": f"{d}9", "path": f"/dev/{d}9", "type": "part", "size": "8388608", "fstype": None}]}
    f = facts()
    f["disks"] += [zdisk("vdd"), zdisk("vde"),
                   {"name": "vdi", "path": "/dev/vdi", "type": "disk", "size": "4000", "children": [
                       {"name": "vdi1", "path": "/dev/vdi1", "type": "part", "size": "3999", "fstype": None}]}]
    assert [d["path"] for d in hostpools.unused_disks(f)] == ["/dev/sdc", "/dev/vdi1"]


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


def _oz_facts():
    return facts(md={"md10": {"level": "raid10", "array_state": "clean", "sync_action": "idle", "degraded": "1", "raid_disks": "4",
                              "size": "11720531968", "detail": DETAIL10,
                              "members": {"sdb1": {"state": "in_sync", "slot": "0", "errors": "0"},
                                          "sdc1": {"state": "in_sync", "slot": "2", "errors": "0"},
                                          "sde1": {"state": "in_sync", "slot": "3", "errors": "0"}}}},
                 disks=[_disk("b", "SERB"), _disk("c", "SERC"), _disk("d", "P8G8JSPR", uuid="8beabe67-a8e5-7c79-4378-36f4ec479ec0"),
                        _disk("e", "SERE")])


def _smart(**kw):
    return {"disks": [{"device": "/dev/sdd", "serial": "P8G8JSPR", "passed": True, "reallocated": 0, "pending": 0,
                       "uncorrectable": 0, **kw}]}


def test_readd_check_judges_the_former_drive_by_its_smart_reading():
    """OZ, 2026-11-06: P8G8JSPR had 77 reallocated sectors, so it needs a typed confirmation to go back."""
    (p,) = hostpools.build(_oz_facts(), _smart())
    (f,) = p["former_members"]
    assert f["assessment"] == {"ok": True, "reasons": []} and "re-add" in p["actions"]
    (p,) = hostpools.build(_oz_facts(), _smart(reallocated=77, pending=1, passed=False))
    assert p["former_members"][0]["assessment"] == {"ok": False, "reasons": [
        "its SMART health check failed", "77 reallocated sectors", "1 pending sector"]}
    (p,) = hostpools.build(_oz_facts(), {})
    assert p["former_members"][0]["assessment"] == {"ok": False, "reasons": ["no SMART reading for this drive yet"]}
    f = facts()
    assert "re-add" not in hostpools.build(f)[0]["actions"]                      # a healthy array has nothing to re-add


def test_readd_scripts():
    (p,) = hostpools.build(_oz_facts(), _smart())
    assert hostpools.action_script(p, "re-add", "/dev/sdd1") == "mdadm /dev/md10 --re-add /dev/sdd1"
    full = hostpools.action_script(p, "re-add", "/dev/sdd1", full=True)
    assert full == "set -e; mdadm --zero-superblock /dev/sdd1; mdadm /dev/md10 --add /dev/sdd1"
    with pytest.raises(hostadmin.AdminError):
        hostpools.action_script(p, "re-add", "/dev/sdd1; reboot")


def test_the_readd_api_asks_for_a_typed_confirmation_when_the_drive_fails_the_check(client, monkeypatch):
    _admin_session(client)
    host_id = _host(client)
    with session_factory()() as db:
        h = db.get(KvmHost, host_id)
        h.access_status, h.ssh_address, h.ssh_user = "ready", "oz.lab", "localadmin01"
        db.add(HostFacts(kvm_host_id=host_id, facts=_oz_facts(), smart=_smart(reallocated=77)))
        db.commit()
    fake = FakeSsh()
    monkeypatch.setattr(hostadmin, "for_host", lambda h: fake)
    monkeypatch.setattr(hostadmin, "collect_facts", lambda db, h, smart=False: None)
    view = client.get(f"/api/hosts/{host_id}/admin").json()
    assert view["pools"][0]["former_members"][0]["assessment"]["reasons"] == ["77 reallocated sectors"]
    post = lambda **b: client.post(f"/api/hosts/{host_id}/pools/action", json={"pool": "md:md10", "op": "re-add", **b})   # noqa: E731
    assert post(device="/dev/sdb1").status_code == 422                          # a current member
    assert post(device="/dev/sdd1").status_code == 422 and not fake.calls       # no confirmation
    assert post(device="/dev/sdd1", confirm="yes").status_code == 422 and not fake.calls
    assert post(device="/dev/sdd1", confirm="/dev/sdd1").status_code == 200
    assert "--re-add /dev/sdd1" in fake.calls[-1] and "findmnt" in fake.calls[-1]
    log = [a for a in client.get("/api/admin/audit").json() if a["action"] == "host.pool.re-add" and a.get("outcome") != "failed"]
    assert log and log[0]["detail"]["forced"] is True and log[0]["detail"]["reasons"] == ["77 reallocated sectors"]
    with session_factory()() as db:                                             # a drive that passed needs none
        db.get(HostFacts, host_id).smart = _smart()
        db.commit()
    assert post(device="/dev/sdd1", full=True).status_code == 200
    assert "--zero-superblock /dev/sdd1" in fake.calls[-1]
