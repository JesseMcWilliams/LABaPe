#!/usr/bin/env bash
# Deploys a throwaway Authentik for testing LABaPe sign-in, with the
# LABaPe OIDC application, role groups and test users from
# blueprints/labape-test.yaml. Secrets are generated into
# tools/authentik-test/.env (gitignored) and never printed.
#
# Usage: deploy.sh --labape-hostname <name> [--public-host <host-or-ip>] [--configure-labape]
#        deploy.sh --down          (stop and delete everything, including data)
#
#   --labape-hostname   the hostname browsers use for LABaPe (the OIDC redirect URI)
#   --public-host       how browsers and the LABaPe containers reach this
#                       Authentik (default: this host's first IP address)
#   --configure-labape  also point container/.env and container/secrets/
#                       oidc_client_secret at this Authentik
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

LABAPE_HOSTNAME_ARG=""
PUBLIC_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
CONFIGURE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --labape-hostname) LABAPE_HOSTNAME_ARG="${2:?}"; shift ;;
    --public-host) PUBLIC_HOST="${2:?}"; shift ;;
    --configure-labape) CONFIGURE=true ;;
    --down) docker compose down -v; rm -f .env; echo "authentik-test: removed."; exit 0 ;;
    *) sed -n '2,16p' "$0" >&2; exit 1 ;;
  esac
  shift
done
[ -n "$LABAPE_HOSTNAME_ARG" ] || { sed -n '2,16p' "$0" >&2; exit 1; }

gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
if [ ! -f .env ]; then
  umask 077
  cat > .env <<EOF
PG_PASS=$(gen)
AUTHENTIK_SECRET_KEY=$(gen)$(gen)
AUTHENTIK_BOOTSTRAP_PASSWORD=$(gen)
AUTHENTIK_BOOTSTRAP_EMAIL=akadmin@example.invalid
AUTHENTIK_ERROR_REPORTING__ENABLED=false
LABAPE_TEST_CLIENT_SECRET=$(gen)
LABAPE_TEST_USER_PASSWORD=$(gen)
LABAPE_HOSTNAME=$LABAPE_HOSTNAME_ARG
EOF
fi

docker compose up -d
echo "authentik-test: waiting for Authentik to apply the blueprint..."
issuer="http://${PUBLIC_HOST}:9000/application/o/labape/"
for _ in $(seq 1 90); do
  if curl -fsS "${issuer}.well-known/openid-configuration" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS "${issuer}.well-known/openid-configuration" >/dev/null || {
  echo "authentik-test: the LABaPe application didn't appear at $issuer; check: docker compose logs worker" >&2
  exit 1
}

if $CONFIGURE; then
  env_file=../../container/.env
  [ -f "$env_file" ] || { echo "authentik-test: run container/setup.sh first" >&2; exit 1; }
  sed -i "s|^LABAPE_OIDC_ISSUER=.*|LABAPE_OIDC_ISSUER=${issuer}|; s|^LABAPE_OIDC_CLIENT_ID=.*|LABAPE_OIDC_CLIENT_ID=labape|" "$env_file"
  (umask 077; grep '^LABAPE_TEST_CLIENT_SECRET=' .env | cut -d= -f2- > ../../container/secrets/oidc_client_secret)
  echo "authentik-test: container/.env and container/secrets/oidc_client_secret now point at $issuer"
  echo "               (recreate the LABaPe containers: docker compose up -d)"
fi

cat <<EOF
authentik-test: ready.
  Issuer:      $issuer
  Admin UI:    http://${PUBLIC_HOST}:9000/  (user akadmin; password: AUTHENTIK_BOOTSTRAP_PASSWORD in tools/authentik-test/.env)
  Test users:  labape-admin, labape-dev, labape-viewer (password: LABAPE_TEST_USER_PASSWORD in .env)
EOF
