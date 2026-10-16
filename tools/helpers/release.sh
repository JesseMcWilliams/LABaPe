#!/usr/bin/env bash
# release.sh VERSION SUMMARY_FILE [--prerelease] [--docx] [--skip-sbom] [--sbom-anyway] [--dry-run] [--resume]
# Cut a LABaPe release (question 24; releases only at milestones, which the user decides):
#   1. checks: on main, clean, up to date; the tag doesn't exist; [Unreleased] has entries;
#   2. on a YYYY-MM-DD-release-X.Y.Z branch, release_prep.py: the CHANGELOG section
#      `## [X.Y.Z] - <today>` (SUMMARY_FILE's paragraph first), the versions in
#      app/pyproject.toml and web/package*.json, "the release after v<previous>" in the docs;
#      The software bill of materials too: sbom_snapshot.py takes the inventory of the lab stack's
#      worker image (Python and Debian packages, tools, collections, providers, plugins, image digests)
#      and sbom.py writes Claude_Docs/Reference_SBOM.md and sbom/labape.cdx.json for the new version.
#      That needs LABAPE_LAB_SSH (and LABAPE_LAB_KEY) and a worker image built from this clean main:
#      run tools/helpers/deploy-web.sh first. --skip-sbom regenerates from the snapshot already in the
#      repository (npm and the pins refresh; Python and images don't) and says so; --sbom-anyway accepts
#      a worker image that isn't this main;
#   3. --docx: Published_Docs/<guide>.docx from each User_Docs/*.md (needs pandoc);
#   4. ship.sh: commit, push, PR, merge;
#   5. a signed tag vX.Y.Z on the merge (git's signing setup; checked with git tag -v), pushed;
#   6. gh release create vX.Y.Z with the CHANGELOG section as its notes (--prerelease for -rc.N) and the
#      CycloneDX file attached as labape-X.Y.Z.cdx.json.
# --resume: the release PR merged but the run stopped before the tag (a long run can be cut off by a
# terminal's time limit: start it in the background, e.g. run_in_background): from clean main with the
# [X.Y.Z] section, make the signed tag and the GitHub release. SUMMARY_FILE is still required but unused.
# VERSION is X.Y.Z or X.Y.Z-rc.N, with or without the v. --dry-run stops after the checks (the lab and
# the worker image are checked too).
# RELEASE_TRAILER (optional) is appended to the commit message, e.g. a Co-Authored-By line.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
USAGE="usage: release.sh VERSION SUMMARY_FILE [--prerelease] [--docx] [--skip-sbom] [--sbom-anyway] [--dry-run] [--resume]"
VERSION="${1:?$USAGE}"; VERSION="${VERSION#v}"
SUMMARY="${2:?$USAGE}"
shift 2
PRE=false DOCX=false DRY=false SKIP_SBOM=false SBOM_ANYWAY=false RESUME=false
for a in "$@"; do
  case "$a" in
    --prerelease) PRE=true ;;
    --docx) DOCX=true ;;
    --skip-sbom) SKIP_SBOM=true ;;
    --sbom-anyway) SBOM_ANYWAY=true ;;
    --dry-run) DRY=true ;;
    --resume) RESUME=true ;;
    *) echo "$USAGE" >&2; exit 2 ;;
  esac
done
case "$VERSION" in *-rc.*) PRE=true ;; esac
TAG="v$VERSION"
PY="$(command -v python3 || command -v python)"
cd "$REPO"
[ -f "$SUMMARY" ] || { echo "release: no summary file $SUMMARY" >&2; exit 2; }
SUMMARY="$(cd "$(dirname "$SUMMARY")" && pwd)/$(basename "$SUMMARY")"

# Steps 5 and 6, also what --resume runs: the signed tag, then the GitHub release.
publish() {
  TMP="${TMP:-$(mktemp -d)}"
  # 5. The signed tag.
  git tag -s "$TAG" -m "LABaPe $TAG"
  git tag -v "$TAG" >/dev/null 2>&1 || { echo "release: $TAG doesn't verify (git tag -v); not pushed" >&2; exit 1; }
  git push -q origin "$TAG"

  # 6. The GitHub release.
  "$PY" "$HERE/release_prep.py" --notes "$VERSION" > "$TMP/notes"
  cp "$REPO/sbom/labape.cdx.json" "$TMP/labape-$VERSION.cdx.json"
  {
    echo
    echo "The software bill of materials is attached (\`labape-$VERSION.cdx.json\`, CycloneDX 1.5) and in the repository (\`Claude_Docs/Reference_SBOM.md\`)."
  } >> "$TMP/notes"
  gh release create "$TAG" --title "$TAG" --notes-file "$TMP/notes" "$TMP/labape-$VERSION.cdx.json" $($PRE && echo --prerelease)
  echo "release: $TAG tagged (signed), pushed and published"
}

# 1. Checks.
[ "$(git branch --show-current)" = main ] || { echo "release: start on main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "release: the working tree isn't clean" >&2; exit 1; }
git pull --ff-only -q
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || git ls-remote --exit-code --tags origin "$TAG" >/dev/null 2>&1; then
  echo "release: tag $TAG exists already" >&2; exit 1
fi
if $RESUME; then      # the release PR merged but the run stopped before the tag: finish steps 5 and 6
  grep -q "^## \[$VERSION\]" CHANGELOG.md || { echo "release: main has no [$VERSION] section; nothing to resume" >&2; exit 1; }
  publish
  exit 0
fi
"$PY" "$HERE/release_prep.py" "$VERSION" "$SUMMARY" --check
if $DOCX && ! command -v pandoc >/dev/null 2>&1; then
  echo "release: --docx needs pandoc" >&2; exit 1
fi
# The SBOM comes from the worker image on the lab stack, which must be this main.
if ! $SKIP_SBOM; then
  [ -n "${LABAPE_LAB_SSH:-}" ] || { echo "release: the SBOM is taken from the lab stack's worker image: set LABAPE_LAB_SSH (and LABAPE_LAB_KEY), or pass --skip-sbom" >&2; exit 1; }
  WANT="$(git describe --tags --always)"
  HAVE="$("$PY" "$HERE/sbom_snapshot.py" --print-version)"
  if [ "$HAVE" != "$WANT" ]; then
    if $SBOM_ANYWAY; then
      echo "release: warning: the lab's worker image is $HAVE, main is $WANT; the SBOM will say so" >&2
    else
      echo "release: the lab's worker image is $HAVE, but main is $WANT; run tools/helpers/deploy-web.sh from this clean main first (or pass --sbom-anyway)" >&2
      exit 1
    fi
  fi
else
  echo "release: --skip-sbom: Python packages, tools and image digests in the SBOM stay as in sbom/image-snapshot.json" >&2
fi
if $DRY; then
  echo "release: dry run: checks passed; would release $TAG$($PRE && echo ' (pre-release)')"
  exit 0
fi

# 2. The files, on a release branch.
BRANCH="$(date +%Y-%m-%d)-release-$VERSION"
git checkout -q -b "$BRANCH"
"$PY" "$HERE/release_prep.py" "$VERSION" "$SUMMARY"

# 2b. The software bill of materials, for the new version.
$SKIP_SBOM || "$PY" "$HERE/sbom_snapshot.py"
"$PY" "$HERE/sbom.py"

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
  echo "The [Unreleased] section becomes [$VERSION] - $(date +%Y-%m-%d); versions, the docs'"
  echo "\"the release after\" wording and the software bill of materials follow (tools/helpers/release.sh)."
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

publish
