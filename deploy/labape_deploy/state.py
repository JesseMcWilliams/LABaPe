"""Saved answers and step progress, so a failed or partial install can be
resumed or have single steps re-run (the BlueTrack installer's model).

Both files live in deploy/state/ (git-ignored), one pair per instance:
  answers.<instance>.json   same shape as a --config-file answer file, so
                            it can be passed back in as one. Never holds a
                            password or secret.
  progress.<instance>.json  each step's last status.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from .answers import ANSWERS

STATUSES = ("Completed", "Failed", "Declined", "NotApplicable")


def state_dir(deploy_root: Path) -> Path:
    return deploy_root / "state"


def state_path(deploy_root: Path, instance: str, kind: str) -> Path:
    return state_dir(deploy_root) / f"{kind}.{instance}.json"


def read_answer_file(path: Path) -> dict:
    """Known answers from a --config-file or saved answers file. Keys
    starting with '_' are comments; unknown keys are warned about; empty
    values count as not answered."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key not in ANSWERS:
            print(f"warning: answer file {path}: unknown setting {key!r} ignored")
            continue
        if value is None or value == "" or value == []:
            continue
        out[key] = value
    return out


def save_answer_file(path: Path, answers: dict, dry_run: bool) -> None:
    ordered = {"_comment": "Written by deploy/install-labape.py. Reusable as --config-file; --resume and "
                           "--step read it automatically. Holds no passwords or secrets."}
    for name in ANSWERS:
        if name in answers and answers[name] not in (None, "", []):
            ordered[name] = answers[name]
    if dry_run:
        print(f"[dry-run] would save answers to {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_progress(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("steps", {})


def save_step(path: Path, progress: dict, step: str, status: str, message: str, dry_run: bool) -> None:
    assert status in STATUSES
    progress[step] = {"status": status, "time": dt.datetime.now().isoformat(timespec="seconds"),
                      "message": message}
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"steps": dict(sorted(progress.items()))}, indent=2) + "\n", encoding="utf-8")
