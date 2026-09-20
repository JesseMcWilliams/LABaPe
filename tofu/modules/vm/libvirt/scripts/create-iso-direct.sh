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

# A domain merely *existing* isn't enough to call this idempotent — an
# interrupted/failed previous install (crash, kill, timeout above) can
# leave a defined-but-not-running domain behind. Only a running domain
# is treated as "already done"; anything else is assumed broken and
# rebuilt from scratch, since this is a one-shot install, not a
# reconciled resource (see kickstart_md5 in main.tf's triggers).
if existing_state="$(virsh --connect "$LIBVIRT_URI" domstate "$VM_NAME" 2>/dev/null)"; then
  if [ "$existing_state" = "running" ]; then
    echo "labape: VM '$VM_NAME' already exists and is running on $LIBVIRT_URI — skipping create (idempotent no-op)." >&2
    exit 0
  fi
  echo "labape: VM '$VM_NAME' exists but is not running (state: $existing_state) — treating as a leftover from an interrupted install and removing it before recreating." >&2
  virsh --connect "$LIBVIRT_URI" destroy "$VM_NAME" >/dev/null 2>&1 || true
  virsh --connect "$LIBVIRT_URI" undefine "$VM_NAME" --remove-all-storage
fi

# --initrd-inject places KICKSTART_PATH at the root of the injected
# initrd, so the kernel argument below references it by basename, not
# by its original path on the control machine.
kickstart_basename="$(basename "$KICKSTART_PATH")"

# log.file= mirrors the serial console to a plain file alongside
# libvirt's own per-domain log (/var/log/libvirt/qemu/<name>.log), so a
# stuck/failed install is debuggable after the fact without needing a
# live `virsh console` attach — the M1 smoke test hit two separate
# installs that hard-hung with zero console output and no way to tell
# why. --wait is capped (was -1/infinite) and wrapped in `timeout` so a
# hung install fails this script within a bounded time instead of
# blocking the whole deploy indefinitely; the VM itself is left running
# either way for post-mortem inspection via the console log.
console_log="/var/log/libvirt/qemu/${VM_NAME}-console.log"
install_timeout_seconds=1800

if ! timeout "$install_timeout_seconds" virt-install \
  --connect "$LIBVIRT_URI" \
  --name "$VM_NAME" \
  --vcpus "$CPU_COUNT" \
  --memory "$MEMORY_MB" \
  --disk "size=${DISK_GB},format=qcow2" \
  --location "$ISO_HOST_PATH" \
  --initrd-inject "$KICKSTART_PATH" \
  --extra-args "inst.ks=file:/${kickstart_basename} console=ttyS0" \
  --network "bridge=${BRIDGE_DEVICE},model=virtio" \
  --os-variant detect=on,require=off \
  --graphics none \
  --console "pty,target_type=serial,log.file=${console_log},log.append=off" \
  --noautoconsole \
  --wait -1; then
  echo "labape: '$VM_NAME' install did not finish within ${install_timeout_seconds}s (or virt-install failed outright)." >&2
  echo "labape: the VM is left running for inspection — console log: $console_log (root-owned; e.g. sudo cat, or sudo cp --no-preserve=mode to a readable copy)." >&2
  exit 1
fi

echo "labape: '$VM_NAME' install complete (virt-install returned after the post-install reboot)." >&2
