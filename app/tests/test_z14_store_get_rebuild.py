"""scripts/lib/store_get.py vault-pass: a command-line vault that nothing can open (the worker rotated the vault password
more times than the store keeps previous ones) is reported, and with --rebuild replaced by the store's current vault,
keeping the old file."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "store_get.py"


@pytest.fixture
def sg(monkeypatch):
    spec = importlib.util.spec_from_file_location("store_get_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # a vault is "ENC:<password>"; it opens with a password file holding that password
    monkeypatch.setattr(mod, "opens", lambda vault, pw_file: vault.read_text().strip() == "ENC:" + Path(pw_file).read_text().strip())
    return mod


class FakeStore:
    def __init__(self, vault="ENC:current", history=()):
        self.values = {"vault_password": "current", "vault_password_history": json.dumps(list(history)), "vault": vault}

    def get(self, name):
        return self.values[name]


def _args(tmp_path, rebuild=False):
    return argparse.Namespace(vault=str(tmp_path / "secrets.vault.yml"), out=str(tmp_path / "pw"), try_file="", rebuild=rebuild)


def test_a_vault_that_opens_is_left_alone(sg, tmp_path):
    (tmp_path / "secrets.vault.yml").write_text("ENC:current\n")
    sg.cmd_vault_pass(FakeStore(), _args(tmp_path, rebuild=True))
    assert (tmp_path / "secrets.vault.yml").read_text() == "ENC:current\n"
    assert not list(tmp_path.glob("*.bak-orphaned-*"))
    assert (tmp_path / "pw").read_text().strip() == "current"


def test_an_orphaned_vault_is_reported_and_not_touched_without_rebuild(sg, tmp_path):
    (tmp_path / "secrets.vault.yml").write_text("ENC:lost\n")
    with pytest.raises(SystemExit):
        sg.cmd_vault_pass(FakeStore(), _args(tmp_path))
    assert (tmp_path / "secrets.vault.yml").read_text() == "ENC:lost\n"
    assert not list(tmp_path.glob("*.bak-orphaned-*"))


def test_rebuild_keeps_the_old_file_and_writes_the_stores_vault(sg, tmp_path):
    (tmp_path / "secrets.vault.yml").write_text("ENC:lost\n")
    sg.cmd_vault_pass(FakeStore(vault="ENC:current"), _args(tmp_path, rebuild=True))
    assert (tmp_path / "secrets.vault.yml").read_text().strip() == "ENC:current"
    backups = list(tmp_path.glob("secrets.vault.yml.bak-orphaned-*"))
    assert len(backups) == 1 and backups[0].read_text() == "ENC:lost\n"
    assert oct((tmp_path / "secrets.vault.yml").stat().st_mode & 0o777) in ("0o600", "0o666")       # 0600 where modes exist


def test_rebuild_that_gives_a_vault_that_does_not_open_puts_everything_back(sg, tmp_path):
    (tmp_path / "secrets.vault.yml").write_text("ENC:lost\n")
    with pytest.raises(SystemExit):
        sg.cmd_vault_pass(FakeStore(vault="ENC:somethingelse"), _args(tmp_path, rebuild=True))
    assert (tmp_path / "secrets.vault.yml").read_text() == "ENC:lost\n"


def test_a_previous_password_still_rekeys_as_before(sg, tmp_path, monkeypatch):
    (tmp_path / "secrets.vault.yml").write_text("ENC:old\n")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        (tmp_path / "secrets.vault.yml").write_text("ENC:current\n")           # what ansible-vault rekey would have done

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    monkeypatch.setattr(sg, "ansible_vault", lambda: "ansible-vault")
    sg.cmd_vault_pass(FakeStore(history=["old"]), _args(tmp_path))
    assert calls and "rekey" in calls[0] and (tmp_path / "secrets.vault.yml").read_text().strip() == "ENC:current"
    assert not list(tmp_path.glob("*.bak-orphaned-*"))
