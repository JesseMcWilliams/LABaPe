#!/usr/bin/env bash
# Claude_Docs/Reference_Validate-Setup.md §2 — ansible-playbook --syntax-check against
# the real playbook. Requires the collections from §1 to be installed;
# module names are resolved even at syntax-check time, not just
# YAML-parsed.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="$ROOT_DIR/ansible/software-manifest.yml"
created_manifest=0

if [ ! -f "$MANIFEST" ]; then
  echo "labape: $MANIFEST doesn't exist yet — temporarily copying the example so the syntax-check has something to read." >&2
  cp "$ROOT_DIR/ansible/software-manifest.example.yml" "$MANIFEST"
  created_manifest=1
fi

cleanup() {
  if [ "$created_manifest" -eq 1 ]; then
    rm -f "$MANIFEST"
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR/ansible"
if ansible-playbook --syntax-check playbooks/site.yml; then
  echo "OK: playbooks/site.yml and its roles parse cleanly." >&2
  exit 0
else
  echo "FAIL: see ansible-playbook's own error output above." >&2
  exit 1
fi
