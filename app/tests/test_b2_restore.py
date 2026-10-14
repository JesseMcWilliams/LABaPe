"""Batch B2 (design §33): installing from a full-stack backup."""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from labape import seal

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
if "pwd" not in sys.modules:                 # the installer runs on Linux; these tests also run on Windows
    try:
        import pwd  # noqa: F401
    except ImportError:
        import types
        sys.modules["pwd"] = types.ModuleType("pwd")

from labape_deploy import restore  # noqa: E402
from labape_deploy import steps as S  # noqa: E402
from labape_deploy.util import StepError  # noqa: E402


def _archive(tmp_path, answers: dict | None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        def add(name, data: bytes):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        add("./manifest.json", json.dumps({"name": "labape-stack-pi-20261014-030000", "host": "Director",
                                           "version": "v0.2.0-140"}).encode())
        add("./db/labape.dump", b"PGDMP")
        if answers is not None:
            add("./files/installer/state/answers.labape.json",
                json.dumps({"_comment": "Written by deploy/install-labape.py.", **answers}).encode())
    key = seal.new_key()
    (tmp_path / "k").write_text(key + "\n")
    out = io.BytesIO()
    seal.seal(io.BytesIO(buf.getvalue()), out, seal.parse_key(key))
    (tmp_path / "a.tar.sealed").write_bytes(out.getvalue())
    return tmp_path / "a.tar.sealed", tmp_path / "k"


def test_the_answers_come_out_of_the_archive(tmp_path):
    arch, key = _archive(tmp_path, {"ServiceUser": "labape", "WebHostname": "director.jlm.abq", "Profile": "web"})
    answers, manifest = restore.archive_answers(REPO, arch, key)
    assert answers == {"ServiceUser": "labape", "WebHostname": "director.jlm.abq", "Profile": "web"}
    assert manifest["host"] == "Director"
    arch, key = _archive(tmp_path, None)                      # a stack not installed by the installer
    assert restore.archive_answers(REPO, arch, key)[0] == {}
    (tmp_path / "wrong").write_text(seal.new_key())
    with pytest.raises(StepError, match="didn't open"):
        restore.archive_answers(REPO, arch, tmp_path / "wrong")
    (tmp_path / "junk").write_text("not a key")
    with pytest.raises(StepError, match="backup key"):
        restore.archive_answers(REPO, arch, tmp_path / "junk")


def test_the_restore_step_runs_after_the_stack_and_before_bootstrap():
    spec = __import__("importlib.util").util.spec_from_file_location("install_labape", DEPLOY / "install-labape.py")
    mod = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = mod.STEP_NAMES
    assert names.index("Stack.UpdateAgent") < names.index("Stack.Restore") < names.index("Stack.Bootstrap")
    args = mod.parse_args(["--from-backup", "a", "--backup-key-file", "k", "--restore-drill"])
    assert args.from_backup == "a" and args.restore_drill


def test_the_restore_step_command(tmp_path):
    ran = []

    class R:
        def run(self, cmd, **kw):
            ran.append(cmd)

    class A:
        def need(self, name):
            return {"ServiceUser": "labape"}[name]

    c = S.Ctx(r=R(), a=A(), repo=Path("/opt/LABaPe"), deploy_root=Path("/opt/LABaPe/deploy"))
    assert S.stack_restore(c)[0] == S.NA
    c.backup, c.backup_key, c.restore_drill = Path("/b/x.tar.sealed"), Path("/b/k"), True
    status, msg = S.stack_restore(c)
    assert status == S.DONE and "drill" in msg
    cmd = ran[0]
    assert cmd[1].replace("\\", "/").endswith("deploy/stack-restore.sh") and "--maintenance" in cmd and "--yes" in cmd
    assert cmd[cmd.index("--user") + 1] == "labape"
