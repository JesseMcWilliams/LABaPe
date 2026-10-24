"""Question 198: VM SSH and WinRM check who they talk to (engine/vmidentity.py)."""
import http.server
import shutil
import ssl
import subprocess
import threading

import pytest
import requests

from labape import remote
from labape.db import session_factory
from labape.engine import vmexec, vmidentity
from labape.models import Environment, KvmHost, VmIdentity

KEYS = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOnlyATest\nssh-rsa AAAAB3NzaC1yc2EAAAATest"


def test_key_lines_drop_the_host_and_comments():
    text = "# 10.0.0.5:22 SSH-2.0-OpenSSH\n10.0.0.5 ssh-rsa AAAAB3NzaC1yc2EAAAATest\n|1|abc|def ssh-ed25519 AAAAC3Test\n"
    assert vmidentity.key_lines(text) == "ssh-ed25519 AAAAC3Test\nssh-rsa AAAAB3NzaC1yc2EAAAATest"


def test_ssh_options_check_when_recorded_and_learn_otherwise(tmp_path):
    kh = tmp_path / "known_hosts"
    opts = vmidentity.ssh_options(kh, "10.0.0.5", KEYS)
    assert "StrictHostKeyChecking=yes" in opts and f"UserKnownHostsFile={kh}" in opts and "HashKnownHosts=no" in opts
    assert kh.read_text().splitlines() == ["10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOnlyATest",
                                           "10.0.0.5 ssh-rsa AAAAB3NzaC1yc2EAAAATest"]
    opts = vmidentity.ssh_options(kh, "10.0.0.5", None)
    assert "StrictHostKeyChecking=accept-new" in opts and kh.read_text() == ""


def test_the_ssh_command_uses_the_record(tmp_path):
    params = {"username": "labape", "hostname": "10.0.0.5", "port": "22", "private-key": "KEY", "known-keys": KEYS}
    argv, _, _ = vmexec._ssh_argv(params, "true", tmp_path, None)
    assert "StrictHostKeyChecking=yes" in argv and "StrictHostKeyChecking=no" not in argv
    assert not any("/dev/null" in a for a in argv)
    del params["known-keys"]
    argv, _, _ = vmexec._ssh_argv(params, "true", tmp_path, None)
    assert "StrictHostKeyChecking=accept-new" in argv


def test_learning_keeps_the_first_record(client, tmp_path):
    kh = tmp_path / "kh"
    kh.write_text("10.0.0.5 ssh-ed25519 AAAAFirst\n")
    assert vmidentity.learn_file("10.0.0.5", kh) is True
    kh.write_text("10.0.0.5 ssh-ed25519 AAAAImpostor\n")
    assert vmidentity.learn_file("10.0.0.5", kh) is False
    with session_factory()() as db:
        assert vmidentity.trusted(db, "10.0.0.5", "ssh") == "ssh-ed25519 AAAAFirst"
    empty = tmp_path / "empty"
    empty.write_text("")
    assert vmidentity.learn_file("10.0.0.6", empty) is False


def test_forget_by_address_and_by_environment(client):
    with session_factory()() as db:
        host = KvmHost(name="h", libvirt_uri="qemu:///system")
        db.add(host)
        db.commit()
        env = Environment(name="e1", kvm_host_id=host.id, spec={}, created_by="t")
        db.add(env)
        db.commit()
        vmidentity.remember(db, "10.0.0.5", "ssh", KEYS, env.id)
        vmidentity.remember(db, "10.0.0.5", "winrm", "ab" * 32, env.id)
        vmidentity.remember(db, "10.0.0.9", "ssh", KEYS, None)
        assert vmidentity.forget(db, address="10.0.0.9") == 1
        assert vmidentity.forget(db, env_id=env.id) == 2
        assert db.query(VmIdentity).count() == 0
        assert vmidentity.forget(db) == 0


def test_vm_target_carries_the_recorded_keys(client):
    with session_factory()() as db:
        assert remote._vm_host_keys(db, "10.0.0.5") == {}
        vmidentity.remember(db, "10.0.0.5", "ssh", KEYS)
        got = remote._vm_host_keys(db, "10.0.0.5")
        assert got["known-keys"] == KEYS
        assert got["host-key"] == "10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOnlyATest"


def test_record_env_stores_what_the_vms_answer(client, monkeypatch):
    monkeypatch.setattr(vmidentity, "scan_ssh", lambda a, *k, **kw: "ssh-ed25519 AAAAscan-" + a)
    monkeypatch.setattr(vmidentity, "fetch_certificate", lambda a, *k, **kw: "cd" * 32)
    inv = {"all": {"hosts": {"l1": {"ansible_host": "10.0.0.5", "ansible_connection": "ssh"},
                             "w1": {"ansible_host": "10.0.0.6", "ansible_connection": "winrm", "ansible_port": 5986},
                             "x": {}}}}
    with session_factory()() as db:
        host = KvmHost(name="h2", libvirt_uri="qemu:///system")
        db.add(host)
        db.commit()
        env = Environment(name="e2", kvm_host_id=host.id, spec={}, created_by="t")
        db.add(env)
        db.commit()
        done = vmidentity.record_env(db, env, inv)
        assert done == {"l1": "ssh", "w1": "winrm"}
        assert vmidentity.trusted(db, "10.0.0.5", "ssh") == "ssh-ed25519 AAAAscan-10.0.0.5"
        assert vmidentity.trusted(db, "10.0.0.6", "winrm") == "cd" * 32


# --- the WinRM pin, against a real TLS server with a self-signed certificate -----------------------

@pytest.fixture()
def tls_server(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("no openssl")
    crt, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-days", "2", "-subj", "/CN=SOMEOTHERNAME"], check=True, capture_output=True)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], vmidentity.fingerprint(ssl.PEM_cert_to_DER_cert(crt.read_text()))
    srv.shutdown()


def test_fetch_certificate_matches_what_the_server_presents(tls_server):
    port, fp = tls_server
    assert vmidentity.fetch_certificate("127.0.0.1", port) == fp


def test_pinned_adapter_accepts_only_the_recorded_certificate(tls_server):
    port, fp = tls_server
    url = f"https://127.0.0.1:{port}/"

    def get(pin):
        s = requests.Session()
        s.verify = False
        s.mount("https://", vmexec._PinnedAdapter(pin))
        return s.get(url, timeout=5)

    assert get(fp).text == "ok"                      # right certificate, though the name doesn't match
    with pytest.raises(requests.exceptions.SSLError):
        get("00" * 32)


def test_pywinrm_session_uses_the_pin(tls_server):
    import winrm
    port, fp = tls_server

    def attempt(pin):
        sess = winrm.Session(f"https://127.0.0.1:{port}/wsman", auth=("u", "p"), transport="ntlm",
                             server_cert_validation="ignore", operation_timeout_sec=5, read_timeout_sec=10)
        vmexec._pin_winrm(sess, pin)
        assert isinstance(sess.protocol.transport.build_session().get_adapter("https://127.0.0.1"), vmexec._PinnedAdapter)
        try:
            sess.run_ps("1")
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        return ""

    assert "ingerprint" in attempt("00" * 32)                 # refused before anything is sent
    assert "ingerprint" not in attempt(fp)                    # the right certificate gets as far as the (fake) server
