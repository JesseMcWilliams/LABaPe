"""Batch Y1 (design §29.1): the stack's syslog collector: rendered rsyslog settings,
certificates, settings validation, the Syslog API, and KVM hosts forwarding their logs."""
import datetime as dt

import pytest

from labape import ca, syslogstack
from labape.appsettings import SyslogCollectorSettings, SyslogForward
from labape.db import session_factory
from labape.engine import hostadmin
from labape.models import KvmHost
from labape.security import Principal, current_principal

from test_api import _admin_session, _host


def test_forward_actions_cover_transports_filters_and_tls():
    udp = syslogstack.forward_action(0, SyslogForward(name="siem", host="10.0.0.9", transport="udp", format="rfc3164"))
    assert 'protocol="udp"' in udp and "RSYSLOG_ForwardFormat" in udp and "TCP_Framing" not in udp
    assert udp.startswith("action(")                                    # no filter: everything
    tls = syslogstack.forward_action(1, SyslogForward(name="s2", host="siem.example", port=6514, transport="tls",
                                                      client_cert=True, facilities=["auth", "local0"],
                                                      min_severity="warning"))
    assert tls.startswith('if prifilt("auth,local0.*") and $syslogseverity <= 4 then {')
    assert 'StreamDriverAuthMode="x509/name" StreamDriverPermittedPeers="siem.example"' in tls
    assert 'StreamDriver.CertFile="/tls/client.crt"' in tls and 'TCP_Framing="octet-counted"' in tls
    assert 'queue.filename="forward_1"' in tls
    ip = syslogstack.forward_action(2, SyslogForward(name="s3", host="10.0.0.9", transport="tls"))
    assert 'x509/certvalid' in ip and "PermittedPeers" not in ip
    anon = syslogstack.forward_action(3, SyslogForward(name="s4", host="10.0.0.9", transport="tls", verify_tls=False))
    assert 'StreamDriverAuthMode="anon"' in anon


def test_render_writes_only_changes_and_the_client_cert_flag(tmp_path):
    cfg = SyslogCollectorSettings()
    assert syslogstack.render(cfg, tmp_path) is False                    # defaults: nothing to write
    cfg = SyslogCollectorSettings(require_client_cert=False, forwards=[
        SyslogForward(name="on", host="a.example"), SyslogForward(name="off", host="b.example", enabled=False)])
    assert syslogstack.render(cfg, tmp_path) is True
    fwd = (tmp_path / "forward.conf").read_text()
    assert "# on" in fwd and "b.example" not in fwd
    assert (tmp_path / "client-certs-optional").is_file()
    assert syslogstack.render(cfg, tmp_path) is False                    # unchanged: the collector isn't restarted
    assert syslogstack.render(SyslogCollectorSettings(), tmp_path) is True
    assert not (tmp_path / "forward.conf").exists() and not (tmp_path / "client-certs-optional").exists()


