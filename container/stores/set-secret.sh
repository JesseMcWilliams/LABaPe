#!/usr/bin/env bash
# Put one of the stack's bootstrap secrets in place, value on stdin (design
# §26.3). Once stores.sh has moved them into the platform store, the stack
# reads a memory-only copy in LABAPE_SECRETS_DIR: the file is written there
# and the folder pushed to the store, so the next boot keeps it. Before the
# move, it goes to container/secrets/ as always.
#
#   printf '%s' "$value" | container/stores/set-secret.sh oidc_client_secret
#
# Run as the service user that owns container/. The value never reaches a
# command line or the terminal. Recreate the containers that read it
# afterwards (e.g. docker compose up -d labape-api labape-worker).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C="$(dirname "$HERE")"                       # container/
name="${1:-}"
case "$name" in
  postgres_password|database_url|tofu_pg_conn|secret_key|oidc_client_secret|ca_password) ;;
  *) sed -n '2,13p' "$0" >&2; exit 1 ;;
esac
value="$(cat)"
[ -n "$value" ] || { echo "set-secret: no value on stdin" >&2; exit 1; }

dir="$(grep -m1 '^LABAPE_SECRETS_DIR=' "$C/.env" 2>/dev/null | cut -d= -f2- || true)"
target="${dir:-$C/secrets}"
install -d -m 0700 "$target"
# 0644 inside a 0700 folder, like the other secret files: the containers read
# them as their own users (Testing_Troubleshooting-Log.md, gotcha 5).
(umask 022; printf '%s\n' "$value" > "$target/$name.new")
chmod 0644 "$target/$name.new"
mv "$target/$name.new" "$target/$name"
if [ -n "$dir" ]; then
  python3 "$HERE/hostsecrets.py" push "$dir"
  echo "set-secret: $name written to $dir and pushed to the platform store"
else
  echo "set-secret: $name written to $target"
fi
