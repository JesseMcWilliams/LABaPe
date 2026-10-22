"""R1 (question 24): the release script's file changes (tools/helpers/release_prep.py)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "tools" / "helpers" / "release_prep.py"
spec = importlib.util.spec_from_file_location("release_prep", HERE)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

CL = """# Changelog

## [Unreleased]

### Added
- Spreading.

## [0.3.0] - 2026-10-14

Old summary.

### Added
- Old.
"""


def test_the_changelog_section():
    out = R.cut_changelog(CL, "0.4.0", "The summary.\n", dt.date(2026, 10, 20))
    assert "## [Unreleased]\n\n## [0.4.0] - 2026-10-20\n\nThe summary.\n\n### Added\n- Spreading.\n\n## [0.3.0]" in out
    assert R.previous(CL) == "0.3.0" and R.previous(out) == "0.4.0"
    assert R.notes(out, "0.4.0") == "The summary.\n\n### Added\n- Spreading.\n"
    with pytest.raises(R.ReleaseError, match="already"):
        R.cut_changelog(out, "0.4.0", "", dt.date(2026, 10, 20))
    with pytest.raises(R.ReleaseError, match="empty"):
        R.cut_changelog(out, "0.5.0", "", dt.date(2026, 10, 20))


def test_versions_and_docs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pyproject.toml").write_text('[project]\nname = "labape"\nversion = "0.2.0"\n')
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"name": "labape-web", "version": "0.2.0"}))
    (tmp_path / "web" / "package-lock.json").write_text(json.dumps(
        {"name": "labape-web", "version": "0.2.0", "packages": {"": {"version": "0.2.0"}, "node_modules/x": {"version": "1.0.0"}}}))
    (tmp_path / "User_Docs").mkdir()
    (tmp_path / "User_Docs" / "Guide.md").write_text("Sign-in over HTTPS (from the release after v0.3.0).\n")
    (tmp_path / "CHANGELOG.md").write_text(CL)
    (tmp_path / "Claude_Docs").mkdir()
    (tmp_path / "Claude_Docs" / "Planning_Handoff-2026-10-20.md").write_text("# h\n\n**Latest release:** v0.3.0 (2026-10-14)\n")
    summary = tmp_path / "s.txt"
    summary.write_text("Summary.")
    assert R.main(["v0.4.0", str(summary), "--repo", str(tmp_path)]) == 0
    assert 'version = "0.4.0"' in (tmp_path / "app" / "pyproject.toml").read_text()
    lock = json.loads((tmp_path / "web" / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == "0.4.0" and lock["packages"]["node_modules/x"]["version"] == "1.0.0"
    assert json.loads((tmp_path / "web" / "package.json").read_text())["version"] == "0.4.0"
    assert (tmp_path / "User_Docs" / "Guide.md").read_text() == "Sign-in over HTTPS (from v0.4.0).\n"
    assert "## [0.4.0] - " in (tmp_path / "CHANGELOG.md").read_text()
    assert "**Latest release:** v0.4.0 (" in (tmp_path / "Claude_Docs" / "Planning_Handoff-2026-10-20.md").read_text()
    assert R.main(["0.4", str(summary), "--repo", str(tmp_path)]) == 2                    # not X.Y.Z
    assert R.main(["0.5.0", str(summary), "--repo", str(tmp_path), "--check"]) == 1       # nothing unreleased


def test_the_handoff_is_stamped_and_required(tmp_path):
    docs = tmp_path / "Claude_Docs"
    docs.mkdir()
    with pytest.raises(R.ReleaseError, match="no Planning_Handoff"):
        R.handoff(tmp_path, "0.5.8", dt.date(2026, 10, 23))
    old = docs / "Planning_Handoff-2026-10-21.md"
    old.write_text("# old\n\n**Latest release:** v0.5.5 (2026-10-20)\n", encoding="utf-8")
    new = docs / "Planning_Handoff-2026-10-22.md"
    new.write_text("# new\n\nNo marker yet.\n", encoding="utf-8")
    with pytest.raises(R.ReleaseError, match="Latest release"):                     # the newest one is the one checked
        R.handoff(tmp_path, "0.5.8", dt.date(2026, 10, 23))
    new.write_bytes(b"# new\r\n\r\n**Latest release:** v0.5.7 (2026-10-22)\r\n\r\nMore.\r\n")
    assert R.handoff(tmp_path, "0.5.8", dt.date(2026, 10, 23), write=False) == []        # a check changes nothing
    assert R.handoff(tmp_path, "0.5.8", dt.date(2026, 10, 23)) == ["Claude_Docs/Planning_Handoff-2026-10-22.md"]
    text = new.read_bytes().decode()
    assert "**Latest release:** v0.5.8 (2026-10-23)\r\n" in text and "More." in text      # line endings kept
    assert "v0.5.5" in old.read_text(encoding="utf-8")                                   # older ones are left alone
