"""Admin settings: sections, secrets, syslog output, backups, retention purges, job emails."""
import datetime as dt
import os
import socket
import time

from labape import notify
from labape.db import session_factory
from labape.engine import maintenance
from labape.engine.runner import claim, run
from labape.models import AuditLog, Job, User, UserError, utcnow

from test_api import _admin_session


def test_sections_roundtrip_and_validation(client):
    _admin_session(client)
    s = client.get("/api/admin/settings").json()
    assert s["backup"]["time_of_day"] == "02:30" and s["retention"]["audit_days"] == 0
    assert s["smtp"]["has_password"] is False and "password" not in s["smtp"]
    r = client.put("/api/admin/settings/backup", json={**s["backup"], "time_of_day": "25:00"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "time_of_day"
    r = client.put("/api/admin/settings/backup", json={**s["backup"], "directory": "relative/dir"})
    assert r.status_code == 422 and "absolute" in r.json()["errors"][0]["message"]
    r = client.put("/api/admin/settings/syslog", json={**s["syslog"], "enabled": True})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host"
    r = client.put("/api/admin/settings/smtp", json={**s["smtp"], "host": "mail.example.test", "from_address": "lab@example.test",
                                                     "username": "lab", "password": "ThisIsMy_FAKE_Password6!"})
    assert r.status_code == 200 and r.json()["has_password"] is True and "password" not in r.json()
    audit = client.get("/api/admin/audit").json()
    assert "ThisIsMy_FAKE_Password6!" not in str(audit)
    assert client.put("/api/admin/settings/nope", json={}).status_code == 404
    # The Errors page retention and the settings share one value.
    assert client.put("/api/admin/errors/retention", json={"days": 30}).status_code == 200
    assert client.get("/api/admin/settings").json()["retention"]["errors_days"] == 30


def test_syslog_test_and_audit_forwarding(client):
    _admin_session(client)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(5)
    port = rx.getsockname()[1]
    try:
        s = client.get("/api/admin/settings").json()["syslog"]
        r = client.put("/api/admin/settings/syslog", json={**s, "enabled": True, "host": "127.0.0.1", "port": port,
                                                           "facility": "local3"})
        assert r.status_code == 200, r.text
        msg = rx.recv(65535).decode()   # the settings change itself is audited, then forwarded
        assert msg.startswith(f"<{19 * 8 + 6}>1 ") and "settings.syslog" in msg
        assert client.post("/api/admin/settings/syslog/test").json()["ok"] is True
        assert "LABaPe syslog test" in rx.recv(65535).decode()
    finally:
        rx.close()


def test_backup_job_list_download_and_purge(client, tmp_root):
    _admin_session(client)
    d = tmp_root / "backups"
    d.mkdir(exist_ok=True)
    old = d / "labape-20000101-000000.dump"
    old.write_bytes(b"old")
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    s = client.get("/api/admin/settings").json()["backup"]
    assert client.put("/api/admin/settings/backup", json={**s, "directory": str(d), "retention_days": 7}).status_code == 200
    job_id = client.post("/api/admin/backups").json()["job_id"]
    assert client.post("/api/admin/backups").status_code == 409          # one at a time
    with session_factory()() as db:
        assert claim(db, "test").id == job_id
    run(job_id, "test")
    with session_factory()() as db:
        assert db.get(Job, job_id).state == "succeeded"
    listing = client.get("/api/admin/backups").json()
    names = [f["name"] for f in listing["files"]]
    assert len(names) == 1 and old.name not in names and listing["last_job"]["state"] == "succeeded"
    r = client.get(f"/api/admin/backups/{names[0]}")
    assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
    assert client.get("/api/admin/backups/..%2Fsecrets").status_code == 404


def test_schedule_backup(client):
    _admin_session(client)
    day = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        assert maintenance.schedule_backup(db, day.replace(hour=2)) is None          # before 02:30
        job = maintenance.schedule_backup(db, day.replace(hour=3))
        assert job is not None and job.requested_by == "system"
        job.created_at = day.replace(hour=3)
        db.commit()
        assert maintenance.schedule_backup(db, day.replace(hour=4)) is None          # already queued today
        assert maintenance.schedule_backup(db, day.replace(day=12, hour=3)) is not None


def test_purge_history(client, tmp_root):
    _admin_session(client)
    old = utcnow() - dt.timedelta(days=400)
    log = tmp_root / "old-job.log"
    log.write_text("x")
    with session_factory()() as db:
        db.add_all([AuditLog(actor="a", action="old", at=old), UserError(at=old, username="u", path="/x"),
                    Job(type="environment.deploy", requested_by="u", state="succeeded", finished_at=old,
                        log_path=str(log))])
        db.commit()
        out = maintenance.purge_history(db)
        assert out["job_logs"] == 1 and out["errors"] == 1 and out["audit"] == 0 and not log.exists()
    s = client.get("/api/admin/settings").json()["retention"]
    client.put("/api/admin/settings/retention", json={**s, "audit_days": 365})
    with session_factory()() as db:
        assert maintenance.purge_history(db)["audit"] == 1


def test_job_email(client, monkeypatch):
    _admin_session(client)
    sent = []
    monkeypatch.setattr(notify, "email_in_background", lambda cfg, pw, to, subject, body, **kw: sent.append((to, subject)))
    s = client.get("/api/admin/settings").json()["smtp"]
    client.put("/api/admin/settings/smtp", json={**s, "enabled": True, "host": "mail.example.test",
                                                 "from_address": "lab@example.test", "notify_jobs": True})
    with session_factory()() as db:
        db.add(User(provider="oidc", subject="s1", username="alice", email="alice@example.test"))
        job = Job(type="environment.deploy", requested_by="alice", state="failed", exit_code=2)
        db.add(job)
        db.commit()
        maintenance.appsettings._cache.clear()
        maintenance.notify_job(db, job)
    assert sent == [(["alice@example.test"], f"LABaPe job {job.id} failed: environment.deploy")]


def test_directory_group_search(client, monkeypatch):
    import httpx
    from labape.api import directory
    _admin_session(client)
    with session_factory()() as db:
        db.add(User(provider="oidc", subject="s2", username="bob", groups=["lab-ops", "lab-dev"]))
        db.commit()
    r = client.get("/api/directory/groups?q=ops").json()
    assert r == {"source": "known", "groups": ["lab-ops"], "warning": ""}
    s = client.get("/api/admin/settings").json()["directory"]
    assert s["has_token"] is False
    r = client.put("/api/admin/settings/directory", json={**s, "api_url": "not a url"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "api_url"
    r = client.put("/api/admin/settings/directory", json={**s, "enabled": True, "api_url": "https://ak.example.test",
                                                         "token": "ThisIsMy_FAKE_Password6!"})
    assert r.status_code == 200 and r.json()["has_token"] is True and "token" not in r.json()
    calls = []

    def fake_get(url, params, headers, timeout, verify):
        calls.append((url, params["search"], headers["Authorization"]))
        return httpx.Response(200, json={"results": [{"name": "lab-ops"}, {"name": "lab-ops-admins"}]},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(directory.httpx, "get", fake_get)
    directory.appsettings._cache.clear()
    r = client.get("/api/directory/groups?q=ops").json()
    assert r["source"] == "authentik" and r["groups"] == ["lab-ops", "lab-ops-admins"]
    assert calls[0] == ("https://ak.example.test/api/v3/core/groups/", "ops", "Bearer ThisIsMy_FAKE_Password6!")
    monkeypatch.setattr(directory.httpx, "get", lambda url, **kw: httpx.Response(
        403, request=httpx.Request("GET", url)))
    r = client.get("/api/directory/groups?q=dev").json()
    assert r["source"] == "known" and r["groups"] == ["lab-dev"] and "can't view groups" in r["warning"]
    r = client.post("/api/admin/settings/directory/test")
    assert r.status_code == 422 and "HTTP 403" in r.json()["errors"][0]["message"]


def test_syslog_tls_trusts_the_platform_root(monkeypatch, tmp_root):
    """Design §27: audit syslog over TLS verifies a collector whose certificate the platform CA
    signed, with verification on; an unknown CA is still refused."""
    import datetime as dt
    import ssl
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from labape import ca, notify
    from labape.appsettings import SyslogSettings

    def cert(subject, issuer, key, issuer_key, is_ca):
        now = dt.datetime.now(dt.timezone.utc)
        b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
             .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(hours=1))
             .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
             # Python 3.13's default context is X.509-strict: it wants key identifiers.
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False))
        if is_ca:
            b = b.add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                              content_commitment=False, key_encipherment=False,
                                              data_encipherment=False, key_agreement=False, encipher_only=False,
                                              decipher_only=False), critical=True)
        if not is_ca:
            b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        return b.sign(issuer_key, hashes.SHA256())

    root_key = ec.generate_private_key(ec.SECP256R1())
    root = cert("Test Platform Root", "Test Platform Root", root_key, root_key, True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = cert("localhost", "Test Platform Root", leaf_key, root_key, False)
    crt, key = tmp_root / "collector.crt", tmp_root / "collector.key"
    crt.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(crt, key)

    def serve(listener, got):
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                got.append(tls.recv(65535).decode())
        except (ssl.SSLError, OSError) as exc:
            got.append(f"error: {exc}")

    def attempt(root_pem):
        if root_pem is None:
            monkeypatch.setattr(ca, "root_pem", lambda: (_ for _ in ()).throw(ca.CaError("no CA")))
        else:
            monkeypatch.setattr(ca, "root_pem", lambda: root_pem)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        got: list = []
        t = threading.Thread(target=serve, args=(listener, got), daemon=True)
        t.start()
        cfg = SyslogSettings(enabled=True, host="localhost", port=listener.getsockname()[1], protocol="tls")
        try:
            notify.send_syslog(cfg, {"action": "test.tls", "actor": "pytest", "outcome": "ok", "detail": {}})
            sent = True
        except (ssl.SSLError, OSError) as exc:
            sent = False
            got.append(f"client: {exc!r}")
        t.join(5)
        listener.close()
        return sent, got

    sent, got = attempt(root.public_bytes(serialization.Encoding.PEM).decode())
    assert sent and "test.tls" in got[0], got
    sent, got = attempt(None)        # no platform root: the system roots don't know this CA
    assert not sent
