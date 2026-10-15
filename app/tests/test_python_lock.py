"""The Python lock (container/requirements.txt, made by tools/helpers/lock-python.sh) covers the project."""
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock() -> dict[str, str]:
    text = (ROOT / "container" / "requirements.txt").read_text(encoding="utf-8")
    return {_norm(m.group(1)): m.group(2) for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", text, re.M)}


def test_every_pin_has_hashes():
    text = (ROOT / "container" / "requirements.txt").read_text(encoding="utf-8")
    entries = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    pins = [e for e in entries if re.match(r"[A-Za-z0-9_.\-]+==", e)]
    assert len(pins) > 40
    assert all("--hash=sha256:" in e for e in pins), [e.split("==")[0] for e in pins if "--hash=sha256:" not in e]


def test_the_projects_dependencies_are_locked_within_their_ranges():
    deps = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    lock = _lock()
    for d in deps:
        r = Requirement(d)
        n = _norm(r.name)
        assert n in lock, f"{r.name} isn't in container/requirements.txt: run tools/helpers/lock-python.sh"
        assert r.specifier.contains(Version(lock[n]), prereleases=True), f"{r.name}: locked {lock[n]} breaks {r.specifier}"
    extra = {_norm(Requirement(l).name): Requirement(l) for l in
             (ROOT / "container" / "requirements.in").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")}
    assert {"ansible-core", "pywinrm"} <= set(extra) and all(n in lock for n in extra)
    assert lock["ansible-core"] in str(extra["ansible-core"].specifier)         # the one pinned in requirements.in


def test_the_image_installs_from_the_lock_and_checks_hashes():
    dockerfile = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-cache-dir --require-hashes -r /opt/labape/requirements.txt" in dockerfile
    assert "pip install --no-cache-dir --no-deps /opt/labape/src/app" in dockerfile     # LABaPe itself resolves nothing
    assert "COPY container/requirements.txt /opt/labape/requirements.txt" in dockerfile
