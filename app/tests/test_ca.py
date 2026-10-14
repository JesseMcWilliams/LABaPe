"""Batch K: platform CA — CSR name checks, the signing endpoint's token rules, the root download."""
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from labape import ca
from labape.api.ca import token_name
from labape.db import session_factory
from labape.engine import runner
from labape.models import Job, utcnow
from labape.security import put_secret

from test_api import _admin_session, _host


def _csr(cn, dns=(), ips=()):
    key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    b = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
    if names:
        b = b.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return b.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def test_csr_name_rules():
    pem = _csr("web1.lab.test", ["web1", "web1.lab.test"], ["172.21.50.7"])
    assert ca.csr_names(pem) == ("web1.lab.test", {"web1", "web1.lab.test"}, {"172.21.50.7"})
    ca.check_names(pem, "web1", {"web1", "web1.lab.test"}, {"172.21.50.7"})
    with pytest.raises(ca.CaError, match="dc1"):
        ca.check_names(_csr("web1", ["dc1.lab.test"]), "web1", {"web1"}, set())
    with pytest.raises(ca.CaError, match="10.0.0.9"):
        ca.check_names(_csr("web1", ["web1"], ["10.0.0.9"]), "web1", {"web1"}, set())
    with pytest.raises(ca.CaError):
        ca.csr_names("not a csr")


def test_root_download_without_ca(client):
    assert client.get("/api/ca/root.crt").status_code == 404


def test_sign_endpoint(client, monkeypatch, tmp_root):
    _admin_session(client)
    _host(client)
    g = {"name": "web", "os": "rocky9", "roles": ["linux_server"], "count": 1}
    env_id = client.post("/api/environments", json={"name": "caenv", "kvm_host_id": 1, "host_groups": [g]}
                         ).json()["environment"]["id"]
    root = tmp_root / "root_ca.crt"
    root.write_text("-----BEGIN CERTIFICATE-----\nROOT\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(ca, "root_pem", lambda: root.read_text())
    signed = []
    monkeypatch.setattr(ca, "sign", lambda pem, hours=720: signed.append(pem) or "LEAF+INTERMEDIATE")
    with session_factory()() as db:
        job = Job(type="environment.deploy", environment_id=env_id, requested_by="admin", state="running",
                  started_at=utcnow())
        db.add(job)
        db.commit()
        job_id = job.id
        put_secret(db, token_name(job_id), "right-token")
    good = _csr("web1", ["web1"])

    def post(host="web1", csr=good, token="right-token", job=job_id):
        return client.post("/api/engine/certificates", json={"host": host, "csr": csr},
                           headers={"X-LABaPe-Job": str(job), "X-LABaPe-Token": token})

    assert post(token="wrong").status_code == 403
    assert post(host="dc1").status_code == 403                                     # not in this environment
    assert post(csr=_csr("web1", ["web1", "evil.example"])).status_code == 422      # asks for another name
    r = post()
    assert r.status_code == 200 and r.json()["certificate"] == "LEAF+INTERMEDIATE" and "ROOT" in r.json()["root"]
    assert len(signed) == 1
    with session_factory()() as db:
        db.get(Job, job_id).state = "succeeded"
        db.commit()
    assert post().status_code == 403                                               # only while the job runs


def test_cert_env(monkeypatch):
    monkeypatch.setattr(ca, "configured", lambda: False)
    assert runner._cert_env(1, "environment.deploy", False) == {}
    monkeypatch.setattr(ca, "configured", lambda: True)
    assert runner._cert_env(1, "environment.destroy", False) == {}
    env = runner._cert_env(77, "environment.deploy", False)
    assert env["LABAPE_JOB_ID"] == "77" and env["LABAPE_CERT_API"] == "http://labape-api:8000" and len(env["LABAPE_CERT_TOKEN"]) > 30
    assert runner._cert_env(78, "environment.deploy", True)["LABAPE_CERT_API"] == "http://testserver"


def test_ca_sign_cli(client, monkeypatch, capsys):
    """labape ca-sign: hours are checked, a CA failure is reported, a signature is audited."""
    import io

    from labape import ca, cli
    from labape.db import session_factory
    from labape.models import AuditLog
    monkeypatch.setattr("sys.stdin", io.StringIO("not a request"))
    assert cli.main(["ca-sign", "--hours", "0"]) == 2
    assert cli.main(["ca-sign"]) == 1
    assert "not a PEM certificate request" in capsys.readouterr().err
    monkeypatch.setattr(ca, "csr_names", lambda csr: ("kvm1", {"kvm1", "kvm1.lab.test"}, {"192.0.2.10"}))
    monkeypatch.setattr(ca, "sign", lambda csr, hours: "LEAF\nINTERMEDIATE\n")
    monkeypatch.setattr(ca, "root_pem", lambda: "ROOT\n")
    monkeypatch.setattr("sys.stdin", io.StringIO("a request"))
    assert cli.main(["ca-sign", "--hours", "48", "--with-root"]) == 0
    assert capsys.readouterr().out == "LEAF\nINTERMEDIATE\nROOT\n"
    with session_factory()() as db:
        row = db.query(AuditLog).filter(AuditLog.action == "ca.sign").one()
    assert row.object_id == "kvm1" and row.detail["hours"] == 48 and row.detail["ips"] == ["192.0.2.10"]


def test_trust_adds_the_platform_root(monkeypatch):
    # Authentik is served with a platform-CA certificate (design §47): OIDC calls trust that root too.
    import datetime as dt
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test LABaPe Root")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    monkeypatch.setattr(ca, "root_pem", lambda: pem)
    assert any(c.get("subject") == ((("commonName", "Test LABaPe Root"),),) for c in ca.trust().get_ca_certs())

    def missing():
        raise ca.CaError("no root")
    monkeypatch.setattr(ca, "root_pem", missing)
    assert ca.trust() is not None                    # the system roots only