def _self_signing(monkeypatch, root_pem="ROOT"):
    """ca.sign stand-in: a certificate for the CSR's key and names, valid 90 days."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    issuer = ec.generate_private_key(ec.SECP256R1())
    calls = []

    def sign(csr_pem, hours=720):
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        calls.append(csr)
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(csr.subject)
                .public_key(csr.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now).not_valid_after(now + dt.timedelta(hours=hours))
                .add_extension(csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value, False)
                .sign(issuer, hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    monkeypatch.setattr(ca, "configured", lambda: True)
    monkeypatch.setattr(ca, "root_pem", lambda: root_pem)
    monkeypatch.setattr(ca, "sign", sign)
    return calls


def test_certificates_are_issued_once_and_follow_the_address(tmp_path, monkeypatch):
    calls = _self_signing(monkeypatch)
    cfg = SyslogCollectorSettings(address="172.21.20.11")
    assert syslogstack.ensure_certs(cfg, tmp_path) == ["server", "client"]
    assert (tmp_path / "root.crt").read_text() == "ROOT"
    assert syslogstack._names(tmp_path / "server.crt") == {"labape-syslog", "localhost", "172.21.20.11"}
    assert oct((tmp_path / "server.key").stat().st_mode)[-3:] in ("600", "666")   # 666: Windows ignores modes
    assert syslogstack.ensure_certs(cfg, tmp_path) == []                         # still valid: nothing new
    assert syslogstack.ensure_certs(SyslogCollectorSettings(address="syslog.lab"), tmp_path) == ["server"]
    assert len(calls) == 3


def test_collector_settings_validation(client):
    _admin_session(client)
    bad = client.put("/api/admin/settings/syslog_collector",
                      json={"forwards": [{"name": "x", "host": "siem", "facilities": ["nope"]}]})
    assert bad.status_code == 422 and "unknown facilities" in bad.json()["errors"][0]["message"]
    dup = client.put("/api/admin/settings/syslog_collector",
                     json={"forwards": [{"name": "x", "host": "a"}, {"name": "x", "host": "b"}]})
    assert dup.status_code == 422
    inject = client.put("/api/admin/settings/syslog_collector", json={"forwards": [{"name": "x", "host": 'a" b'}]})
    assert inject.status_code == 422
    ok = client.put("/api/admin/settings/syslog_collector",
                    json={"retention_days": 30, "forwards": [{"name": "SIEM 1", "host": "siem.example",
                                                              "transport": "tls", "min_severity": "notice"}]})
    assert ok.status_code == 200 and ok.json()["forwards"][0]["min_severity"] == "notice"
    assert client.get("/api/admin/settings").json()["syslog_collector"]["retention_days"] == 30


def test_syslog_api_roles_and_absent_collector(client):
    _admin_session(client)
    st = client.get("/api/syslog/status").json()
    assert st["present"] is False and st["retention_days"] == 90 and "local0" in st["facilities"]
    assert client.get("/api/syslog/messages").status_code == 404
    for roles, code in (({"viewer"}, 403), ({"auditor"}, 200)):
        client.app.dependency_overrides[current_principal] = lambda roles=roles: Principal(
            user_id=99, username="someone", display_name="", roles=roles)
        assert client.get("/api/syslog/status").status_code == code
    client.app.dependency_overrides.clear()


def test_query_rejects_unknown_names():
    with pytest.raises(ValueError):
        syslogstack.query(object(), facility="bogus")
    with pytest.raises(ValueError):
        syslogstack.query(object(), min_severity="loud")


def test_host_log_forwarding(client, monkeypatch):
    _admin_session(client)
    _host(client)
    _self_signing(monkeypatch, root_pem="-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n")
    seen = []

    class FakeSsh:
        def python(self, script, args, timeout=120):
            seen.append(args)
            return {"ok": True}

    monkeypatch.setattr(hostadmin, "for_host", lambda h: FakeSsh())
    client.put("/api/admin/settings/syslog_collector", json={"address": "172.21.20.11"})
    r = client.post("/api/hosts/1/log-forwarding", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["log_forward"] is True and r.json()["log_forward_until"]
    a = seen[-1]
    assert a["enable"] and 'target="172.21.20.11"' in a["conf"] and "x509/certvalid" in a["conf"]
    assert "BEGIN PRIVATE KEY" in a["key"] and "root" in a["root"]
    assert client.post("/api/hosts/1/log-forwarding", json={"enabled": False}).json()["log_forward"] is False
    assert seen[-1] == {"enable": False}
    assert client.get("/api/admin/audit?action=host.log_forwarding").json()
    assert 'StreamDriverPermittedPeers="syslog.lab"' in hostadmin.forward_conf("syslog.lab", "kvm2")


def test_host_log_forwarding_failure_is_kept(client, monkeypatch):
    _admin_session(client)
    _host(client)
    _self_signing(monkeypatch)

    class Broken:
        def python(self, script, args, timeout=120):
            raise hostadmin.AdminError("installing rsyslog failed: no network")

    monkeypatch.setattr(hostadmin, "for_host", lambda h: Broken())
    client.put("/api/admin/settings/syslog_collector", json={"address": "172.21.20.11"})
    r = client.post("/api/hosts/1/log-forwarding", json={"enabled": True})
    assert r.status_code == 422 and "no network" in r.json()["errors"][0]["message"]
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        assert h.log_forward is False and "no network" in h.log_forward_error


def test_audit_trail_adopts_the_collector_once(client):
    from labape import appsettings
    with session_factory()() as db:
        assert syslogstack.adopt_audit_trail(db) is True
        cfg = appsettings.get(db, "syslog")
        assert (cfg.enabled, cfg.host, cfg.port, cfg.protocol, cfg.client_cert) == (True, "labape-syslog", 6514, "tls", True)
        appsettings.put(db, "syslog", appsettings.SyslogSettings(enabled=False))
        assert syslogstack.adopt_audit_trail(db) is False                 # a saved choice, even off, stays
        assert appsettings.get(db, "syslog").enabled is False
