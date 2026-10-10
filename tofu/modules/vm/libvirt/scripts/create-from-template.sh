#!/usr/bin/env bash
# Invoked by tofu/modules/vm/libvirt/main.tf's vm_from_template local-exec
# provisioner (image_source = "packer_template"). Not meant to be run by
# hand — Claude_Docs/Design_Base-Images.md §5/§8 for the design.
#
# Creates the VM's disk as a qcow2 overlay whose read-only backing file is
# the template (so the template is never written to, and many VMs share
# it), attaches a small CD carrying the VM's first-boot identity, and
# boots it. Returns as soon as the VM is defined and started; Ansible's
# first play waits for it to answer.
set -euo pipefail

# shellcheck source=lib/safe-undefine.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/safe-undefine.sh"
# shellcheck source=lib/existing-domain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/existing-domain.sh"

: "${LIBVIRT_URI:?}"
: "${VM_NAME:?}"
: "${CPU_COUNT:?}"
: "${MEMORY_MB:?}"
: "${DISK_GB:?}"
: "${TEMPLATE_PATH:?}"
: "${SEED_DIR:?}"
: "${BRIDGE_DEVICE:?}"
: "${MAC_ADDRESS:?}"
: "${OS_FAMILY:?}"
: "${OS_VARIANT:=}"
: "${VM_STORAGE_PATH:?}"
: "${LABAPE_WORKSPACE:?}"

workspace_tag="labape-workspace=${LABAPE_WORKSPACE}"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "labape: template not found: $TEMPLATE_PATH — build it with Packer or scripts/promote-to-template.sh first (Claude_Docs/Design_Base-Images.md)." >&2
  exit 1
fi

# An overlay smaller than its template truncates the guest's disk: the
# root LV's tail goes missing and the clone drops to a dracut emergency
# shell (Claude_Docs/Testing_Troubleshooting-Log.md). The template's size
# is the floor, so raise a smaller request to it.
template_gb="$(qemu-img info -U --output=json "$TEMPLATE_PATH" | python3 -c 'import json, math, sys; print(math.ceil(json.load(sys.stdin)["virtual-size"] / 2**30))')"
if [ "$DISK_GB" -lt "$template_gb" ]; then
  echo "labape: $VM_NAME: disk_gb $DISK_GB is smaller than template $(basename "$TEMPLATE_PATH") ($template_gb GB); using $template_gb GB." >&2
  DISK_GB="$template_gb"
fi

handle_existing_domain "$LIBVIRT_URI" "$VM_NAME" "$workspace_tag"

mkdir -p "$VM_STORAGE_PATH"
disk_path="${VM_STORAGE_PATH}/${VM_NAME}.qcow2"
stage_dir="$(mktemp -d)"
trap 'rm -rf "$stage_dir"' EXIT

if [ "$OS_FAMILY" = "windows" ]; then
  # The answer file at the root of a CD, comments stripped (Setup's XML
  # parser rejects multi-line comments — create-iso-direct.sh's windows)
  # branch has the history). Named unattend.xml, NOT autounattend.xml:
  # Setup only reads Autounattend.xml from removable media for the
  # windowsPE/offlineServicing passes; the specialize and oobeSystem
  # passes a sysprepped template runs need unattend.xml (Microsoft's
  # "Windows Setup Automation Overview"; a clone with autounattend.xml
  # came up ignoring it, asking for a new Administrator password).
  seed_iso="${VM_STORAGE_PATH}/${VM_NAME}-autounattend.iso"
  perl -0777 -pe 's/<!--.*?-->//gs' "$SEED_DIR/autounattend.xml" > "$stage_dir/unattend.xml"
  cp "$SEED_DIR/firstboot.ps1" "$stage_dir/firstboot.ps1"
  xorrisofs -o "$seed_iso" -V AUTOUNATTEND -J -r "$stage_dir" >/dev/null
  disk_bus=sata          # what the template was installed on
  nic_model=e1000e       # in-box driver; the answer file names it "Ethernet"
  graphics=(--graphics vnc,listen=127.0.0.1)
  # Legacy BIOS like the iso_direct path: windows_11 is cataloged as
  # os_variant win10 for that reason (tofu/backends/libvirt/main.tf).
else
  # cloud-init NoCloud: found by the CIDATA volume label.
  seed_iso="${VM_STORAGE_PATH}/${VM_NAME}-seed.iso"
  cp "$SEED_DIR/user-data" "$SEED_DIR/meta-data" "$SEED_DIR/network-config" "$stage_dir/"
  xorrisofs -o "$seed_iso" -V CIDATA -J -r "$stage_dir" >/dev/null
  disk_bus=virtio
  nic_model=virtio
  console_log="/var/log/libvirt/qemu/${VM_NAME}-console.log"
  graphics=(--graphics none --console "pty,target_type=serial,log.file=${console_log},log.append=off")
fi
sync "$seed_iso"

os_variant_args=(--os-variant "${OS_VARIANT:-detect=on,require=off}")

virt-install \
  --connect "$LIBVIRT_URI" \
  --name "$VM_NAME" \
  --vcpus "$CPU_COUNT" \
  --memory "$MEMORY_MB" \
  --import \
  --disk "path=${disk_path},size=${DISK_GB},format=qcow2,backing_store=${TEMPLATE_PATH},backing_format=qcow2,bus=${disk_bus}" \
  --disk "path=${seed_iso},device=cdrom,bus=sata" \
  --network "bridge=${BRIDGE_DEVICE},model=${nic_model},mac=${MAC_ADDRESS}" \
  "${os_variant_args[@]}" \
  "${graphics[@]}" \
  --noautoconsole \
  --metadata "description=${workspace_tag}" \
  --wait 0

echo "labape: '$VM_NAME' cloned from $(basename "$TEMPLATE_PATH") and started; first-boot setup continues inside the guest." >&2
