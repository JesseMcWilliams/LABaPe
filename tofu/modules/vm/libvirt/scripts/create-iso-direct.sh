#!/usr/bin/env bash
# Invoked by tofu/modules/vm/libvirt/main.tf's local-exec provisioner.
# Not meant to be run by hand — see docs/base-images.md §4 for the
# design this implements.
set -euo pipefail

# shellcheck source=lib/safe-undefine.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/safe-undefine.sh"

: "${LIBVIRT_URI:?}"
: "${VM_NAME:?}"
: "${CPU_COUNT:?}"
: "${MEMORY_MB:?}"
: "${DISK_GB:?}"
: "${ISO_HOST_PATH:?}"
: "${ANSWER_FILE_PATH:?}"
: "${BRIDGE_DEVICE:?}"
: "${OS_FAMILY:?}"
: "${VM_STORAGE_PATH:?}"

# A domain merely *existing* isn't enough to call this idempotent — an
# interrupted/failed previous install (crash, kill, timeout below) can
# leave a defined-but-not-running domain behind. Only a running domain
# is treated as "already done"; anything else is assumed broken and
# rebuilt from scratch, since this is a one-shot install, not a
# reconciled resource (see answer_file_md5 in main.tf's triggers).
if existing_state="$(virsh --connect "$LIBVIRT_URI" domstate "$VM_NAME" 2>/dev/null)"; then
  if [ "$existing_state" = "running" ]; then
    echo "labape: VM '$VM_NAME' already exists and is running on $LIBVIRT_URI — skipping create (idempotent no-op)." >&2
    exit 0
  fi
  echo "labape: VM '$VM_NAME' exists but is not running (state: $existing_state) — treating as a leftover from an interrupted install and removing it before recreating." >&2
  virsh --connect "$LIBVIRT_URI" destroy "$VM_NAME" >/dev/null 2>&1 || true
  safe_undefine "$LIBVIRT_URI" "$VM_NAME"
fi

mkdir -p "$VM_STORAGE_PATH"
disk_path="${VM_STORAGE_PATH}/${VM_NAME}.qcow2"

# log.file= (Linux path only, see below) mirrors the serial console to
# a plain file alongside libvirt's own per-domain log
# (/var/log/libvirt/qemu/<name>.log), so a stuck/failed install is
# debuggable after the fact without needing a live `virsh console`
# attach — the M1 smoke test hit two separate installs that hard-hung
# with zero console output and no way to tell why. --wait is capped
# (was -1/infinite) and wrapped in `timeout` so a hung install fails
# this script within a bounded time instead of blocking the whole
# deploy indefinitely; the VM itself is left running either way for
# post-mortem inspection.
console_log="/var/log/libvirt/qemu/${VM_NAME}-console.log"

case "$OS_FAMILY" in
linux)
  install_timeout_seconds=1800

  # --initrd-inject places ANSWER_FILE_PATH at the root of the injected
  # initrd, so the kernel argument below references it by basename, not
  # by its original path on the control machine.
  kickstart_basename="$(basename "$ANSWER_FILE_PATH")"

  if ! timeout "$install_timeout_seconds" virt-install \
    --connect "$LIBVIRT_URI" \
    --name "$VM_NAME" \
    --vcpus "$CPU_COUNT" \
    --memory "$MEMORY_MB" \
    --disk "path=${disk_path},size=${DISK_GB},format=qcow2" \
    --location "$ISO_HOST_PATH" \
    --initrd-inject "$ANSWER_FILE_PATH" \
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
  ;;

windows)
  : "${OS_VARIANT:?}"
  install_timeout_seconds=3600

  # Windows Setup auto-detects an autounattend.xml at the root of any
  # attached optical/floppy media — no kernel-argument hook like
  # Linux's inst.ks= exists (docs/base-images.md §4), so build a tiny
  # ISO containing just that file and attach it as a second CD-ROM.
  # This is intermittently missed (~40% failure, README's Known Gaps —
  # see that doc for the full investigation, since this is the surviving
  # approach after several ruled-out alternatives, not the first one
  # tried). Two of the strongest candidate fixes were disproven with a
  # live VNC session rather than just theorized: a floppy in place of
  # the second CD-ROM (checked earliest in Setup's search order, no
  # optical/SCSI negotiation) hit the exact same failure — confirmed via
  # a WinPE Shift+F10 shell that the file was present and readable both
  # times, on both media types, ruling out placement/format/enumeration-
  # timing as the cause. Folding autounattend.xml directly into the boot
  # ISO (no second device at all) was also retried with a fresh mkisofs
  # recipe from https://palant.info/2023/02/13/automating-windows-installation-in-a-vm/
  # — it re-hit the exact two dead ends this repo had already separately
  # documented (BOOTMGR not found without -boot-info-table; a CPU-spin
  # hang at the boot screen with it), confirming that's a genuine
  # xorrisofs/toolchain incompatibility on this host, not a flag to
  # tune around.
  answer_iso="${VM_STORAGE_PATH}/${VM_NAME}-autounattend.iso"
  answer_stage_dir="$(mktemp -d)"
  trap 'rm -rf "$answer_stage_dir"' EXIT
  cp "$ANSWER_FILE_PATH" "$answer_stage_dir/autounattend.xml"
  xorrisofs -o "$answer_iso" -V AUTOUNATTEND -J -r "$answer_stage_dir" >/dev/null

  # bus=sata / model=e1000e (not virtio) on purpose — both have in-box
  # Windows Server 2022 drivers, avoiding the virtio-win
  # driver-injection dance entirely for this first pass.
  #
  # --graphics vnc (not none, unlike the Linux path above): Windows
  # Setup is a graphical installer with no serial-console equivalent to
  # Linux's console=ttyS0, so --graphics none leaves genuinely nothing
  # to inspect if it stalls — confirmed the hard way during development
  # (only a live VNC/screenshot attach ever showed anything useful).
  # listen=127.0.0.1 keeps it host-local; view it via Cockpit's Virtual
  # Machines page (docs/install-opentofu.md §8) or, absent Cockpit,
  # `virsh -c qemu:///system screenshot <name> out.png`.
  if ! timeout "$install_timeout_seconds" virt-install \
    --connect "$LIBVIRT_URI" \
    --name "$VM_NAME" \
    --vcpus "$CPU_COUNT" \
    --memory "$MEMORY_MB" \
    --disk "path=${disk_path},size=${DISK_GB},format=qcow2,bus=sata" \
    --disk "path=${answer_iso},device=cdrom,bus=sata" \
    --cdrom "$ISO_HOST_PATH" \
    --network "bridge=${BRIDGE_DEVICE},model=e1000e" \
    --os-variant "$OS_VARIANT" \
    --graphics vnc,listen=127.0.0.1 \
    --noautoconsole \
    --wait -1; then
    echo "labape: '$VM_NAME' install did not finish within ${install_timeout_seconds}s (or virt-install failed outright)." >&2
    echo "labape: the VM is left running for inspection — view its console via Cockpit's Virtual Machines page, or 'virsh -c $LIBVIRT_URI screenshot $VM_NAME out.png'." >&2
    exit 1
  fi
  ;;

*)
  echo "labape: unknown OS_FAMILY \"$OS_FAMILY\" — expected \"linux\" or \"windows\"." >&2
  exit 1
  ;;
esac

echo "labape: '$VM_NAME' install complete (virt-install returned after the post-install reboot)." >&2
