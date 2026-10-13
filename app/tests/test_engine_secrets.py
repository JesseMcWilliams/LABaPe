"""The engine secrets in the catalog (design §26, S2): import, each job's copies, who rotates them."""
import json
import shutil

import pytest

from labape.config import get_settings
from labape.db import session_factory
from labape.engine import engine_secrets as es
from labape.engine.rotation import uses_agent
from labape.security import get_secret


@pytest.fixture()
def secret_files(db_reset):
    src = get_settings().secrets_dir
    shutil.rmtree(src, ignore_errors=True)
    (src / "ssh" / "history").mkdir(parents=True)
    (src / "secrets.vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\nabc\n")
    (src / "vault-pass").write_text("pw-1\n")
    (src / "ssh" / "labape_bootstrap").write_text("PRIVATE-NOW\n")
    (src / "ssh" / "labape_bootstrap.pub").write_text("ssh-ed25519 NOW\n")
    for stamp in ("20261010T000000Z", "20261011T000000Z"):
        (src / "ssh" / "history" / f"labape_bootstrap-{stamp}").write_text(f"PRIVATE-{stamp}\n")
        (src / "ssh" / "history" / f"labape_bootstrap-{stamp}.pub").write_text(f"ssh-ed25519 {stamp}\n")
    yield src
    shutil.rmtree(src, ignore_errors=True)


def test_import_and_copies(secret_files, tmp_root, monkeypatch):
    monkeypatch.setattr(es.shutil, "which", lambda _: None)      # no ansible-vault here: skip the open check
    with session_factory()() as db:
        assert not es.in_catalog(db)
        # The worker rotates them either way; the agent's file-based rotation (R2) is gone.
        assert not uses_agent(db, "vault_password") and not uses_agent(db, "bootstrap_ssh_key")
        assert len(es.import_files(db, "builtin")) == len(es.NAMES)
        assert es.in_catalog(db) and es.import_files(db, "builtin") == []      # only once
        assert get_secret(db, es.VAULT_PASSWORD) == "pw-1"
        hist = json.loads(get_secret(db, es.SSH_HISTORY))
        assert [h["stamp"] for h in hist] == ["20261011T000000Z", "20261010T000000Z"]    # newest first
        assert not uses_agent(db, "vault_password") and not uses_agent(db, "bootstrap_ssh_key")
        assert uses_agent(db, "master_key")                        # the bootstrap secrets stay with the agent (S4)
        work = tmp_root / "es-work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        es.write_vault(db, work)
        es.write_ssh(db, work / "ssh")
    assert (work / ".vault-pass").read_text() == "pw-1\n"
    assert (work / "secrets.vault.yml").read_text().startswith("$ANSIBLE_VAULT")
    assert (work / "ssh" / "labape_bootstrap").read_text() == "PRIVATE-NOW\n"
    assert sorted(p.name for p in (work / "ssh" / "history").iterdir()) == [
        "labape_bootstrap-20261010T000000Z", "labape_bootstrap-20261010T000000Z.pub",
        "labape_bootstrap-20261011T000000Z", "labape_bootstrap-20261011T000000Z.pub"]


def test_import_refuses_missing_files(db_reset):
    shutil.rmtree(get_settings().secrets_dir, ignore_errors=True)
    with session_factory()() as db, pytest.raises(es.EngineSecretsError, match="missing"):
        es.import_files(db, "builtin")


def test_import_moves_to_a_new_platform_store(secret_files, monkeypatch):
    """question 104: a changed platform store moves what's already in the catalog."""
    from labape import stores
    moved = []
    monkeypatch.setattr(stores, "move", lambda db, name, target, *a, **k: moved.append((name, target)) or "moved")
    with session_factory()() as db:
        es.import_files(db, "builtin")
        assert es.import_files(db, "builtin") == []
        out = es.import_files(db, "conjur")
    assert len(out) == len(es.NAMES) and {t for _, t in moved} == {"conjur"}
