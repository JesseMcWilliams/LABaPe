#!/usr/bin/env bash
# stack_names.sh [add NAME | remove NAME]
# The lab stack's names (design §48.4, §58): show them, or add or remove one in container/.env's LABAPE_ALT_NAMES,
# then re-sign the web certificate with the new list (web-tls.sh renew: reloads Caddy and syncs Authentik's
# redirect URIs).
set -euo pipefail
R="${LABAPE_LAB_REPO:-$HOME/LABaPe-web}"; R="${R/#\~/$HOME}"
ENVF="$R/container/.env"
cur="$(sed -n 's/^LABAPE_ALT_NAMES=//p' "$ENVF" | tail -n 1)"
case "${1:-}" in
  add)    new="$(printf '%s\n' "$cur,${2:?name}" | tr ',' '\n' | awk 'NF && !seen[tolower($0)]++' | paste -sd, -)" ;;
  remove) new="$(printf '%s\n' "$cur" | tr ',' '\n' | awk -v n="${2:?name}" 'NF && tolower($0) != tolower(n)' | paste -sd, -)" ;;
  *)      echo "LABAPE_HOSTNAME=$(sed -n 's/^LABAPE_HOSTNAME=//p' "$ENVF")"; echo "LABAPE_ALT_NAMES=$cur"; bash "$R/container/caddy/web-tls.sh" status; exit 0 ;;
esac
sed -i '/^LABAPE_ALT_NAMES=/d' "$ENVF"
echo "LABAPE_ALT_NAMES=$new" >> "$ENVF"
echo "names now: $new"
bash "$R/container/caddy/web-tls.sh" renew
bash "$R/container/caddy/web-tls.sh" status
# The API reads LABAPE_ALT_NAMES when it starts.
if command -v docker >/dev/null 2>&1; then (cd "$R/container" && docker compose up -d labape-api >/dev/null 2>&1 && echo "labape-api recreated"); fi
