#!/bin/bash
# LABaPe platform CA (Claude_Docs/Planning_Certificate-Authority.md §5,
# design §22.5): first start initializes step-ca, then it runs.
#
# Our own init instead of the image's DOCKER_STEPCA_INIT_*: that one
# prints the provisioner password into the container log, and LABaPe uses
# the same password (secrets/ca_password) to have certificates signed.
#
# - Root and intermediate as `step ca init` makes them (10 years each).
# - JWK provisioner "labape-runner": LABaPe's API signs VMs' CSRs with it;
#   leaf certificates last 30 days by default, at most 90.
# - ACME provisioner "acme": Caddy's certificate for the web interface, and any ACME client on the LAN
#   (Home Assistant's Let's Encrypt add-on, certbot). An ACME client can't ask for a lifetime, so the
#   provisioner's own default is what it gets: 90 days (step-ca's built-in 24 hours made such clients
#   renew daily). Set on every start, so a CA made before this is corrected too.
set -euo pipefail
export STEPPATH=/home/step
PASSWORD=/run/secrets/ca_password
LABAPE_CA_DNS="${LABAPE_CA_DNS:-labape-ca,localhost,${LABAPE_HOSTNAME:?LABAPE_HOSTNAME is not set}}"

if [ ! -f "$STEPPATH/config/ca.json" ]; then
  echo "labape-ca: initializing ${LABAPE_CA_NAME:-LABaPe CA} for ${LABAPE_CA_DNS}"
  step ca init --deployment-type standalone --name "${LABAPE_CA_NAME:-LABaPe CA}" \
    --dns "${LABAPE_CA_DNS}" --address ":9000" \
    --provisioner labape-runner --password-file "$PASSWORD" --provisioner-password-file "$PASSWORD" \
    --acme >/dev/null
  step ca provisioner update labape-runner --x509-default-dur 720h --x509-max-dur 2160h >/dev/null
  echo "labape-ca: root fingerprint $(step certificate fingerprint "$STEPPATH/certs/root_ca.crt")"
fi
# The ACME provisioner's certificate lifetime: 90 days by default and at most (certbot-style clients renew
# with 30 days left). An admin's own choice (any claim already set on it) is kept.
if [ -z "$(jq -r '.authority.provisioners[]? | select(.name == "acme") | .claims.defaultTLSCertDuration // empty' "$STEPPATH/config/ca.json")" ]; then
  step ca provisioner update acme --x509-default-dur 2160h --x509-max-dur 2160h >/dev/null \
    && echo "labape-ca: ACME certificates last 90 days" \
    || echo "labape-ca: could not set the ACME certificate lifetime (they stay at step-ca's 24 hours)" >&2
fi
# The CA's database is a database of the stack's PostgreSQL ("stepca"), not step-ca's Badger file, so that LABaPe can
# list every certificate the CA issued, ACME clients' included (Certificates page). Set on every start: the password
# follows a rotation. The first start after the switch begins with an empty database: ACME clients register again
# (the old Badger folder, /home/step/db, is left where it is).
DBURL_FILE=/run/secrets/database_url
if [ -s "$DBURL_FILE" ]; then
  # postgresql+psycopg://user:password@host/dbname  ->  postgresql://user:password@host:5432/?sslmode=disable
  ds="$(tr -d '\n' < "$DBURL_FILE" | sed -E 's#^postgresql(\+[a-z0-9]+)?://([^@]+)@([^/:]+)(:[0-9]+)?/.*$#postgresql://\2@\3:5432/?sslmode=disable#')"
  tmp="$STEPPATH/config/ca.json.tmp"
  if jq --arg ds "$ds" --arg db "${LABAPE_CA_DB:-stepca}" '.db = {type: "postgresql", dataSource: $ds, database: $db}' "$STEPPATH/config/ca.json" > "$tmp" \
     && ! cmp -s "$tmp" "$STEPPATH/config/ca.json"; then
    mv "$tmp" "$STEPPATH/config/ca.json"
    echo "labape-ca: the CA's database is ${LABAPE_CA_DB:-stepca} in the stack's PostgreSQL"
  else
    rm -f "$tmp"
  fi
fi
# The names the CA answers to follow LABAPE_CA_DNS on every start (the web interface's host name
# can change after the CA was made): missing ones are added to ca.json, old ones kept so existing
# clients keep working. step-ca issues its own TLS certificate for these names when it starts.
want="$(printf '%s' "$LABAPE_CA_DNS" | jq -R 'split(",") | map(select(length > 0))')"
tmp="$STEPPATH/config/ca.json.tmp"
if jq --argjson want "$want" '.dnsNames = ((.dnsNames // []) + $want | unique)' "$STEPPATH/config/ca.json" > "$tmp" \
   && ! cmp -s "$tmp" "$STEPPATH/config/ca.json"; then
  mv "$tmp" "$STEPPATH/config/ca.json"
  echo "labape-ca: answering to $(jq -r '.dnsNames | join(", ")' "$STEPPATH/config/ca.json")"
else
  rm -f "$tmp"
fi
# Readable by the other containers that mount this volume (the root and intermediate certificates only).
chmod 0755 "$STEPPATH" "$STEPPATH/certs"
chmod 0644 "$STEPPATH/certs/root_ca.crt" "$STEPPATH/certs/intermediate_ca.crt"
exec /usr/local/bin/step-ca --password-file "$PASSWORD" "$STEPPATH/config/ca.json"
