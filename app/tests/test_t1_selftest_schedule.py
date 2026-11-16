"""Batch T1 (plan: Planning_SMART-Self-Test-Schedule.md): the schedule's settings, what the host's smartd.conf says,
and the warnings that know whether a self-test is scheduled."""
import builtins
import contextlib
import datetime as dt
import io
import json
import os
import subprocess

from labape import appsettings
from labape.db import session_factory
from labape.engine import hostadmin, selftests
from labape.models import Setting

NEVER = {"device": "/dev/sda", "model": "WD", "power_on_hours": 5000, "tests": {}}
DEFAULT_EXPR = "S/../../7/02|L/../01/./03"


def smartd(**kw):
    return {"conf": "/etc/smartd.conf", "scan": DEFAULT_EXPR, "devices": {}, "active": True,
            "now": "2026-11-13T09:00:00", "conf_mtime": "2026-11-12T10:00:00", **kw}


def test_the_expression_round_trips_and_reads_in_words():
    eff = {"run": True, "short_on": True, "short_weekday": 3, "short_hour": 4, "long_on": True, "long_day": 15, "long_hour": 5}
    expr = selftests.expression(eff)
    assert expr == "S/../../3/04|L/../15/./05"
    assert selftests.parse_expression(expr)["short"] == {"weekday": 3, "hour": 4}
    assert selftests.describe(expr) == "short: every Wednesday at 04:00, long: the 15th of the month at 05:00"
    assert selftests.expression({**eff, "long_on": False}) == "S/../../3/04"
    assert selftests.expression({**eff, "run": False}) == ""
    assert selftests.describe("") == "no scheduled self-tests"
    assert selftests.parse_expression("(S/../../3/04|C/../../1/02)")["other"] is True       # a hand-written form is kept as it is
    assert "custom:" in selftests.describe("S/../../3/04|C/../../1/02")


def test_the_next_run_is_the_next_matching_time_on_the_host_clock():
    fri = dt.datetime(2026, 11, 13, 9, 0)
    nxt = selftests.next_run(DEFAULT_EXPR, fri)
    assert nxt["short"] == dt.datetime(2026, 11, 15, 2, 0) and nxt["long"] == dt.datetime(2026, 12, 1, 3, 0)
    sun_early = dt.datetime(2026, 11, 15, 1, 0)
    assert selftests.next_run(DEFAULT_EXPR, sun_early)["short"] == dt.datetime(2026, 11, 15, 2, 0)
    sun_late = dt.datetime(2026, 11, 15, 2, 30)
    assert selftests.next_run(DEFAULT_EXPR, sun_late)["short"] == dt.datetime(2026, 11, 22, 2, 0)
    assert selftests.next_run("", fri) == {"short": None, "long": None}


def test_a_host_follows_the_stack_default_unless_it_overrides_it(client):
    db = session_factory()()
    try:
        host = 987
        assert selftests.effective(db, host)["mode"] == "default" and selftests.expression(selftests.effective(db, host)) == DEFAULT_EXPR
        selftests.save_override(db, host, {"mode": "custom", "short_weekday": 2, "long_on": False, "skip": ["/dev/sdb"], "warn": False})
        eff = selftests.effective(db, host)
        assert selftests.expression(eff) == "S/../../2/02" and eff["skip"] == ["/dev/sdb"] and eff["warn"] is False
        selftests.save_override(db, host, {"mode": "off"})
        assert selftests.expression(selftests.effective(db, host)) == "" and selftests.effective(db, host)["warn"] is True
        db.add(Setting(key="app.selftests", value={"short_hour": 5}))
        db.commit()
        appsettings._cache.clear()
        selftests.save_override(db, host, {})
        assert selftests.expression(selftests.effective(db, host)).startswith("S/../../7/05")
    finally:
        db.query(Setting).filter(Setting.key.in_(("selftests.host987", "app.selftests"))).delete(synchronize_session=False)
        db.commit()
        appsettings._cache.clear()
        db.close()


def messages(d, sd, policy=None):
    return [(w["level"], w["message"]) for w in selftests.disk_warnings(d, sd, policy)]


