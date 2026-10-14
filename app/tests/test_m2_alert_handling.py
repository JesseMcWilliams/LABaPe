"""Batch M2 (design §31): acknowledging and silencing alerts, quiet during maintenance, an alert when
alert emails fail, and the syslog sender's kept-open connection."""
import datetime as dt
import socket
import ssl
import threading

import pytest

from labape import appsettings, maintenance_mode, notify
from labape.db import session_factory
from labape.engine import stackmon
from labape.models import MonitorAlert, MonitorSilence
from labape.security import Principal, current_principal

from test_api import _admin_session

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
FS = {"host1:fs:/ is #% full": (None, "warning", "kvm1: / is 87% full")}


def _recorder():
    sent = []

    def send(db, s, c):
        sent.append(([a.message for a in s], [a.message for a in c]))
    return sent, send


def test_a_silenced_start_waits_and_its_clear_follows_the_start(client):
    _admin_session(client)
    sent, send = _recorder()
    with session_factory()() as db:
        db.add(MonitorSilence(key="host1:fs:/ is #% full", created_at=NOW, until=NOW + dt.timedelta(hours=1)))
        db.commit()
        stackmon.sync_alerts(db, FS, NOW, send)
        assert sent == [] and db.query(MonitorAlert).one().announced_at is None       # recorded, not announced
        stackmon.sync_alerts(db, FS, NOW + dt.timedelta(hours=2), send)                 # the silence ended
        assert sent == [(["kvm1: / is 87% full"], [])]
        # Silenced again: the clear still goes, since its start did.
        db.add(MonitorSilence(key="host1:fs:/ is #% full", created_at=NOW, until=NOW + dt.timedelta(hours=5)))
        db.commit()
        stackmon.sync_alerts(db, {}, NOW + dt.timedelta(hours=3), send)
        assert sent[-1] == ([], ["kvm1: / is 87% full"])
        # A start and clear inside a silence: neither is announced.
        stackmon.sync_alerts(db, FS, NOW + dt.timedelta(hours=3, minutes=1), send)
        stackmon.sync_alerts(db, {}, NOW + dt.timedelta(hours=3, minutes=2), send)
        assert len(sent) == 2


def test_maintenance_mode_holds_announcements_until_it_ends(client):
    _admin_session(client)
    sent, send = _recorder()
    with session_factory()() as db:
        maintenance_mode.turn_on(db, "admin", "patching")
        stackmon.sync_alerts(db, FS, NOW, send)
        assert sent == []
        maintenance_mode.turn_off(db)
        stackmon.sync_alerts(db, FS, NOW + dt.timedelta(minutes=1), send)
        assert sent == [(["kvm1: / is 87% full"], [])]


def test_ack_and_silence_endpoints(client):
    _admin_session(client)
    with session_factory()() as db:
        stackmon.sync_alerts(db, FS, NOW, lambda *a: None)
        aid = db.query(MonitorAlert).one().id
        maintenance_mode.turn_on(db, "admin", "patching")                      # still allowed in maintenance
    assert client.post(f"/api/dashboard/monitoring/alerts/{aid}/ack", json={}).status_code == 200
    a = next(x for x in client.get("/api/dashboard/monitoring").json()["open"] if x["id"] == aid)
    assert a["acked_by"] and a["acked_at"] and a["silenced_until"] is None
    assert client.post("/api/dashboard/monitoring/silences", json={"alert_id": aid, "hours": 0}).status_code == 422
    r = client.post("/api/dashboard/monitoring/silences", json={"alert_id": aid, "hours": 4, "reason": "disk swap"})
    assert r.status_code == 200
    m = client.get("/api/dashboard/monitoring").json()
    assert m["quiet"] is True and m["silences"][0]["reason"] == "disk swap" and m["open"][0]["silenced_until"]
    assert client.delete(f"/api/dashboard/monitoring/silences/{r.json()['id']}").status_code == 200
    assert client.get("/api/dashboard/monitoring").json()["silences"] == []
    assert client.post(f"/api/dashboard/monitoring/alerts/{aid}/ack", json={"ack": False}).status_code == 200
    assert client.get("/api/dashboard/monitoring").json()["open"][0]["acked_at"] is None
    client.app.dependency_overrides[current_principal] = lambda: Principal(user_id=9, username="a", display_name="",
                                                                           roles={"auditor"})
    assert client.post(f"/api/dashboard/monitoring/alerts/{aid}/ack", json={}).status_code == 403   # auditors read
    client.app.dependency_overrides.clear()
    with session_factory()() as db:
        maintenance_mode.turn_off(db)


