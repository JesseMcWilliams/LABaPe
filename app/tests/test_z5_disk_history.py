"""Batch H3 (design §64): SMART history per drive, the drive's page, and the link from an alert."""
import datetime as dt

from labape.api import host_disks
from labape.db import session_factory
from labape.engine import hostadmin, maintenance
from labape.models import DiskSample, HostFacts, MonitorAlert, utcnow

from test_api import _admin_session, _host

SDC = {"device": "/dev/sdc", "model": "ST4000", "serial": "WD-SER1", "passed": True, "temperature": 38, "power_on_hours": 9000,
       "reallocated": 76, "pending": 3960, "uncorrectable": 0, "wear_pct": None, "media_errors": None,
       "attributes": [{"id": 5, "name": "Reallocated_Sector_Ct", "value": 90, "worst": 90, "thresh": 36, "raw": 76, "failed": ""}]}
SDA = {"device": "/dev/sda", "model": "SSD", "serial": "SSD-1", "passed": True, "temperature": 30, "power_on_hours": 100,
       "reallocated": 0, "pending": 0, "uncorrectable": 0}


def _facts(db, host_id=1):
    db.merge(HostFacts(kvm_host_id=host_id, collected_at=utcnow(), smart_at=utcnow(),
                       facts={"disks": [{"name": "sdc", "path": "/dev/sdc", "serial": "WD-SER1", "type": "disk", "size": 4000787030016,
                                         "children": [{"name": "sdc1", "path": "/dev/sdc1", "type": "part", "size": 1,
                                                       "fstype": "ext4", "mountpoints": ["/data"]}]}]},
                       smart={"available": True, "disks": [SDC, SDA]}))
    db.commit()


def test_the_alert_names_the_drive_and_a_link_can_be_built():
    ws = hostadmin.warnings({}, {"disks": [SDC]}, None)
    assert [w["disk"] for w in ws] == ["/dev/sdc"] and "3960 pending" in ws[0]["message"]
    assert hostadmin.disk_of("kvm1: /dev/sdc: 76 reallocated, 3960 pending sectors") == "/dev/sdc"
    assert hostadmin.disk_of("/dev/nvme0n1: 82% of its rated wear used") == "/dev/nvme0n1"
    assert hostadmin.disk_of("kvm1: /dev/sdc (ST4000): SMART overall health FAILED") == "/dev/sdc"
    assert hostadmin.disk_of("kvm1: /data is 91% full") == "" and hostadmin.disk_of("") == ""


def test_readings_are_kept_and_repeats_are_not(client):
    _admin_session(client)
    _host(client)
    now = utcnow()
    with session_factory()() as db:
        assert hostadmin.record_samples(db, 1, {"disks": [SDC, SDA]}, now) == 2
        assert hostadmin.record_samples(db, 1, {"disks": [SDC, SDA]}, now + dt.timedelta(minutes=10)) == 0     # same, soon
        worse = {**SDC, "pending": 4100}
        assert hostadmin.record_samples(db, 1, {"disks": [worse]}, now + dt.timedelta(minutes=20)) == 1         # changed
        assert hostadmin.record_samples(db, 1, {"disks": [SDC]}, now + dt.timedelta(hours=7)) == 1                # same but old
        db.commit()
        assert db.query(DiskSample).filter_by(disk="WD-SER1").count() == 3
        assert hostadmin.record_samples(db, 1, {"disks": [{"device": "", "serial": ""}]}, now) == 0                # nothing to key


def test_the_drive_page_has_history_changes_alerts_and_layout(client):
    _admin_session(client)
    _host(client)
    now = utcnow()
    with session_factory()() as db:
        _facts(db)
        for i, pending in enumerate((0, 0, 120, 3960)):
            db.add(DiskSample(kvm_host_id=1, disk="WD-SER1", device="/dev/sdc", at=now - dt.timedelta(days=30 - i * 10),
                              data={"pending": pending, "reallocated": 76, "uncorrectable": 0, "temperature": 38}))
        db.add(DiskSample(kvm_host_id=1, disk="WD-SER1", device="/dev/sdc", at=now - dt.timedelta(days=400), data={"pending": 0}))
        db.add(MonitorAlert(key="host1:x", kvm_host_id=1, level="warning",
                            message="kvm1: /dev/sdc: 76 reallocated, 3960 pending sectors"))
        db.commit()
    for ref in ("WD-SER1", "sdc"):
        j = client.get(f"/api/hosts/1/disks/{ref}?days=60").json()
        assert j["disk"]["serial"] == "WD-SER1" and j["host"]["name"] and j["retention_days"] == 365
        assert [s["data"]["pending"] for s in j["samples"]] == [0, 0, 120, 3960]       # the 400-day-old one is out of range
        assert [(c["attribute"], c["from"], c["to"]) for c in j["changes"]] == [("pending", 120, 3960), ("pending", 0, 120)]
        assert j["alerts"] and "3960 pending" in j["alerts"][0]["message"] and j["warnings"][0]["disk"] == "/dev/sdc"
        assert j["block"]["name"] == "sdc" and j["block"]["children"][0]["mountpoints"] == ["/data"]
    assert client.get("/api/hosts/1/disks/%2Fdev%2Fsdc").status_code == 200              # /dev/sdc, encoded
    assert client.get("/api/hosts/1/disks/nope").status_code == 404
    assert client.get("/api/hosts/99/disks/sdc").status_code == 404
    assert host_disks.changes([]) == []


def test_old_readings_are_purged_by_the_retention_setting(client):
    _admin_session(client)
    _host(client)
    now = utcnow()
    with session_factory()() as db:
        db.add_all([DiskSample(kvm_host_id=1, disk="a", at=now - dt.timedelta(days=500), data={}),
                    DiskSample(kvm_host_id=1, disk="a", at=now - dt.timedelta(days=10), data={})])
        db.commit()
        assert maintenance.purge_history(db)["disk_samples"] == 1
        assert db.query(DiskSample).count() == 1
    s = client.get("/api/admin/settings").json()["retention"]
    assert s["disk_history_days"] == 365
