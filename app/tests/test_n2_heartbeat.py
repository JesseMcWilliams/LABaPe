"""Batch N2 (plan: Planning_Disk-Health-Notifications.md): the alert-path heartbeat and the daily digest."""
import datetime as dt

from labape.db import session_factory
from labape.engine import heartbeat as HB, hostadmin, notifications as N, stackmon
from labape.models import KvmHost, MonitorAlert, NotificationChannel, NotificationRule, utcnow

from test_c1_containers import FakeSsh, _ready_host
from test_api import _admin_session


def setup(client, monkeypatch, out=b"mail=sent syslog=sent\n", mailpit=True, syslog=None):
    _admin_session(client)
    host_id = _ready_host(client)
    ssh = FakeSsh(text=out)
    monkeypatch.setattr(hostadmin, "for_host", lambda h: ssh)
    monkeypatch.setattr(HB, "mailpit_has", lambda token: mailpit)
    monkeypatch.setattr(HB, "syslog_judged", lambda h, now: syslog is not None)
    monkeypatch.setattr(HB, "syslog_has", lambda token, since: bool(syslog))
    return host_id, ssh


def beat(host_id, now=None):
    with session_factory()() as db:
        return HB.run_host(db, db.get(KvmHost, host_id), now or utcnow(), wait=0, sleep=lambda s: None)


def test_a_heartbeat_that_arrives_is_ok_and_runs_the_hosts_mail_program(client, monkeypatch):
    host_id, ssh = setup(client, monkeypatch)
    r = beat(host_id)
    assert r["mail"] == "ok" and r["syslog"] == "n/a"
    assert "sudo -n sh -c" in ssh.calls[0] and "mail -s" in ssh.calls[0] and "logger -t labape-heartbeat" in ssh.calls[0]
    with session_factory()() as db:
        assert HB.state(db, host_id)["misses"] == 0 and HB.alerts(db) == {}


def test_two_lost_heartbeats_raise_an_alert_naming_the_dead_path(client, monkeypatch):
    host_id, _ = setup(client, monkeypatch, mailpit=False, syslog=False)
    now = utcnow()
    assert beat(host_id, now)["mail"] == "lost"
    with session_factory()() as db:
        assert HB.alerts(db) == {}                                           # one miss is not enough
    beat(host_id, now + dt.timedelta(days=1))
    with session_factory()() as db:
        (key, (hid, level, msg)), = HB.alerts(db).items()
        assert key == f"monitor:heartbeat:{host_id}" and level == "warning"
        assert "didn't reach the stack's Mailpit" in msg and "syslog line didn't reach" in msg
        assert N.category(key, msg) == "alert-path"
    HB_ok = setup  # noqa: F841
    monkeypatch.setattr(HB, "mailpit_has", lambda token: True)
    monkeypatch.setattr(HB, "syslog_has", lambda token, since: True)
    beat(host_id, now + dt.timedelta(days=2))
    with session_factory()() as db:
        assert HB.alerts(db) == {}                                           # back to normal clears it


def test_a_host_without_a_mail_program_is_named(client, monkeypatch):
    host_id, _ = setup(client, monkeypatch, out=b"mail=none syslog=sent\n")
    now = utcnow()
    assert beat(host_id, now)["mail"] == "none"
    beat(host_id, now + dt.timedelta(days=1))
    with session_factory()() as db:
        assert "no mail program" in list(HB.alerts(db).values())[0][2]


def test_an_unreachable_host_is_not_a_miss(client, monkeypatch):
    host_id, _ = setup(client, monkeypatch)
    monkeypatch.setattr(hostadmin, "for_host", lambda h: (_ for _ in ()).throw(hostadmin.AdminError("ssh: no route")))
    assert "no route" in beat(host_id)["error"]
    with session_factory()() as db:
        assert HB.state(db, host_id).get("misses", 0) == 0


def test_due_runs_once_a_day_and_the_setting_turns_it_off(client, monkeypatch):
    host_id, ssh = setup(client, monkeypatch)
    monkeypatch.setattr(HB.stack_mail, "available", lambda: True)
    monkeypatch.setattr(HB, "WAIT_SECONDS", 0)
    now = utcnow()
    with session_factory()() as db:
        HB.due(db, now)
        HB.due(db, now + dt.timedelta(hours=3))
        assert len(ssh.calls) == 1
        HB.due(db, now + dt.timedelta(hours=24))
        assert len(ssh.calls) == 2


def test_the_digest_goes_once_a_day_at_the_hour_to_rules_that_ask(client, monkeypatch):
    sent = []
    monkeypatch.setattr(N, "send", lambda db, ch, title, lines: sent.append((title, lines)))
    now = dt.datetime(2026, 11, 8, 7, 5, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        ch = NotificationChannel(name="ops", kind="ntfy", config={"topic": "t"})
        db.add(ch)
        db.commit()
        db.add(NotificationRule(name="digest", channel_ids=[ch.id], send_digest=True, min_level="warning", created_at=now))
        db.add(NotificationRule(name="plain", channel_ids=[ch.id], created_at=now))
        db.commit()
        assert N.digest(db, now.replace(hour=6)) == 0                        # not the hour
        assert N.digest(db, now) == 0 and not sent                           # a quiet day sends nothing
        db.add(MonitorAlert(key="a", level="error", message="kvm1: RAID array md10 is degraded", started_at=now - dt.timedelta(hours=30)))
        db.add(MonitorAlert(key="b", level="warning", message="old thing", started_at=now - dt.timedelta(hours=40), cleared_at=now - dt.timedelta(hours=2)))
        db.add(MonitorAlert(key="c", level="warning", message="ancient", started_at=now - dt.timedelta(days=9), cleared_at=now - dt.timedelta(days=5)))
        db.commit()
        N_digest = N.digest
        from labape.models import Setting
        db.delete(db.get(Setting, N.DIGEST_KEY))                             # the quiet day above used up today
        db.commit()
        assert N_digest(db, now) == 1
        title, lines = sent[0]
        assert "1 open, 1 cleared" in title and "RAID array md10 is degraded" in lines[0] and "old thing" in lines[1]
        assert N_digest(db, now + dt.timedelta(minutes=10)) == 0             # once


def test_the_monitoring_tick_has_the_new_alert(client, monkeypatch):
    host_id, _ = setup(client, monkeypatch, mailpit=False)
    now = utcnow()
    beat(host_id, now)
    beat(host_id, now + dt.timedelta(days=1))
    with session_factory()() as db:
        assert f"monitor:heartbeat:{host_id}" in stackmon.evaluate(db)
