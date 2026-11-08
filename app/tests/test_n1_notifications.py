"""Batch N1 (plan: Planning_Disk-Health-Notifications.md): notification channels, rules and the delivery log."""
import datetime as dt

from sqlalchemy import select

from labape.db import session_factory
from labape.engine import notifications as N, stackmon
from labape.models import MonitorAlert, NotificationChannel, NotificationLog, NotificationRule, utcnow

from test_api import _admin_session


def alert(db, key="host1:fs:/ is #% full", level="warning", message="kvm1: / is 91% full", host=1, hours_ago=0, announced=True):
    t = utcnow() - dt.timedelta(hours=hours_ago)
    a = MonitorAlert(key=key, kvm_host_id=host, level=level, message=message, started_at=t, announced_at=t if announced else None)
    db.add(a)
    db.commit()
    return a


def test_categories_from_keys_and_words():
    cat = N.category
    assert cat("monitor:email", "") == "alert-path" and cat("secret:x", "") == "secret" and cat("link:u", "") == "stack"
    assert cat("host1:fs:/ is #% full", "") == "disk" and cat("host1:cert:x", "") == "certificate"
    assert cat("host1:stack-backup:x", "") == "backup" and cat("host1:restart:x", "") == "container"
    assert cat("host1:abc", "RAID array md10 is degraded") == "raid"
    assert cat("host1:abc", "/dev/sdd: 8 pending sectors") == "disk"


def test_quiet_hours_and_matching():
    r = NotificationRule(name="r", quiet_start="22:00", quiet_end="06:00", min_level="error", host_ids=[2], categories=["disk"])
    at = lambda h: dt.datetime(2026, 11, 8, h, 30, tzinfo=dt.timezone.utc)      # noqa: E731
    assert N.in_quiet(r, at(23)) and N.in_quiet(r, at(3)) and not N.in_quiet(r, at(12))
    a = MonitorAlert(key="host2:fs:x", kvm_host_id=2, level="error", message="x")
    assert N.matches(r, a)
    a.level = "warning"
    assert not N.matches(r, a)
    a.level, a.kvm_host_id = "error", 1
    assert not N.matches(r, a)


def test_webhook_shapes():
    assert N.webhook_body("slack", "T", "x", ["x"]) == {"text": "*T*\nx"}
    assert N.webhook_body("discord", "T", "x", ["x"])["content"].startswith("**T**")
    assert N.webhook_body("teams", "T", "a\nb", [])["text"] == "a<br>b"
    assert N.webhook_body("generic", "T", "x", ["x"])["alerts"] == ["x"]


def test_the_run_sends_once_reminds_and_clears(client, monkeypatch):
    sent = []
    monkeypatch.setattr(N, "send", lambda db, ch, title, lines: sent.append((ch.name, title, list(lines))))
    now = utcnow()
    with session_factory()() as db:
        ch = NotificationChannel(name="ops", kind="ntfy", config={"topic": "t"})
        db.add(ch)
        db.commit()
        db.add(NotificationRule(name="all", channel_ids=[ch.id], reminder_hours=2, created_at=now - dt.timedelta(days=1)))
        db.commit()
        a = alert(db, hours_ago=0.5)
        assert N.run(db, now, []) == 1 and sent[-1][2][0].startswith("[WARNING]")
        assert N.run(db, now, []) == 0                                            # once
        assert N.run(db, now + dt.timedelta(hours=2, minutes=1), [])
        assert sent[-1][2][0].startswith("[REMINDER")
        a.cleared_at = now + dt.timedelta(hours=3)
        db.commit()
        assert N.run(db, now + dt.timedelta(hours=3), [a]) == 1 and sent[-1][2][0] == "[CLEARED] kvm1: / is 91% full"
        logs = db.scalars(select(NotificationLog)).all()
        assert len(logs) == 3 and all(x.ok for x in logs)


