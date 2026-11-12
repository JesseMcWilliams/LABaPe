"""Batch B2 (plan: Planning_WinRM-And-Host-Keys.md): a WinRM listener on a platform-CA certificate is held to the CA."""
import datetime as dt
import ipaddress
import socket
import ssl
import sys
import threading
import types

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from labape import ca
from labape.db import session_factory
from labape.engine import vmexec, vmidentity


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pki(tmp_path, san_ip="127.0.0.1"):
    """A throwaway root, and a leaf for `san_ip` it signed; returns (root pem, leaf pem file, key file)."""
    now = dt.datetime.now(dt.timezone.utc)
    rk, lk = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    root = (x509.CertificateBuilder().subject_name(_name("Test Root")).issuer_name(_name("Test Root")).public_key(rk.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(rk.public_key()), critical=False).sign(rk, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(_name("vm")).issuer_name(root.subject).public_key(lk.public_key())
            .serial_number(2).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=10))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san_ip))]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(rk.public_key()), critical=False)   # Python's default checks are strict
            .sign(rk, hashes.SHA256()))
    cert_file, key_file = tmp_path / f"leaf-{san_ip}.crt", tmp_path / f"leaf-{san_ip}.key"
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(lk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return root.public_bytes(serialization.Encoding.PEM).decode(), cert_file, key_file


def _listener(cert_file, key_file):
    """A TLS server on a free local port that completes handshakes; returns (port, stop)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    stopped = threading.Event()

    def serve():
        while not stopped.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], lambda: (stopped.set(), srv.close())


def test_a_listener_on_a_platform_certificate_is_recognised(tmp_path, monkeypatch):
    root, crt, key = _pki(tmp_path)
    monkeypatch.setattr(ca, "root_pem", lambda: root)
    port, stop = _listener(crt, key)
    try:
        assert vmidentity.chains_to_platform_ca("127.0.0.1", port) is True
        other_root, *_ = _pki(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path)
        monkeypatch.setattr(ca, "root_pem", lambda: other_root)                    # signed by another root
        assert vmidentity.chains_to_platform_ca("127.0.0.1", port) is False
    finally:
        stop()
    monkeypatch.setattr(ca, "root_pem", lambda: (_ for _ in ()).throw(ca.CaError("no root")))
    assert vmidentity.chains_to_platform_ca("127.0.0.1", 1) is False              # no CA here: nothing to chain to


def test_a_certificate_for_another_address_is_not_accepted(tmp_path, monkeypatch):
    root, crt, key = _pki(tmp_path, san_ip="10.9.9.9")                           # the listener carries a different address
    monkeypatch.setattr(ca, "root_pem", lambda: root)
    port, stop = _listener(crt, key)
    try:
        assert vmidentity.chains_to_platform_ca("127.0.0.1", port) is False
    finally:
        stop()


def test_an_unreachable_vm_raises_so_nothing_is_recorded(tmp_path, monkeypatch):
    root, *_ = _pki(tmp_path)
    monkeypatch.setattr(ca, "root_pem", lambda: root)
    with pytest.raises(OSError):
        vmidentity.chains_to_platform_ca("127.0.0.1", 1, timeout=1)


def test_the_connection_is_held_to_the_ca_or_to_a_pin(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(vmidentity, "chains_to_platform_ca", lambda a, p=5986, t=8.0: seen.setdefault("ca", True))
    monkeypatch.setattr(vmidentity, "fetch_certificate", lambda a, p=5986, t=8.0: "ab" * 32)
    assert vmexec._winrm_pin("10.0.0.5", 5986, "ca") == "ca" and vmexec._winrm_pin("10.0.0.5", 5986, "cd" * 32) == "cd" * 32
    assert vmexec._winrm_pin("10.0.0.6", 5986, None) == "ca"                    # first use: learned as CA
    with session_factory()() as db:
        assert vmidentity.trusted(db, "10.0.0.6", vmidentity.WINRM) == "ca"
    seen.clear()
    monkeypatch.setattr(vmidentity, "chains_to_platform_ca", lambda a, p=5986, t=8.0: False)
    assert vmexec._winrm_pin("10.0.0.7", 5986, None) == "ab" * 32               # a self-made certificate: pinned


def _fake_winrm(monkeypatch, error=None):
    calls = {}

    class Session:
        def __init__(self, url, **kw):
            calls.update(url=url, **kw)
            self.protocol = types.SimpleNamespace(transport=types.SimpleNamespace(build_session=lambda: types.SimpleNamespace(mount=lambda *a: calls.setdefault("mounted", True))))

        def run_ps(self, command):
            if error:
                raise RuntimeError(error)
            return types.SimpleNamespace(status_code=0, std_out=b"ok", std_err=b"")
    monkeypatch.setitem(sys.modules, "winrm", types.SimpleNamespace(Session=Session))
    return calls


def test_a_ca_vm_is_validated_against_the_platform_root_and_a_pinned_one_is_pinned(monkeypatch):
    calls = _fake_winrm(monkeypatch)
    host, params = {"ansible_port": 5986}, {"hostname": "10.0.0.5", "username": "Administrator", "password": "x"}
    r = vmexec._run_windows(host, params, "whoami", 10, 1000, recorded="ca")
    assert r.exit_code == 0 and calls["server_cert_validation"] == "validate" and calls["ca_trust_path"].endswith("root_ca.crt")
    assert "mounted" not in calls                                                  # no pin on top of the CA check
    calls.clear()
    vmexec._run_windows(host, params, "whoami", 10, 1000, recorded="cd" * 32)
    assert calls["server_cert_validation"] == "ignore" and "ca_trust_path" not in calls and calls["mounted"] is True


def test_a_certificate_the_ca_did_not_sign_is_reported_as_a_possible_impersonation(monkeypatch):
    _fake_winrm(monkeypatch, error="HTTPSConnectionPool: SSLError certificate verify failed: unable to get local issuer")
    with pytest.raises(vmexec.ExecError, match="possible impersonation"):
        vmexec._run_windows({"ansible_port": 5986}, {"hostname": "10.0.0.5", "password": "x"}, "whoami", 10, 1000, recorded="ca")
