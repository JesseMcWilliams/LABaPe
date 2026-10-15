#!/usr/bin/env bash
# lock-python.sh [--upgrade]
# Refresh container/requirements.txt, the hash-pinned list of every Python package the worker image
# installs (LABaPe's dependencies from app/pyproject.toml plus container/requirements.in), for Debian
# trixie's Python 3.13 on amd64 and arm64 at once (uv's universal resolution). Without --upgrade the
# versions already pinned stay; --upgrade moves everything to the newest allowed.
# Afterwards: rebuild and try the image (tools/helpers/deploy-web.sh), run the tests, refresh the
# SBOM (tools/helpers/sbom_snapshot.py, sbom.py), commit.
# Needs uv (pip install uv).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
if command -v uv >/dev/null 2>&1; then UV=(uv); else UV=("$(command -v python3 || command -v python)" -m uv); fi
"${UV[@]}" --version >/dev/null 2>&1 || { echo "lock-python: needs uv (pip install uv)" >&2; exit 1; }
extra=()
[ "${1:-}" = --upgrade ] && extra+=(--upgrade)
[ -f container/requirements.txt ] || : > container/requirements.txt
"${UV[@]}" pip compile app/pyproject.toml container/requirements.in \
  --universal --python-version 3.13 --generate-hashes --no-header \
  --output-file container/requirements.txt --quiet "${extra[@]}"
{
  echo "# Locked Python packages of the worker image (LABaPe's dependencies and container/requirements.in), with hashes."
  echo "# Made by tools/helpers/lock-python.sh for Python 3.13 on Linux, amd64 and arm64. Do not edit by hand."
  cat container/requirements.txt
} > container/requirements.txt.new
mv container/requirements.txt.new container/requirements.txt
echo "lock-python: $(grep -c '^[A-Za-z0-9_.-]*==' container/requirements.txt) packages pinned in container/requirements.txt"
