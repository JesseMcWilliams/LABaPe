#!/usr/bin/env bash
# Invoked by tofu/modules/vm/libvirt/main.tf's local-exec provisioner.
# Not meant to be run by hand — see docs/base-images.md §4 for the
# design this implements.
set -euo pipefail

: "${LIBVIRT_URI:?}"
: "${VM_NAME:?}"
: "${CPU_COUNT:?}"
: "${MEMORY_MB:?}"
: "${DISK_GB:?}"
: "${ISO_HOST_PATH:?}"
: "${KICKSTART_PATH:?}"
: "${BRIDGE_DEVICE:?}"

if virsh --connect "$LIBVIRT_URI" domstate "$VM_NAME" >/dev/null 2>&1; then
  echo "labape: VM '$VM_NAME' already exists on $LIBVIRT_URI — skipping create (idempotent no-op)." >&2
  exit 0
fi

# --initrd-inject places KICKSTART_PATH at the root of the injected
# initrd, so the kernel argument below references it by basename, not
# by its original path on the control machine.
kickstart_basename="$(basename "$KICKSTART_PATH")"

virt-install \
  --connect "$LIBVIRT_URI" \
  --name "$VM_NAME" \
  --vcpus "$CPU_COUNT" \
  --memory "$MEMORY_MB" \
  --disk "size=${DISK_GB},format=qcow2" \
  --cdrom "$ISO_HOST_PATH" \
  --initrd-inject "$KICKSTART_PATH" \
  --extra-args "inst.ks=file:/${kickstart_basename} console=ttyS0" \
  --network "bridge=${BRIDGE_DEVICE},model=virtio" \
  --os-variant detect=on,require=off \
  --graphics none \
  --console pty,target_type=serial \
  --noautoconsole \
  --wait -1

echo "labape: '$VM_NAME' install complete (virt-install returned after the post-install reboot)." >&2
