#!/usr/bin/env bash
# Invoked by tofu/modules/vm/libvirt/main.tf's destroy-time local-exec
# provisioner. Not meant to be run by hand.
set -euo pipefail

# shellcheck source=lib/safe-undefine.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/safe-undefine.sh"

: "${LIBVIRT_URI:?}"
: "${VM_NAME:?}"

if ! virsh --connect "$LIBVIRT_URI" domstate "$VM_NAME" >/dev/null 2>&1; then
  echo "labape: VM '$VM_NAME' not found on $LIBVIRT_URI — already gone, nothing to do." >&2
  exit 0
fi

virsh --connect "$LIBVIRT_URI" destroy "$VM_NAME" >/dev/null 2>&1 || true
safe_undefine "$LIBVIRT_URI" "$VM_NAME"

echo "labape: '$VM_NAME' destroyed and undefined." >&2
