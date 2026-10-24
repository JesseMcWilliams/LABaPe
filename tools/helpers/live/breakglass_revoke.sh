#!/usr/bin/env bash
# Question 197 on the real stack: enable break-glass, sign in with it, revoke, and see the open session stop.
# Runs on the lab host from the stack's clone (lab-live.sh). Prints statuses only, never the credential.
set -u
cd "${LABAPE_REPO:-$HOME/LABaPe-web}/container"
api() { docker compose exec -T labape-api "$@"; }
out="$(api labape breakglass enable 2>/dev/null)"
token="$(printf '%s\n' "$out" | grep -oE '[A-Za-z0-9_-]{30,}' | tail -n 1)"
[ -n "$token" ] || { echo "no credential in the output"; exit 1; }
jar="$(mktemp)"; trap 'rm -f "$jar"' EXIT
base="https://localhost"
echo "redeem:        $(curl -sk -c "$jar" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "{\"token\":\"$token\"}" "$base/api/auth/breakglass")"
echo "me (open):     $(curl -sk -b "$jar" -o /dev/null -w '%{http_code}' "$base/api/auth/me")"
echo "revoke:        $(api labape breakglass revoke 2>/dev/null | tail -n 1)"
echo "me (revoked):  $(curl -sk -b "$jar" -o /dev/null -w '%{http_code}' "$base/api/auth/me")  (401 expected)"
echo "reuse:         $(curl -sk -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "{\"token\":\"$token\"}" "$base/api/auth/breakglass")  (401 expected)"
