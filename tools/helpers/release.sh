#!/usr/bin/env bash
# release.sh VERSION SUMMARY_FILE [--prerelease] [--docx] [--dry-run]
# Cut a LABaPe release (question 24; releases only at milestones, which the user decides):
#   1. checks: on main, clean, up to date; the tag doesn't exist; [Unreleased] has entries;
#   2. on a YYYY-MM-DD-release-X.Y.Z branch, release_prep.py: the CHANGELOG section
#      `## [X.Y.Z] - <today>` (SUMMARY_FILE's paragraph first), the versions in
#      app/pyproject.toml and web/package*.json, "the release after v<previous>" in the docs;
#   3. --docx: Published_Docs/<guide>.docx from each User_Docs/*.md (needs pandoc);
#   4. ship.sh: commit, push, PR, merge;
#   5. a signed tag vX.Y.Z on the merge (git's signing setup; checked with git tag -v), pushed;
#   6. gh release create vX.Y.Z with the CHANGELOG section as its notes (--prerelease for -rc.N).
# VERSION is X.Y.Z or X.Y.Z-rc.N, with or without the v. --dry-run stops after the checks.
# RELEASE_TRAILER (optional) is appended to the commit message, e.g. a Co-Authored-By line.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
USAGE="usage: release.sh VERSION SUMMARY_FILE [--prerelease] [--docx] [--dry-run]"
VERSION="${1:?$USAGE}"; VERSION="${VERSION#v}"
SUMMARY="${2:?$USAGE}"
shift 2
PRE=false DOCX=false DRY=false
for a in "$@"; do
  case "$a" in
    --prerelease) PRE=true ;;
    --docx) DOCX=true ;;
    --dry-run) DRY=true ;;
    *) echo "$USAGE" >&2; exit 2 ;;
  esac
done
case "$VERSION" in *-rc.*) PRE=true ;; esac
TAG="v$VERSION"
PY="$(command -v python3 || command -v python)"
cd "$REPO"
[ -f "$SUMMARY" ] || { echo "release: no summary file $SUMMARY" >&2; exit 2; }
SUMMARY="$(cd "$(dirname "$SUMMARY")" && pwd)/$(basename "$SUMMARY")"

# 1. Checks.
[ "$(git branch --show-current)" = main ] || { echo "release: start on main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "release: the working tree isn't clean" >&2; exit 1; }
git pull --ff-only -q
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || git ls-remote --exit-code --tags origin "$TAG" >/dev/null 2>&1; then
  echo "release: tag $TAG exists already" >&2; exit 1
fi
"$PY" "$HERE/release_prep.py" "$VERSION" "$SUMMARY" --check
if $DOCX && ! command -v pandoc >/dev/null 2>&1; then
  echo "release: --docx needs pandoc" >&2; exit 1
fi
if $DRY; then
  echo "release: dry run: checks passed; would release $TAG$($PRE && echo ' (pre-release)')"
  exit 0
fi

# 2. The files, on a release branch.
BRANCH="$(date +%Y-%m-%d)-release-$VERSION"
git checkout -q -b "$BRANCH"
"$PY" "$HERE/release_prep.py" "$VERSION" "$SUMMARY"

# 3. The published guides.
if $DOCX; then
  mkdir -p Published_Docs
  for f in User_Docs/*.md; do
    pandoc "$f" -o "Published_Docs/$(basename "${f%.md}").docx" --metadata title="LABaPe $TAG: $(basename "${f%.md}" | tr '-' ' ')"
  done
  echo "release: Published_Docs/*.docx made from User_Docs/"
fi

# 4. Commit, push, PR, merge.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
{
  echo "Release $TAG"
  echo
  echo "The [Unreleased] section becomes [$VERSION] - $(date +%Y-%m-%d); versions and"
  echo "the docs' \"the release after\" wording follow (tools/helpers/release.sh)."
  [ -z "${RELEASE_TRAILER:-}" ] || { echo; echo "$RELEASE_TRAILER"; }
} > "$TMP/msg"
{
  echo "## Release $TAG"
  echo
  "$PY" "$HERE/release_prep.py" --notes "$VERSION"
} > "$TMP/body"
bash "$HERE/ship.sh" "Release $TAG" "$TMP/msg" "$TMP/body" | tee "$TMP/ship.log"
if grep -q "failed to push" "$TMP/ship.log" || [ "$(git branch --show-current)" != main ]; then
  echo "release: shipping didn't finish; no tag made" >&2; exit 1
fi
git pull --ff-only -q
grep -q "^## \[$VERSION\]" CHANGELOG.md || { echo "release: main doesn't have the [$VERSION] section; no tag made" >&2; exit 1; }

# 5. The signed tag.
git tag -s "$TAG" -m "LABaPe $TAG"
git tag -v "$TAG" >/dev/null 2>&1 || { echo "release: $TAG doesn't verify (git tag -v); not pushed" >&2; exit 1; }
git push -q origin "$TAG"

# 6. The GitHub release.
"$PY" "$HERE/release_prep.py" --notes "$VERSION" > "$TMP/notes"
gh release create "$TAG" --title "$TAG" --notes-file "$TMP/notes" $($PRE && echo --prerelease)
echo "release: $TAG tagged (signed), pushed and published"