def test_old_alerts_acked_alerts_and_quiet_hours_are_not_sent(client, monkeypatch):
    sent = []
    monkeypatch.setattr(N, "send", lambda db, ch, title, lines: sent.append(lines))
    now = dt.datetime(2026, 11, 8, 23, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        ch = NotificationChannel(name="ops", kind="ntfy", config={"topic": "t"})
        db.add(ch)
        db.commit()
        rule = NotificationRule(name="night", channel_ids=[ch.id], quiet_start="22:00", quiet_end="06:00", created_at=now - dt.timedelta(days=30))
        db.add(rule)
        db.commit()
        first = alert(db, hours_ago=0.1)
        assert N.run(db, now, []) == 0                                           # held through the quiet hours
        assert N.run(db, now.replace(hour=7), []) == 1                          # and sent when they end
        assert first.reminded
        rule.created_at = utcnow()
        db.commit()
        old = alert(db, key="host1:x:old", message="old", hours_ago=24 * 3)
        assert N.run(db, now.replace(hour=8), []) == 0 and old.reminded == {}    # raised before the rule existed
        b = alert(db, key="host1:x:b", message="b")
        b.acked_at = utcnow()
        db.commit()
        assert N.run(db, utcnow() + dt.timedelta(minutes=1), []) == 0


def test_a_failing_channel_is_logged_and_raises_an_alert(client, monkeypatch):
    def boom(db, ch, title, lines):
        raise OSError("connection refused")
    monkeypatch.setattr(N, "send", boom)
    with session_factory()() as db:
        ch = NotificationChannel(name="hook", kind="webhook", config={"shape": "slack"}, secret_set=True)
        db.add(ch)
        db.commit()
        ok, err = N.test_message(db, ch, "me")
        assert not ok and "connection refused" in err
        a = N.alerts(db)
        assert list(a.values())[0][1:] == ("warning", "notification channel hook can't deliver: connection refused")
        assert N.category(next(iter(a)), "") == "alert-path"


def test_the_notifications_api(client, monkeypatch):
    _admin_session(client)
    sent = []
    monkeypatch.setattr(N, "send", lambda db, ch, title, lines: sent.append((ch.kind, lines)))
    ch = lambda **b: client.post("/api/admin/notifications/channels", json=b)             # noqa: E731
    assert ch(name="x", kind="webhook", shape="slack").status_code == 422                  # no address
    assert ch(name="x", kind="webhook", secret="ftp://h/x").status_code == 422
    assert ch(name="x", kind="email", to=["nope"]).status_code == 422
    assert ch(name="x", kind="ntfy", topic="bad topic").status_code == 422
    v = ch(name="hook", kind="webhook", shape="teams", secret="https://example.test/hook/SECRET").json()
    cid = v["channels"][0]["id"]
    assert v["channels"][0]["secret_set"] is True and v["channels"][0]["config"] == {"shape": "teams", "host": "example.test"}
    assert "SECRET" not in client.get("/api/admin/notifications").text
    assert ch(name="hook", kind="ntfy", topic="t").status_code == 422                       # name in use
    rule = lambda **b: client.post("/api/admin/notifications/rules", json=b)               # noqa: E731
    assert rule(name="r", channel_ids=[999]).status_code == 422
    assert rule(name="r", channel_ids=[cid], quiet_start="25:00", quiet_end="06:00").status_code == 422
    assert rule(name="r", channel_ids=[cid], quiet_start="22:00").status_code == 422
    assert rule(name="r", channel_ids=[cid], categories=["nope"]).status_code == 422
    v = rule(name="r", channel_ids=[cid], categories=["disk"], min_level="error", reminder_hours=4).json()
    rid = v["rules"][0]["id"]
    assert client.delete(f"/api/admin/notifications/channels/{cid}").status_code == 409     # a rule uses it
    t = client.post(f"/api/admin/notifications/channels/{cid}/test").json()
    assert t["ok"] is True and sent[-1][0] == "webhook" and t["log"][0]["ok"] is True
    put = client.put(f"/api/admin/notifications/rules/{rid}", json={"name": "r", "channel_ids": [cid], "reminder_hours": 0})
    assert put.json()["rules"][0]["reminder_hours"] == 0
    assert client.delete(f"/api/admin/notifications/rules/{rid}").json()["rules"] == []
    assert client.delete(f"/api/admin/notifications/channels/{cid}").json()["channels"] == []
    audit = client.get("/api/admin/audit")
    actions = {a["action"] for a in audit.json()}
    assert {"notification.channel.add", "notification.channel.test", "notification.rule.add", "notification.rule.delete"} <= actions
    assert "SECRET" not in audit.text


def test_sync_alerts_runs_the_rules(client, monkeypatch):
    sent = []
    monkeypatch.setattr(N, "send", lambda db, ch, title, lines: sent.append(lines))
    with session_factory()() as db:
        ch = NotificationChannel(name="ops", kind="ntfy", config={"topic": "t"})
        db.add(ch)
        db.commit()
        db.add(NotificationRule(name="all", channel_ids=[ch.id], created_at=utcnow() - dt.timedelta(minutes=5)))
        db.commit()
        stackmon.sync_alerts(db, {"stack:x": (None, "error", "something broke")}, send=lambda *a: None)
        assert sent and sent[0] == ["[ERROR] something broke"]
        stackmon.sync_alerts(db, {}, send=lambda *a: None)
        assert sent[-1] == ["[CLEARED] something broke"]
