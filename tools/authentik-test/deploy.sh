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
# Docker Compose by default; Podman when Docker isn't installed, or set COMPOSE.
if [ -z "${COMPOSE:-}" ]; then
  if command -v docker >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="podman compose"; fi
fi

LABAPE_HOSTNAME_ARG=""
PUBLIC_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
CONFIGURE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --labape-hostname) LABAPE_HOSTNAME_ARG="${2:?}"; shift ;;
    --public-host) PUBLIC_HOST="${2:?}"; shift ;;
    --configure-labape) CONFIGURE=true ;;
    --down) $COMPOSE down -v; rm -f .env; echo "authentik-test: removed."; exit 0 ;;
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
# LABaPe's host name can change (e.g. from an address to its DNS name): the blueprint's redirect
# URI follows it; `up -d` below recreates the containers when it changed.
if ! grep -qx "LABAPE_HOSTNAME=$LABAPE_HOSTNAME_ARG" .env; then
  sed -i '/^LABAPE_HOSTNAME=/d' .env
  echo "LABAPE_HOSTNAME=$LABAPE_HOSTNAME_ARG" >> .env
  echo "authentik-test: LABaPe's host name is now $LABAPE_HOSTNAME_ARG"
fi
# Added later: keep an older .env working.
grep -q '^LABAPE_TEST_DIRECTORY_TOKEN=' .env || echo "LABAPE_TEST_DIRECTORY_TOKEN=$(gen)" >> .env
grep -q '^LABAPE_TEST_ROTATOR_TOKEN=' .env || echo "LABAPE_TEST_ROTATOR_TOKEN=$(gen)" >> .env

bash ./sso.sh --bundle-only                     # the CA bundle the containers read (design §47.2)
$COMPOSE up -d
echo "authentik-test: waiting for Authentik to apply the blueprint..."
issuer="http://${PUBLIC_HOST}:9000/application/o/labape/"
for _ in $(seq 1 90); do
  if curl -fsS "${issuer}.well-known/openid-configuration" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS "${issuer}.well-known/openid-configuration" >/dev/null || {
  echo "authentik-test: the LABaPe application didn't appear at $issuer; check: $COMPOSE logs worker" >&2
  exit 1
}

# The blueprint makes the provider once (state: created, so a rotated client secret sticks), so a
# changed LABaPe host name never reaches its redirect URI that way: set that one field here.
$COMPOSE exec -T worker ak shell -c "
import dataclasses, os
from authentik.providers.oauth2.models import OAuth2Provider
p = OAuth2Provider.objects.get(client_id='labape')
url = 'https://%s/api/auth/oidc/callback' % os.environ['LABAPE_HOSTNAME']
if [u.url for u in p.redirect_uris] != [url]:
    p.redirect_uris = [dataclasses.replace(u, url=url) for u in p.redirect_uris[:1]]
    p.save()
    print('authentik-test: the redirect URI is now', url)
" 2>&1 | grep '^authentik-test:' || true

# The rotation account may set the directory token's key and the LABaPe
# provider's client secret, on those two objects only (object permissions; the
# blueprint importer can't assign them to a role).
$COMPOSE exec -T worker ak shell -c "
from authentik.core.models import Token
from authentik.providers.oauth2.models import OAuth2Provider
from authentik.rbac.models import Role
role = Role.objects.get(name='labape-token-rotator')
token = Token.objects.get(identifier='labape-directory')
# Authentik checks POST .../set_key/ as add_token on the object (its docs name
# set_token_key, which this version doesn't check); grant both, on this token only.
role.assign_perms(['authentik_core.view_token', 'authentik_core.add_token', 'authentik_core.set_token_key'], obj=token)
provider = OAuth2Provider.objects.get(client_id='labape')
role.assign_perms(['authentik_providers_oauth2.view_oauth2provider',
                   'authentik_providers_oauth2.change_oauth2provider'], obj=provider)
print('authentik-test: labape-token-rotator may set the directory token key and the client secret')
" 2>/dev/null | tail -1 || echo "authentik-test: couldn't grant the rotator its permissions (see $COMPOSE logs worker)" >&2

if $CONFIGURE; then
  env_file=../../container/.env
  [ -f "$env_file" ] || { echo "authentik-test: run container/setup.sh first" >&2; exit 1; }
  # LABaPe and browsers use Authentik over HTTPS through the stack's Caddy (port 9445, the web
  # interface's certificate, design §47); plain HTTP on 9000 stays for this script's own checks.
  https_issuer="https://${LABAPE_HOSTNAME_ARG}:${LABAPE_AUTH_PORT:-9445}/application/o/labape/"
  sed -i "s|^LABAPE_OIDC_ISSUER=.*|LABAPE_OIDC_ISSUER=${https_issuer}|; s|^LABAPE_OIDC_CLIENT_ID=.*|LABAPE_OIDC_CLIENT_ID=labape|" "$env_file"
  # set-secret.sh writes where the stack reads it: container/secrets/, or the memory
  # copy plus the platform store once the bootstrap secrets have moved (design §26.3).
  # It runs as the owner of container/ (this script usually runs as root).
  owner="$(stat -c %U ../../container)"
  as_owner=(); [ "$(id -un)" = "$owner" ] || as_owner=(runuser -u "$owner" --)
  grep '^LABAPE_TEST_CLIENT_SECRET=' .env | cut -d= -f2- | "${as_owner[@]}" bash ../../container/stores/set-secret.sh oidc_client_secret
  echo "authentik-test: container/.env and the stack's oidc_client_secret now point at $https_issuer"
  echo "               (recreate the LABaPe containers: $COMPOSE up -d)"
fi

cat <<EOF
authentik-test: ready.
  Issuer:      $issuer
  Admin UI:    http://${PUBLIC_HOST}:9000/  (user akadmin; password: AUTHENTIK_BOOTSTRAP_PASSWORD in tools/authentik-test/.env)
  Test users:  labape-admin, labape-dev, labape-viewer (password: LABAPE_TEST_USER_PASSWORD in .env)
  Group search: LABaPe Settings > Directory; API token LABAPE_TEST_DIRECTORY_TOKEN in .env (view groups only)
EOF
