#!/usr/bin/env bash
# sync_redirects.sh: run the lab stack's tools/authentik-test/sync-redirects.sh for its current names and show the result.
set -euo pipefail
R="${LABAPE_LAB_REPO:-$HOME/LABaPe-web}"; R="${R/#\~/$HOME}"
main="$(sed -n 's/^LABAPE_HOSTNAME=//p' "$R/container/.env" | tail -n 1)"
alt="$(sed -n 's/^LABAPE_ALT_NAMES=//p' "$R/container/.env" | tail -n 1)"
bash -x "$R/tools/authentik-test/sync-redirects.sh" "$main" "$alt" 2>&1 | tail -15
