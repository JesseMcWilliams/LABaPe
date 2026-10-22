"""Batch C2 (design §65): the certificate register, requests, roles, revoking, settings."""
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from labape import ca, certs
from labape.db import session_factory
from labape.models import Certificate, Environment, Grant, KvmHost
from labape.security import Principal, current_principal

from test_api import _admin_session


def _pem(c):
    return c.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture()
def fake_ca(monkeypatch):
    """A test CA standing in for step-ca: signs a CSR with the SANs it asked for."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = dt.datetime.now(dt.timezone.utc)
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    calls = {"revoked": []}

    def sign(csr_pem, hours=720, uses=None):
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        b = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(name).public_key(csr.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now)
             .not_valid_after(now + dt.timedelta(hours=hours)))
        for ext in csr.extensions:
            b = b.add_extension(ext.value, ext.critical)
        return _pem(b.sign(key, hashes.SHA256())) + _pem(root)

    monkeypatch.setattr(ca, "sign", sign)
    monkeypatch.setattr(ca, "revoke", lambda serial, reason="": calls["revoked"].append((serial, reason)))
    monkeypatch.setattr(ca, "root_pem", lambda: _pem(root))
    monkeypatch.setattr(ca, "health", lambda: (True, "ok"))
    monkeypatch.setattr(ca, "configured", lambda: True)
    monkeypatch.setattr(ca, "provisioners", lambda: [{"type": "JWK", "name": "labape-runner"}])
    return calls


def _as(client, username, *roles, uid=50):
    client.app.dependency_overrides[current_principal] = lambda: Principal(uid, username, username, [], set(roles))


def _config(client):
    _admin_session(client)
    r = client.put("/api/certificates/settings", json={"allowed_domains": ["jlm.abq"], "allowed_networks": ["172.21.0.0/16"],
                                                      "default_days": 30, "max_days": 60})
    assert r.status_code == 200, r.text


def test_a_requester_gets_a_certificate_and_a_key_that_is_not_kept(client, fake_ca):
    _config(client)
    _as(client, "bob", "ca_requester")
    r = client.post("/api/certificates", json={"common_name": "App1.jlm.abq", "dns": "app1-alt.jlm.abq", "ips": "172.21.50.7"})
    assert r.status_code == 201, r.text
    j = r.json()
    assert "BEGIN PRIVATE KEY" in j["key"] and j["chain"].count("BEGIN CERTIFICATE") == 2 and "BEGIN CERTIFICATE" in j["root"]
    c = j["certificate"]
    assert c["subject"] == "app1.jlm.abq" and c["sans"] == ["app1-alt.jlm.abq", "app1.jlm.abq", "172.21.50.7"] and c["requested_by"] == "bob"
    assert 28 <= c["days_left"] <= 30 and c["state"] == "active" and "pem" in c
    with session_factory()() as db:
        row = db.get(Certificate, c["id"])
        assert "PRIVATE KEY" not in row.pem and row.source == "request"
    # a CSR of the requester's own comes back without a key
    key, csr = certs.make_request("web.jlm.abq", ["web.jlm.abq"], [])
    r = client.post("/api/certificates", json={"csr": csr, "days": 10})
    assert r.status_code == 201 and r.json()["key"] == "" and r.json()["certificate"]["days_left"] <= 10


def test_names_and_lifetimes_are_limited_for_requesters_only(client, fake_ca):
    _config(client)
    _as(client, "bob", "ca_requester")
    post = lambda **b: client.post("/api/certificates", json=b)                    # noqa: E731
    for bad in ({"common_name": "evil.example"}, {"common_name": "*.jlm.abq"}, {"common_name": "a.jlm.abq", "ips": "8.8.8.8"},
                {"common_name": "a.jlm.abq", "ips": "nope"}, {"common_name": "bad name.jlm.abq"}):
        r = post(**bad)
        assert r.status_code == 422, bad
    assert "You may not request" in post(common_name="evil.example").json()["errors"][0]["message"]
    assert post(common_name="a.jlm.abq", days=61).status_code == 422               # over the longest allowed
    assert post(common_name="").status_code == 422                                  # nothing asked for
    _as(client, "carol", "viewer", uid=51)
    assert post(common_name="a.jlm.abq").status_code == 403                         # no requester role
    _as(client, "root", "ca_admin", uid=52)
    assert post(common_name="*.anything.example", ips="8.8.8.8").status_code == 201  # CA administrators are not limited


def test_who_sees_which_certificates(client, fake_ca):
    _config(client)
    with session_factory()() as db:
        h = KvmHost(name="k", libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t")
        db.add(h)
        db.flush()
        env = Environment(name="certenv", kvm_host_id=h.id, created_by="dave", spec={"host_groups": []})
        db.add(env)
        db.flush()
        db.add(Grant(object_type="environment", object_id=env.id, principal_type="user", principal="erin", level="viewer"))
        db.commit()
        env_id = env.id
    _as(client, "bob", "ca_requester")
    mine = client.post("/api/certificates", json={"common_name": "bob.jlm.abq"}).json()["certificate"]["id"]
    tied = client.post("/api/certificates", json={"common_name": "tied.jlm.abq", "environment_id": 999}).status_code
    assert tied == 404                                                              # an environment he can't see
    _as(client, "dave", "ca_requester", uid=53)
    with session_factory()() as db:
        from labape.permissions import add_owner
        add_owner(db, "environment", env_id, "dave")
    theirs = client.post("/api/certificates", json={"common_name": "dave.jlm.abq", "environment_id": env_id}).json()["certificate"]["id"]
    ids = lambda who, **q: sorted(c["id"] for c in client.get("/api/certificates", params=q).json())   # noqa: E731
    _as(client, "bob", "ca_requester")
    assert ids("bob") == [mine]
    _as(client, "erin", "viewer", uid=54)
    assert ids("erin") == [theirs]                                                  # her environment's certificate, not bob's
    assert client.get(f"/api/certificates/{mine}").status_code == 404
    assert client.get(f"/api/certificates/{theirs}").status_code == 200 and "pem" in client.get(f"/api/certificates/{theirs}").json()
    assert client.get("/api/certificates", params={"scope": "all"}).status_code == 403
    for role in ("auditor", "ca_admin", "admin"):
        _as(client, "x" + role, role, uid=60)
        assert ids("x", scope="all") == sorted([mine, theirs]), role
    _as(client, "xauditor", "auditor", uid=61)
    assert ids("a") == []                                                           # "mine" for an auditor is what they asked for
    assert client.get("/api/certificates/ca").status_code == 200
    _as(client, "erin", "viewer", uid=54)
    assert client.get("/api/certificates/ca").status_code == 403
    dl = client.get(f"/api/certificates/{theirs}/download")
    assert dl.status_code == 200 and "BEGIN CERTIFICATE" in dl.text and "dave.jlm.abq.crt" in dl.headers["content-disposition"]


def test_revoking_is_for_ca_administrators(client, fake_ca):
    _config(client)
    _as(client, "bob", "ca_requester")
    cid = client.post("/api/certificates", json={"common_name": "r.jlm.abq"}).json()["certificate"]["id"]
    assert client.post(f"/api/certificates/{cid}/revoke", json={"reason": "x"}).status_code == 403
    _as(client, "root", "ca_admin", uid=52)
    r = client.post(f"/api/certificates/{cid}/revoke", json={"reason": "key leaked"})
    assert r.status_code == 200 and r.json()["state"] == "revoked" and r.json()["revoked_by"] == "root"
    serial = r.json()["serial"]
    assert fake_ca["revoked"] == [(serial, "key leaked")] and serial.isdigit()
    assert client.post(f"/api/certificates/{cid}/revoke", json={}).status_code == 409           # already revoked
    states = {c["id"]: c["state"] for c in client.get("/api/certificates", params={"scope": "all", "state": "revoked"}).json()}
    assert states == {cid: "revoked"}


def test_settings_import_and_the_ca_page(client, fake_ca):
    _config(client)
    _as(client, "bob", "ca_requester")
    assert client.put("/api/certificates/settings", json={"allowed_domains": []}).status_code == 403
    assert client.get("/api/certificates/settings").json()["can_edit"] is False
    _as(client, "root", "ca_admin", uid=52)
    s = client.get("/api/certificates/settings").json()
    assert s["allowed_domains"] == ["jlm.abq"] and s["can_edit"] and "always_allowed" in s
    assert client.put("/api/certificates/settings", json={"allowed_networks": ["nope"]}).status_code == 422
    assert client.put("/api/certificates/settings", json={"default_days": 50, "max_days": 20}).status_code == 422
    # a certificate signed elsewhere can be added once
    key, csr = certs.make_request("old.jlm.abq", ["old.jlm.abq"], [])
    chain = ca.sign(csr, hours=48)
    a = client.post("/api/certificates/import", json={"pem": chain, "note": "before the register"})
    b = client.post("/api/certificates/import", json={"pem": chain})
    assert a.status_code == 201 and a.json()["source"] == "imported" and b.json()["id"] == a.json()["id"]
    assert client.post("/api/certificates/import", json={"pem": "x" * 200}).status_code == 422
    ca_page = client.get("/api/certificates/ca").json()
    assert ca_page["healthy"] is True and ca_page["root"]["subject"] == "Test Root CA" and ca_page["total"] == 1
    assert ca_page["provisioners"] == [{"type": "JWK", "name": "labape-runner"}]


def test_signings_by_the_engine_and_the_stack_are_recorded(client, fake_ca):
    _admin_session(client)
    key, csr = certs.make_request("vm1.jlm.abq", ["vm1.jlm.abq"], [])
    with session_factory()() as db:
        row = certs.record(db, ca.sign(csr), requested_by="alice", source="engine", environment_id=None, host="vm1")
        again = certs.record(db, row.pem, requested_by="alice", source="engine")
        assert again.id == row.id and row.host == "vm1" and row.source == "engine"
        out = certs.out(db, row)
        assert out["serial_hex"] and out["issuer"] == "Test Root CA" and out["state"] in ("active", "expiring")


def test_revoking_mints_a_token_first_and_puts_the_serial_last(monkeypatch, tmp_path):
    """The step CLI stops reading flags at the first positional argument; `ca revoke` takes a token, not a provisioner."""
    import subprocess
    from labape.config import get_settings
    s = get_settings()
    root, pw = tmp_path / "root.crt", tmp_path / "pw"
    root.write_text("x")
    pw.write_text("y")
    monkeypatch.setattr(s, "ca_url", "https://ca:9000")
    monkeypatch.setattr(s, "ca_root", root)
    monkeypatch.setattr(s, "ca_password_file", pw)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = "eyJ.token.value\n" if argv[2] == "token" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    monkeypatch.setattr(ca.subprocess, "run", run)
    ca.revoke("12345", "key leaked")
    token_cmd, revoke_cmd = calls
    assert token_cmd[:4] == ["step", "ca", "token", "--revoke"] and token_cmd[-1] == "12345" and "--provisioner" in token_cmd
    assert revoke_cmd[:3] == ["step", "ca", "revoke"] and revoke_cmd[-1] == "12345"
    assert revoke_cmd[revoke_cmd.index("--token") + 1] == "eyJ.token.value" and "--provisioner" not in revoke_cmd
    assert revoke_cmd[revoke_cmd.index("--reason") + 1] == "key leaked"
    with pytest.raises(ca.CaError):
        ca.revoke("12a", "")                                                       # a serial is digits


def test_extra_attributes_need_the_switch_and_are_checked_after_signing(client, fake_ca):
    _config(client)
    _as(client, "bob", "ca_requester")
    ask = {"common_name": "app2.jlm.abq", "uses": ["client_auth", "code_signing"]}
    r = client.post("/api/certificates", json=ask)
    assert r.status_code == 422 and "turns them on" in r.text                  # the CA is not set up for them yet
    _as(client, "root", "admin", "ca_admin", uid=1)
    r = client.put("/api/certificates/settings", json={"allowed_domains": ["jlm.abq"], "allowed_networks": ["172.21.0.0/16"],
                                                      "default_days": 30, "max_days": 60, "attributes_enabled": True})
    assert r.status_code == 200 and r.json()["attributes_enabled"] is True
    _as(client, "bob", "ca_requester")
    r = client.post("/api/certificates", json=ask)
    assert r.status_code == 201, r.text
    assert r.json()["certificate"]["uses"] == ["client_auth", "code_signing"]
    r = client.post("/api/certificates", json={"common_name": "app3.jlm.abq", "uses": ["nope"]})
    assert r.status_code == 422
