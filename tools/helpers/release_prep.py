#!/usr/bin/env python3
"""The file changes of a release (question 24; tools/helpers/release.sh runs it).

Usage: release_prep.py VERSION SUMMARY_FILE [--repo DIR] [--check]
       release_prep.py --notes VERSION [--repo DIR]      (print the release's CHANGELOG section)

VERSION is X.Y.Z or X.Y.Z-rc.N (no leading v). It
1. turns CHANGELOG.md's [Unreleased] entries into `## [VERSION] - <today>`, with the summary
   paragraph first, and leaves an empty [Unreleased] above;
2. sets the version in app/pyproject.toml, web/package.json and web/package-lock.json;
3. in README.md and User_Docs/*.md, replaces "the release after v<previous>" with "v<VERSION>"
   (wording for things that ship in the next release);
4. stamps the newest `Claude_Docs/Planning_Handoff-*.md`: its line `**Latest release:** vX.Y.Z (date)` becomes this
   release (the handoff is part of a release; release.sh also refuses a release whose handoff hasn't changed since
   the previous one). A handoff without that line stops the release.
--check only reports what it would change (exit 1 when [Unreleased] is empty or the version is
already in the CHANGELOG).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-rc\.\d+)?$")
SECTION_RE = re.compile(r"^## \[([^\]]+)\]", re.M)


class ReleaseError(RuntimeError):
    pass


def previous(changelog: str) -> str:
    """The newest released version in the CHANGELOG (after [Unreleased])."""
    for m in SECTION_RE.finditer(changelog):
        if m.group(1) != "Unreleased":
            return m.group(1)
    return ""


def cut_changelog(text: str, version: str, summary: str, today: dt.date) -> str:
    if f"## [{version}]" in text:
        raise ReleaseError(f"CHANGELOG.md already has [{version}]")
    head = "## [Unreleased]\n"
    if head not in text:
        raise ReleaseError("CHANGELOG.md has no [Unreleased] section")
    start = text.index(head) + len(head)
    nxt = SECTION_RE.search(text, start)
    body = text[start:nxt.start() if nxt else len(text)].strip("\n")
    if not body.strip():
        raise ReleaseError("[Unreleased] is empty: nothing to release")
    section = f"## [{version}] - {today.isoformat()}\n\n"
    if summary.strip():
        section += summary.strip() + "\n\n"
    rest = text[nxt.start():] if nxt else ""
    return text[:start] + "\n" + section + body + "\n\n" + rest


def notes(text: str, version: str) -> str:
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", text, re.M)
    if not m:
        raise ReleaseError(f"CHANGELOG.md has no [{version}]")
    nxt = SECTION_RE.search(text, m.end())
    return text[m.end():nxt.start() if nxt else len(text)].strip() + "\n"


def set_versions(repo: Path, version: str) -> list[str]:
    changed = []
    py = repo / "app" / "pyproject.toml"
    if py.is_file():
        t = py.read_text(encoding="utf-8")
        n = re.sub(r'(?m)^version = "[^"]*"', f'version = "{version}"', t, count=1)
        if n != t:
            py.write_text(n, encoding="utf-8", newline="\n")
            changed.append(str(py.relative_to(repo)))
    for name in ("package.json", "package-lock.json"):
        f = repo / "web" / name
        if not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        before = json.dumps(data)
        data["version"] = version
        if name == "package-lock.json" and "" in (data.get("packages") or {}):
            data["packages"][""]["version"] = version
        if json.dumps(data) != before:
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            changed.append(str(f.relative_to(repo)))
    return changed


def docs(repo: Path, prev: str, version: str) -> list[str]:
    changed = []
    if not prev:
        return changed
    files = [repo / "README.md", *sorted((repo / "User_Docs").glob("*.md"))]
    for f in files:
        if not f.is_file():
            continue
        t = f.read_text(encoding="utf-8")
        n = t.replace(f"the release after v{prev}", f"v{version}")
        if n != t:
            f.write_text(n, encoding="utf-8", newline="\n")
            changed.append(str(f.relative_to(repo)))
    return changed


HANDOFF_LINE = re.compile(r"(?m)^\*\*Latest release:\*\*[^\r\n]*")


def newest_handoff(repo: Path) -> Path | None:
    found = sorted((repo / "Claude_Docs").glob("Planning_Handoff-*.md"))
    return found[-1] if found else None


def handoff(repo: Path, version: str, today: dt.date, write: bool = True) -> list[str]:
    """Stamp the newest handoff with this release; ReleaseError when there is none or it lacks the line."""
    f = newest_handoff(repo)
    if f is None:
        raise ReleaseError("Claude_Docs has no Planning_Handoff-*.md: write the handoff before a release")
    t = f.read_bytes().decode("utf-8")                       # bytes: the checkout's line endings stay
    if not HANDOFF_LINE.search(t):
        raise ReleaseError(f"{f.name} has no '**Latest release:** vX.Y.Z (date)' line: add it and update the handoff first")
    n = HANDOFF_LINE.sub(f"**Latest release:** v{version} ({today.isoformat()})", t, count=1)
    if write and n != t:
        f.write_bytes(n.encode("utf-8"))
        return [f.relative_to(repo).as_posix()]
    return []


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("summary_file", nargs="?")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--notes", action="store_true")
    a = ap.parse_args(argv)
    repo, version = Path(a.repo), a.version.lstrip("v")
    if not VERSION_RE.match(version):
        print(f"release: {a.version} isn't X.Y.Z or X.Y.Z-rc.N", file=sys.stderr)
        return 2
    cl = repo / "CHANGELOG.md"
    text = cl.read_text(encoding="utf-8")
    try:
        if a.notes:
            sys.stdout.buffer.write(notes(text, version).encode("utf-8"))      # UTF-8 whatever the console's code page
            return 0
        summary = Path(a.summary_file).read_text(encoding="utf-8") if a.summary_file else ""
        prev = previous(text)
        new = cut_changelog(text, version, summary, dt.date.today())
        handoff(repo, version, dt.date.today(), write=False)           # stops a release whose handoff has no marker
        if a.check:
            print(f"release: v{version} after v{prev or '(none)'}; [Unreleased] has entries")
            return 0
        cl.write_text(new, encoding="utf-8", newline="\n")
        changed = ["CHANGELOG.md", *set_versions(repo, version), *docs(repo, prev, version),
                   *handoff(repo, version, dt.date.today())]
        print("release: changed " + ", ".join(changed))
        return 0
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
