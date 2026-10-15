#!/usr/bin/env bash
# Sign-on between linked stacks (design §47.2, F1b), on the host with this Authentik.
#
# Usage: sso.sh REQUEST_JSON       the update agent's request (action authentik_sso): op and fields;
#                                  stores/stores.sh's for OpenBao's sign-in (op client_add, design §49.2)
#        sso.sh --bundle-only      (re)write trust/ca-bundle.pem only (deploy.sh, before starting)
#
# trust/ca-bundle.pem is what Authentik's outbound HTTPS trusts (REQUESTS_CA_BUNDLE in compose.yaml):
# the image's own roots (certifi) plus each linked stack's platform CA root it signs users in with
# (trust/peers/<source>.pem). The Authentik objects are made by sso.py with `ak shell` in the worker.
# Prints one line starting "sso:" on success.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ -z "${COMPOSE:-}" ]; then
  if command -v docker >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="podman compose"; fi
fi

bundle() {
  mkdir -p trust/peers
  if [ ! -s trust/certifi.pem ]; then
    $COMPOSE run --rm --no-deps -T --entrypoint python worker \
      -c 'import certifi, sys; sys.stdout.write(open(certifi.where()).read())' > trust/certifi.pem.tmp
    grep -q "BEGIN CERTIFICATE" trust/certifi.pem.tmp || { echo "sso: couldn't read the image's CA roots" >&2; exit 1; }
    mv trust/certifi.pem.tmp trust/certifi.pem
  fi
  local peers
  shopt -s nullglob; peers=(trust/peers/*.pem); shopt -u nullglob
  cat trust/certifi.pem "${peers[@]}" > trust/ca-bundle.pem.tmp
  chmod 0644 trust/ca-bundle.pem.tmp
  mv trust/ca-bundle.pem.tmp trust/ca-bundle.pem
}

if [ "${1:-}" = --bundle-only ]; then
  bundle
  exit 0
fi

REQ="${1:?usage: sso.sh REQUEST_JSON | --bundle-only}"
f() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" "$REQ" "$1"; }
OP="$(f op)"
case "$OP" in
  source_add)
    mkdir -p trust/peers
    f peer_ca > "trust/peers/$(f slug).pem.tmp"
    grep -q "BEGIN CERTIFICATE" "trust/peers/$(f slug).pem.tmp" || { echo "sso: the linked stack's CA root is missing" >&2; exit 1; }
    mv "trust/peers/$(f slug).pem.tmp" "trust/peers/$(f slug).pem" ;;
  source_remove) rm -f "trust/peers/$(f slug).pem" ;;
  provider_add|provider_remove|client_add) ;;
  *) echo "sso: unknown op $OP" >&2; exit 1 ;;
esac
bundle
$COMPOSE up -d >/dev/null 2>&1        # the bundle's settings, if this Authentik started before them
for _ in $(seq 1 30); do $COMPOSE exec -T worker true 2>/dev/null && break; sleep 2; done
# The client secret and redirect list go by name only (-e VAR), from this process's environment:
# never on a command line.
SSO_CLIENT_SECRET="$(f client_secret)" SSO_REDIRECT_URIS="$(f redirect_uris)" \
$COMPOSE exec -T -e SSO_OP="$OP" -e SSO_CLIENT_ID="$(f client_id)" -e SSO_NAME="$(f name)" \
  -e SSO_REDIRECT_URI="$(f redirect_uri)" -e SSO_SLUG="$(f slug)" -e SSO_ISSUER="$(f issuer)" \
  -e SSO_AUTH_BASE="$(f auth_base)" -e SSO_LAUNCH_URL="$(f launch_url)" -e SSO_CLIENT_SECRET -e SSO_REDIRECT_URIS worker ak shell < sso.py 2>&1 | grep '^sso:' | tail -1 | grep . \
  || { echo "sso: $OP failed in Authentik (see: $COMPOSE logs worker)" >&2; exit 1; }
