#!/usr/bin/env bash
# ship.sh <title> <commit-message-file> <pr-body-file>
# Stage everything on the current topic branch, refuse sensitive-looking files
# and secret-like lines, commit, push, open a PR into main, merge it (deleting
# the branch here and on GitHub), and update local main. Only for changes you've been asked to ship.
set -euo pipefail
[ $# -eq 3 ] || { sed -n '2,5p' "$0" >&2; exit 1; }
title="$1"; msg="$(realpath "$2")"; body="$(realpath "$3")"
cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
branch="$(git branch --show-current)"
[ "$branch" != main ] || { echo "refusing to ship from main; create a YYYY-MM-DD-<topic> branch" >&2; exit 1; }

git add -A
bad="$(git diff --cached --name-only | grep -i -E '(^|/)\.env$|secrets/|\.vault\.yml$|\.local\.md$|egg-info|deploy/state/|deploy/logs/|answers\.[a-z0-9]+\.json$' | grep -v sample || true)"
[ -z "$bad" ] || { echo "refusing: sensitive-looking files staged: $bad" >&2; exit 1; }
# "password" or "secret", then = or :, then 16+ word characters. It also
# matches one variable assigned to another (a *_password variable set from another one);
# check such a hit by eye and commit by hand if it's code, not a secret.
if git diff --cached | grep '^+' | grep -i -E "(password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}" \
     | grep -v -E 'ThisIsMy_FAKE|test-only-secret-key|^\+Secret=[a-z0-9-]+,target=/run/secrets/'; then
  echo "refusing: possible secret in the diff" >&2; exit 1
fi

git commit -q -F "$msg"
git push -q -u origin "$branch" 2>&1 | grep -v -E '^remote:\s*$|pull/new|Create a pull' || true
url="$(gh pr create --base main --head "$branch" --title "$title" --body-file "$body")"
echo "PR: $url"
gh pr merge "$url" --merge --delete-branch >/dev/null
echo "state: $(gh pr view "$url" --json state -q .state)"
git checkout -q main && git pull -q
git branch -d "$branch" 2>/dev/null || true      # gh usually deletes it already
git fetch -q --prune
git log --oneline -1
