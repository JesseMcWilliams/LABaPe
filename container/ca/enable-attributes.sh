#!/bin/bash
# Make the platform CA apply the key usage and extended key usage attributes a request asks for (Certificates >
# Request > Attributes). LABaPe passes them to the labape-runner provisioner as template data (`.Insecure.User`); this installs the
# template that turns them into the certificate. Run it once on an existing stack's CA, then tick "The CA applies
# extra attributes" under Certificates > The CA:
#
#   docker exec labape-ca bash /labape/enable-attributes.sh     (or podman exec ...)
#
# New stacks get it from init.sh. The CA reads provisioner changes when it restarts.
set -euo pipefail
export STEPPATH=/home/step
step ca provisioner update labape-runner --x509-template /labape/leaf.tpl >/dev/null
echo "labape-ca: the labape-runner provisioner now applies requested attributes; restart the CA container to load it"
