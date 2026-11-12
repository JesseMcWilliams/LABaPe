"""Batch B3 (plan: Planning_WinRM-And-Host-Keys.md): Ansible's later runs check the VMs against their records."""
import subprocess

from labape.db import session_factory
from labape.engine import vmaccess, vmidentity

INV = {"all": {"hosts": {
    "lin1": {"ansible_host": "10.0.0.5", "ansible_connection": "ssh"},
    "lin2": {"ansible_host": "10.0.0.6"},
    "win1": {"ansible_host": "10.0.0.7", "ansible_connection": "winrm"},
    "win2": {"ansible_host": "10.0.0.8", "ansible_connection": "winrm"},
}}}


def record(client):
    with session_factory()() as db:
        vmidentity.remember(db, "10.0.0.5", vmidentity.SSH, "ssh-ed25519 AAAAkey1")
        vmidentity.remember(db, "10.0.0.7", vmidentity.WINRM, vmidentity.CA)
        vmidentity.remember(db, "10.0.0.8", vmidentity.WINRM, "ab" * 32)           # a pin


def test_without_records_nothing_changes(client, tmp_path):
    inv, env = vmidentity.harden(INV, tmp_path)
    assert inv is INV and env == {} and not (tmp_path / "known_hosts").exists()


def test_recorded_vms_are_checked_and_unrecorded_ones_still_accepted(client, tmp_path):
    record(client)
    inv, env = vmidentity.harden(INV, tmp_path, "-i /keys/old")
    kh = (tmp_path / "known_hosts").read_text()
    assert kh == "10.0.0.5 ssh-ed25519 AAAAkey1\n"                                    # only the recorded VM's key
    assert env["ANSIBLE_HOST_KEY_CHECKING"] == "True" and env["ANSIBLE_SSH_CONTROL_PATH_DIR"] == str(tmp_path)
    assert env["ANSIBLE_SSH_COMMON_ARGS"].startswith("-i /keys/old ") and "StrictHostKeyChecking=accept-new" in env["ANSIBLE_SSH_COMMON_ARGS"]
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in env["ANSIBLE_SSH_COMMON_ARGS"]
    h = inv["all"]["hosts"]
    assert h["win1"]["ansible_winrm_server_cert_validation"] == "validate" and h["win1"]["ansible_winrm_ca_trust_path"].endswith("root_ca.crt")
    assert "ansible_winrm_server_cert_validation" not in h["win2"] and "ansible_winrm_server_cert_validation" not in h["lin1"]
    assert "ansible_winrm_server_cert_validation" not in INV["all"]["hosts"]["win1"]       # the caller's inventory is not changed


def test_the_runs_use_it_and_a_changed_key_explains_itself(client, monkeypatch):
    record(client)
    seen = {}

    def fake_run(argv, **kw):
        seen["env"] = kw["env"]
        seen["inv"] = open(argv[2], encoding="utf-8").read()
        out = ('fatal: [lin1]: UNREACHABLE! => {"changed": false, "msg": "Failed to connect to the host via ssh: Host key verification failed.", "unreachable": true}\n'
               "PLAY RECAP *****\nlin1 : ok=0 changed=0 unreachable=1 failed=0\n")
        return subprocess.CompletedProcess(argv, 4, out, "")
    monkeypatch.setattr(vmaccess.subprocess, "run", fake_run)
    r = vmaccess._ansible(INV, "lin1", "ansible.builtin.ping", {}, env={"ANSIBLE_SSH_COMMON_ARGS": "-i /keys/old"})
    assert r["lin1"] == "unreachable"
    assert seen["env"]["ANSIBLE_HOST_KEY_CHECKING"] == "True" and "-i /keys/old" in seen["env"]["ANSIBLE_SSH_COMMON_ARGS"]
    assert "ansible_winrm_server_cert_validation: validate" in seen["inv"]
    assert "labape vm-identity forget" in r.errors["lin1"]
