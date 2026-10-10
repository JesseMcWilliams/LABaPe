#!/usr/bin/env bash
# One-time setup for the LABaPe container stack: writes container/.env,
# generates the database password, session key and connection strings
# under container/secrets/, and creates the config and engine-secrets
# directories. Safe to re-run: existing files are kept.
#
# Usage: container/setup.sh [--hostname labape.example.lan] [--tls internal|<acme-email>]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

HOSTNAME_ARG=""
TLS_ARG="internal"
while [ $# -gt 0 ]; do
  case "$1" in
    --hostname) HOSTNAME_ARG="${2:?--hostname needs a value}"; shift ;;
    --tls) TLS_ARG="${2:?--tls needs a value}"; shift ;;
    *) echo "usage: setup.sh [--hostname <name>] [--tls internal|<acme-email>]" >&2; exit 1 ;;
  esac
  shift
done

if [ ! -f .env ]; then
  if [ -z "$HOSTNAME_ARG" ]; then
    read -r -p "Hostname browsers will use for LABaPe (e.g. labape.lab.lan): " HOSTNAME_ARG
  fi
  [ -n "$HOSTNAME_ARG" ] || { echo "labape: a hostname is required." >&2; exit 1; }
  cat > .env <<EOF
# LABaPe container settings (see container/README.md)
LABAPE_HOSTNAME=$HOSTNAME_ARG
LABAPE_TLS=$TLS_ARG
LABAPE_OIDC_ISSUER=
LABAPE_OIDC_CLIENT_ID=
LABAPE_CONFIG_PATH=./config
LABAPE_ENGINE_SECRETS_PATH=./engine-secrets
LABAPE_DATA_ROOT=/data
EOF
  echo "labape: wrote container/.env"
fi

umask 077
mkdir -p secrets config engine-secrets/ssh
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
[ -f secrets/postgres_password ] || gen > secrets/postgres_password
[ -f secrets/secret_key ] || gen > secrets/secret_key
pw="$(cat secrets/postgres_password)"
[ -f secrets/database_url ] || echo "postgresql+psycopg://labape:${pw}@postgres/labape" > secrets/database_url
[ -f secrets/tofu_pg_conn ] || echo "postgres://labape:${pw}@postgres/labape?sslmode=disable" > secrets/tofu_pg_conn
[ -f secrets/oidc_client_secret ] || : > secrets/oidc_client_secret
# Containers read these as their own users (postgres reads its password
# file as uid 70), so the files are world-readable; the 0700 directory
# keeps other host users out.
chmod 700 secrets engine-secrets
chmod 644 secrets/*

cat <<'EOF'
labape: secrets are in container/secrets/ (never commit them).
Next:
  1. container/config/: environment.yml (from tofu/environment.example.yml),
     software-manifest.yml, and optionally directory-manifest.yml.
  2. container/engine-secrets/: secrets.vault.yml, vault-pass, and ssh/ with the
     key pair named by ansible_ssh_private_key_path in the vault.
  3. For Authentik sign-in: LABAPE_OIDC_ISSUER and LABAPE_OIDC_CLIENT_ID in .env,
     and the client secret in secrets/oidc_client_secret.
  4. docker compose up -d --build
  5. docker compose exec labape-api labape breakglass enable   (first admin sign-in)
EOF
