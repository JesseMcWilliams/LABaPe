"""Install from a full-stack backup (design §33, batch B2): the answers inside the archive.

`install-labape.py --from-backup ARCHIVE --backup-key-file KEY` installs this host with the
answers the backed-up stack was installed with (its `files/installer/state/answers.*.json`), then
the Stack.Restore step puts the archive's state onto the new stack (deploy/stack-restore.sh).

The archive is opened here with the LABaPe image's own code (app/labape/seal.py, loaded by path
so nothing else of the app is needed) before any image exists; it needs only python3-cryptography,
installed from the distribution when it's missing.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .util import StepError


def _seal_module(repo: Path):
    try:
        import cryptography  # noqa: F401
    except ImportError:
        subprocess.run(["apt-get", "install", "-y", "python3-cryptography"], check=True,
                       env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    spec = importlib.util.spec_from_file_location("labape_seal", repo / "app" / "labape" / "seal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def archive_answers(repo: Path, archive: Path, key_file: Path) -> tuple[dict, dict]:
    """(the installer answers saved in the archive, its manifest). Answers are {} when the backed-up
    stack wasn't installed with the installer (give them with --config-file or --set then)."""
    seal = _seal_module(repo)
    try:
        key = seal.parse_key(key_file.read_text(encoding="utf-8"))
    except (OSError, seal.SealError) as exc:
        raise StepError(f"the backup key file {key_file}: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="labape-restore-") as tmp:
        plain = Path(tmp) / "stack.tar"
        try:
            with open(archive, "rb") as src, open(plain, "wb") as dst:
                os.chmod(plain, 0o600)
                seal.unseal(src, dst, key)
        except (OSError, seal.SealError) as exc:
            raise StepError(f"{archive} didn't open: {exc}") from exc
        answers, manifest = {}, {}
        with tarfile.open(plain) as t:
            for m in t.getmembers():
                name = m.name.removeprefix("./")
                if name == "manifest.json":
                    manifest = json.load(t.extractfile(m))
                elif name.startswith("files/installer/state/answers.") and name.endswith(".json") and m.isfile():
                    answers = json.load(t.extractfile(m))
    answers.pop("_comment", None)
    return answers, manifest
