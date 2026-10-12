#!/bin/bash
# Re-encrypt the platform CA's root and intermediate keys with a new password
# (design §24, phase R3). Run inside the labape-ca container by the update
# agent:  repass.sh <old password file> <new password file>
# The labape-runner provisioner's key is re-encrypted by `labape ca-repass`
# (`step crypto jwe encrypt` only takes a password at a prompt).
set -euo pipefail
export STEPPATH="${STEPPATH:-/home/step}"   # overridable for a dry run on copies
old="$1" new="$2"
for k in root_ca_key intermediate_ca_key; do
  step crypto change-pass "$STEPPATH/secrets/$k" --password-file "$old" --new-password-file "$new" --force >/dev/null 2>&1
done
echo "labape-ca: root and intermediate keys re-encrypted"