def test_a_failed_alert_email_raises_its_own_alert_until_the_server_works(client, monkeypatch):
    _admin_session(client)
    mails, checks = [], []

    def fail(cfg, pw, to, subject, body, **k):
        mails.append(body)
        raise OSError("connection refused")

    monkeypatch.setattr(notify, "send_email", fail)
    monkeypatch.setattr(notify, "audit_to_syslog", lambda cfg, ev: None)
    with session_factory()() as db:
        appsettings.put(db, "monitoring", appsettings.MonitoringSettings(email_to=["ops@example.invalid"]))
        appsettings.put(db, "smtp", appsettings.SmtpSettings(enabled=True, host="smtp.example.invalid",
                                                             from_address="labape@example.invalid"))
        stackmon.sync_alerts(db, FS, NOW)
        assert stackmon.email_state(db)["ok"] is False
        cur = stackmon.evaluate(db, NOW)
        assert cur[stackmon.EMAIL_KEY][2] == "alert emails can't be sent: connection refused"
        stackmon.sync_alerts(db, {**FS, **cur}, NOW)
        assert len(mails) == 1                                    # its own start isn't emailed
        monkeypatch.setattr(notify, "smtp_check", lambda cfg, pw: checks.append(cfg.host))
        stackmon.recheck_email(db, NOW + dt.timedelta(minutes=1))          # too soon
        assert checks == []
        stackmon.recheck_email(db, NOW + dt.timedelta(minutes=6))
        assert checks == ["smtp.example.invalid"] and stackmon.email_state(db)["ok"] is True
        assert stackmon.EMAIL_KEY not in stackmon.evaluate(db, NOW)


def _server_context(tmp_path):
    """TLS 1.3 with a throwaway self-signed certificate (the collector sends session tickets)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(minutes=1)).not_valid_after(now + dt.timedelta(hours=1))
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    return ctx


def _collector(tls=None):
    """A TCP (or TLS) syslog collector: counts connections and collects the framed messages."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    got = {"conns": [], "data": b""}

    def serve():
        while True:
            try:
                c, _ = srv.accept()
                if tls:
                    c = tls.wrap_socket(c, server_side=True)
            except OSError:
                return
            got["conns"].append(c)

            def read(c=c):
                while True:
                    try:
                        b = c.recv(65536)
                    except OSError:
                        return
                    if not b:
                        return
                    got["data"] += b
            threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return srv, got


def _wait(cond):
    import time
    for _ in range(100):
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize("protocol", ["tcp", "tls"])
def test_the_syslog_sender_keeps_one_connection_and_reconnects(protocol, tmp_path):
    srv, got = _collector(_server_context(tmp_path) if protocol == "tls" else None)
    cfg = appsettings.SyslogSettings(enabled=True, host="127.0.0.1", port=srv.getsockname()[1], protocol=protocol,
                                     verify_tls=False)
    st = notify._Stream()
    try:
        for i in range(3):
            st.send(cfg, {"action": f"test.{i}"})
            assert st._alive()                               # session tickets aren't a close
        assert _wait(lambda: got["data"].count(b'"action":"test.') == 3)
        assert st.opened == 1 and len(got["conns"]) == 1
        got["conns"][0].close()                              # the collector restarts
        assert _wait(lambda: not st._alive())
        st.send(cfg, {"action": "test.after"})
        assert _wait(lambda: b"test.after" in got["data"]) and st.opened == 2
    finally:
        st.close()
        srv.close()
