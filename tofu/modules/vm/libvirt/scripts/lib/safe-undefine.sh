#!/usr/bin/env bash
# Shared by create-iso-direct.sh (cleaning up a broken leftover domain
# before recreating it) and destroy-vm.sh (a real teardown) — the ONE
# place this logic exists now, after it existed in both scripts
# separately and only one copy got fixed when the bug below was first
# found and patched.
#
# `virsh undefine --remove-all-storage` deletes every storage volume
# attached to the domain, including read-only media that isn't
# LABaPe's to delete — for Windows, --cdrom leaves the *shared* master
# install ISO as a persistent <disk device='cdrom'> (unlike Linux's
# --location, which doesn't), so that flag deletes shared install
# media out from under every other environment on this host. This
# actually happened once during development. Undefine without it, then
# remove only what create-iso-direct.sh actually created for this VM:
# its own disk, and (Windows) the generated per-VM answer-file ISO —
# identified by path, not "every attached disk."
#
# Usage: safe_undefine <libvirt-uri> <vm-name>
safe_undefine() {
  local uri="$1" name="$2"

  local disk_paths
  disk_paths="$(
    virsh --connect "$uri" domblklist "$name" --details 2>/dev/null \
      | awk '$2=="disk"{print $4} $2=="cdrom" && $4 ~ /-autounattend\.iso$/{print $4}'
  )"

  # --nvram: a UEFI (OVMF) domain has a per-VM NVRAM variable store and
  # plain undefine refuses it ("cannot undefine domain with nvram").
  # Removes only that VM's own vars file; a no-op on BIOS domains, which
  # every LABaPe VM is today (windows_11 uses os_variant win10 to avoid
  # UEFI, see tofu/backends/libvirt/main.tf), kept so a hand-made or
  # older UEFI leftover can't wedge a destroy.
  virsh --connect "$uri" undefine "$name" --nvram

  # libvirtd creates these with dynamic_ownership (libvirt-qemu:kvm) in
  # a root-owned, non-world-writable directory — plain `rm` as this
  # unprivileged user can't unlink them regardless of the file's own
  # permissions (Unix unlink needs write on the *parent dir*, not the
  # file). sudo is scoped to exactly this command, not a blanket
  # escalation.
  while IFS= read -r p; do
    [ -n "$p" ] && [ -f "$p" ] && sudo -n /usr/bin/rm -f "$p"
  done <<<"$disk_paths"
}
