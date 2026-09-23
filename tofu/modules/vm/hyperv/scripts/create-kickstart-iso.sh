#!/usr/bin/env bash
# Invoked once per VM instance by tofu/modules/vm/hyperv/main.tf's
# local-exec provisioner — the per-VM counterpart to
# scripts/prepare-boot-iso.sh's once-per-OS boot media. Builds a tiny
# ISO containing just this VM's rendered kickstart at its root (as
# ks.cfg, matching inst.ks=cdrom:/ks.cfg baked into the boot media) and
# stages it on the Hyper-V host next to the boot ISO.
set -euo pipefail

: "${KICKSTART_LOCAL_PATH:?}"   # rendered by local_file.kickstart, main.tf
: "${HYPERV_HOST:?}"
: "${HYPERV_USER:?}"
: "${HYPERV_PASSWORD:?}"
: "${ISO_STORAGE_PATH:?}"       # Windows path on the Hyper-V host, e.g. C:\ISOs
: "${DEST_ISO_NAME:?}"          # e.g. linsrv1-ks.iso

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

stage_dir="$work_dir/stage"
mkdir -p "$stage_dir"
cp "$KICKSTART_LOCAL_PATH" "$stage_dir/ks.cfg"

built_iso="$work_dir/$DEST_ISO_NAME"
xorrisofs -o "$built_iso" -V KICKSTART -J -r "$stage_dir" >/dev/null

remote_rel_path="$(echo "$ISO_STORAGE_PATH" | sed 's#^[A-Za-z]:\\*##')"
smbclient -U "${HYPERV_USER}%${HYPERV_PASSWORD}" "//${HYPERV_HOST}/${ISO_STORAGE_PATH%%:*}\$" \
  -c "mkdir ${remote_rel_path} ; put \"$built_iso\" \"${remote_rel_path}\\${DEST_ISO_NAME}\"" \
  >/dev/null

echo "labape: staged ${DEST_ISO_NAME} to ${HYPERV_HOST}:${ISO_STORAGE_PATH}\\${DEST_ISO_NAME}" >&2
