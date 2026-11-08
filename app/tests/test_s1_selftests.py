"""Batch S1 (plan: Planning_Disk-Health-Notifications.md): scheduled SMART self-tests and their warnings."""
import contextlib
import io
import json
import subprocess

from labape.engine import hostadmin


def disk(poh=61359, **kw):
    return {"device": "/dev/sdd", "model": "HITACHI", "serial": "P8G8JSPR", "power_on_hours": poh, **kw}


def test_a_drive_that_was_never_tested_or_not_lately_is_flagged():
    never = hostadmin.selftest_warnings(disk(tests={}))
    assert [(w["level"], w["disk"]) for w in never] == [("warning", "/dev/sdd")] and "no SMART self-test has ever run" in never[0]["message"]
    old = hostadmin.selftest_warnings(disk(tests={"short": {"hours": 21163, "passed": True, "status": "Completed without error"}}))
    assert "no SMART self-test in 1674 days of running (the last at hour 21163)" in old[0]["message"]
    fresh = disk(tests={"short": {"hours": 61300, "passed": True, "status": "ok"}, "long": {"hours": 61000, "passed": True, "status": "ok"}})
    assert hostadmin.selftest_warnings(fresh) == []
    edge = disk(tests={"long": {"hours": 61359 - 336, "passed": True, "status": "ok"}})
    assert hostadmin.selftest_warnings(edge) == []                               # exactly two weeks is still fine


def test_a_failed_self_test_is_an_error_and_unknown_readings_say_nothing():
    bad = hostadmin.selftest_warnings(disk(tests={"long": {"hours": 61350, "passed": False, "status": "Completed: read failure"}}))
    assert [(w["level"], "read failure" in w["message"]) for w in bad] == [("error", True)]
    assert hostadmin.selftest_warnings(disk()) == []                              # NVMe, or a reading from before this existed
    assert hostadmin.selftest_warnings({"device": "/dev/sda", "tests": {}}) == []   # no power-on hours to compare
    w = hostadmin.warnings({}, {"disks": [disk(passed=True, tests={})]}, None)
    assert any("ever run" in x["message"] for x in w)


JSON = {"model_name": "HITACHI HUS724030ALE641", "serial_number": "P8G8JSPR", "smart_status": {"passed": True},
        "power_on_time": {"hours": 61359}, "ata_smart_attributes": {"table": []},
        "ata_smart_self_test_log": {"standard": {"table": [
            {"type": {"string": "Short offline"}, "status": {"passed": True, "string": "Completed without error"}, "lifetime_hours": 61300},
            {"type": {"string": "Vendor (0xb0)"}, "status": {"passed": True, "string": "x"}, "lifetime_hours": 61290},
            {"type": {"string": "Extended offline"}, "status": {"passed": False, "string": "Completed: read failure"}, "lifetime_hours": 61000},
            {"type": {"string": "Short offline"}, "status": {"passed": True, "string": "old"}, "lifetime_hours": 100}]}}}


def _run_smart(monkeypatch, payload):
    def fake_run(cmd, **kw):
        if "--scan-open" in cmd:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"devices": [{"name": "/dev/sdd", "type": "sat"}]}), "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/sbin/smartctl")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(hostadmin.SMART, {"ARGS": {}})
    return json.loads(buf.getvalue())["disks"][0]


def test_the_smart_read_keeps_the_newest_short_and_long_test(monkeypatch):
    d = _run_smart(monkeypatch, JSON)
    assert d["tests"] == {"short": {"hours": 61300, "passed": True, "status": "Completed without error"},
                          "long": {"hours": 61000, "passed": False, "status": "Completed: read failure"}}
    assert "tests" not in _run_smart(monkeypatch, {k: v for k, v in JSON.items() if k != "ata_smart_self_test_log"})   # NVMe: no such log
