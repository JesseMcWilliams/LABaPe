#!/usr/bin/env bash
# Claude_Docs/Reference_Validate-Setup.md §3 — `tofu init -backend=false` + `tofu
# validate` against every backend directory that actually has
# configuration in it yet (tofu/backends/hyperv is still empty as of
# M1, Claude_Docs/Design_System-Overview.md §18 — skipped cleanly, not failed).
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0
checked=0

for backend_dir in "$ROOT_DIR"/tofu/backends/*/; do
  backend="$(basename "$backend_dir")"

  if ! ls "$backend_dir"/*.tf >/dev/null 2>&1; then
    echo "labape: $backend — no .tf files yet, skipping." >&2
    continue
  fi

  checked=$((checked + 1))
  echo "== $backend ==" >&2
  (
    cd "$backend_dir"
    tofu init -backend=false -input=false >/tmp/labape-tofu-init.$$ 2>&1
    init_status=$?
    if [ $init_status -ne 0 ]; then
      echo "FAIL: tofu init failed for $backend" >&2
      cat /tmp/labape-tofu-init.$$ >&2
      rm -f /tmp/labape-tofu-init.$$
      exit 1
    fi
    rm -f /tmp/labape-tofu-init.$$

    if tofu validate; then
      echo "OK: $backend validates." >&2
      exit 0
    else
      echo "FAIL: $backend — see tofu validate's own output above." >&2
      exit 1
    fi
  ) || fail=1
done

if [ "$checked" -eq 0 ]; then
  echo "labape: no backend has any .tf files yet — nothing to validate." >&2
fi

exit $fail