def test_never_tested_says_whether_a_test_is_scheduled():
    lvl, msg = messages(NEVER, smartd())[0]
    assert lvl == "info" and "no self-test has run yet; the first is scheduled" in msg
    assert "short: every Sunday at 02:00" in msg and "next 2026-11-15 02:00 host time" in msg
    lvl, msg = messages(NEVER, smartd(scan=None))[0]
    assert lvl == "warning" and "none is scheduled" in msg and "Self-tests page" in msg
    # a scheduled time came and went a day ago and still nothing: smartd cannot test the drive
    lvl, msg = messages(NEVER, smartd(now="2026-11-18T09:00:00"))[0]
    assert lvl == "warning" and "smartd may be unable to test this drive" in msg and "-d sat" in msg
    assert "flash stick" in msg and "leave the drive out" in msg
    assert messages({**NEVER, "smart_support": False}, smartd(now="2026-11-18T09:00:00")) == []   # no SMART at all: nothing to schedule
    lvl, msg = messages(NEVER, smartd(active=False))[0]
    assert lvl == "warning" and "smartd isn't running" in msg
    lvl, msg = messages(NEVER, None)[0]                                           # a reading from before smartd was reported
    assert lvl == "warning" and "no SMART self-test has ever run" in msg


def test_switches_and_skipped_drives_silence_the_missing_test_but_never_a_failed_one():
    assert messages(NEVER, smartd(), {"warn": False}) == []
    assert messages(NEVER, smartd(), {"skip": ["/dev/sda"]}) == []
    assert messages(NEVER, smartd(devices={"/dev/sda": None})) == []              # the host's file leaves the drive out
    assert messages(NEVER, smartd(devices={"/dev/sda": "S/../../1/01"}))[0][0] == "info"   # its own line wins over DEVICESCAN
    bad = {**NEVER, "tests": {"short": {"hours": 4990, "passed": False, "status": "Completed: read failure"}}}
    assert [lvl for lvl, _ in messages(bad, smartd(), {"warn": False})] == ["error"]
    assert [lvl for lvl, _ in messages(bad, smartd(devices={"/dev/sda": None}))] == ["error"]


def test_a_stale_test_names_what_is_scheduled():
    old = {**NEVER, "power_on_hours": 9000, "tests": {"short": {"hours": 100, "passed": True, "status": "ok"}}}
    assert "no SMART self-test in 370 days" in messages(old, smartd())[0][1] and "scheduled: short:" in messages(old, smartd())[0][1]
    assert "none is scheduled" in messages(old, smartd(scan=None))[0][1]


def test_the_collector_reports_what_smartd_conf_says(monkeypatch, tmp_path):
    conf = tmp_path / "smartd.conf"
    nl = chr(10)
    conf.write_text("# top" + nl + "#original: DEVICESCAN -a" + nl + "/dev/sdb -a -m root" + nl +
                    "/dev/sdc -a -s (L/../../1/05)" + nl + "DEVICESCAN -a -m root -s (" + DEFAULT_EXPR + ")" + nl +
                    "/dev/sdz -a" + nl)
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        return real_open(conf if path == "/etc/smartd.conf" else path, *a, **kw)

    def fake_run(cmd, **kw):
        if "--scan-open" in cmd:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"devices": []}), "")
        return subprocess.CompletedProcess(cmd, 0 if cmd[:2] == ["systemctl", "is-active"] and cmd[-1] == "smartd" else 3, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/sbin/smartctl")
    monkeypatch.setattr(os.path, "isfile", lambda p: p == "/etc/smartd.conf")
    monkeypatch.setattr(os.path, "getmtime", lambda p: 1763000000.0)
    monkeypatch.setattr(builtins, "open", fake_open)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(hostadmin.SMART, {"ARGS": {}})
    rep = json.loads(buf.getvalue())["smartd"]
    assert rep["scan"] == "(" + DEFAULT_EXPR + ")" and rep["active"] is True        # the first of smartd/smartmontools is enough
    assert rep["devices"] == {"/dev/sdb": None, "/dev/sdc": "(L/../../1/05)"}       # nothing after DEVICESCAN counts
    assert rep["now"] and rep["conf_mtime"]
