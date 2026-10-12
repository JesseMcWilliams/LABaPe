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
# - ACME provisioner "acme": Caddy's certificate for the web interface.
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
# Readable by the other containers that mount this volume (the root and intermediate certificates only).
chmod 0755 "$STEPPATH" "$STEPPATH/certs"
chmod 0644 "$STEPPATH/certs/root_ca.crt" "$STEPPATH/certs/intermediate_ca.crt"
exec /usr/local/bin/step-ca --password-file "$PASSWORD" "$STEPPATH/config/ca.json"
