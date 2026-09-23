#!/usr/bin/env bash
# Invoked once per distinct `os` value by tofu/backends/hyperv/main.tf's
# local-exec provisioner — NOT per VM instance, since every VM of the
# same OS boots from the identical modified media (docs/base-images.md
# §4, Hyper-V section).
#
# DESIGN.md §6.1: Hyper-V has no kernel-argument-injection hook like
# libvirt's virt-install --initrd-inject, and Windows' own answer-file
# auto-detection turned out to be unusable (README's Known Gaps for the
# libvirt/Windows saga this repo went through first). For RHEL-family
# Linux, isolinux's boot menu is far more tractable: this copies the
# vendor ISO, adds one new boot entry that appends inst.ks=cdrom:/ks.cfg
# and marks it `menu default`, and rebuilds with the same xorrisofs
# recipe every "embed a kickstart in a custom RHEL ISO" tutorial uses —
# a well-trodden path, unlike the dead end Windows' El Torito/BOOTMGR
# loader turned out to be. Confirmed end-to-end on real infrastructure
# (a full unattended Rocky 9 kickstart install, 333/333 packages) before
# this script existed, via the same commands by hand.
set -euo pipefail

: "${VENDOR_ISO_LOCAL_PATH:?}"   # path on THIS (control) machine — os_iso_paths' value
: "${HYPERV_HOST:?}"
: "${HYPERV_USER:?}"
: "${HYPERV_PASSWORD:?}"
: "${ISO_STORAGE_PATH:?}"        # Windows path on the Hyper-V host, e.g. C:\ISOs
: "${DEST_ISO_NAME:?}"           # e.g. rocky9-boot.iso

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

# xorriso writes -pvd_info's actual output to stderr, not stdout —
# confirmed by testing, not assumed; merge it in rather than losing it.
volume_id="$(xorriso -indev "$VENDOR_ISO_LOCAL_PATH" -pvd_info 2>&1 1>/dev/null | grep -m1 '^Volume id' | sed "s/^Volume id *: *//;s/ *\$//;s/^'//;s/'\$//")"
if [ -z "$volume_id" ]; then
  echo "labape: could not read a Volume id from $VENDOR_ISO_LOCAL_PATH — is this really an ISO9660 image?" >&2
  exit 1
fi

extract_dir="$work_dir/extract"
mkdir -p "$extract_dir"
( cd "$extract_dir" && 7z x -y "$VENDOR_ISO_LOCAL_PATH" >/dev/null )

isolinux_cfg="$extract_dir/isolinux/isolinux.cfg"
if [ ! -f "$isolinux_cfg" ]; then
  echo "labape: $VENDOR_ISO_LOCAL_PATH has no isolinux/isolinux.cfg — not a BIOS-bootable RHEL-family ISO this script knows how to modify." >&2
  exit 1
fi

# Insert a new default entry right before the first `label` line (the
# menu's existing entries, whichever they're named, are left completely
# untouched below it — only the default selection moves to ours), and
# strip `menu default` from wherever it was set before. quiet is kept
# (matches every other entry) so this doesn't spam kernel boot logs
# unnecessarily; inst.ks=cdrom scans attached optical media for ks.cfg
# at its root, matching the second CD-ROM tofu/modules/vm/hyperv/main.tf
# attaches (docs/base-images.md §4).
python3 - "$isolinux_cfg" "$volume_id" <<'PYEOF'
import re, sys

path, volume_id = sys.argv[1], sys.argv[2]
content = open(path).read()

entry = (
    "label labape-ks\n"
    "  menu label ^LABaPe unattended install\n"
    "  menu default\n"
    "  kernel vmlinuz\n"
    f"  append initrd=initrd.img inst.stage2=hd:LABEL={volume_id} inst.ks=cdrom:/ks.cfg quiet\n\n"
)

# Strip whichever existing entry currently has "menu default" (there's
# exactly one, always some entry other than ours since ours doesn't
# exist yet) before inserting ours with it instead.
content = re.sub(r"(label \S+\n(?:.*\n)*?)  menu default\n", r"\1", content, count=1)
content = re.sub(r"^label ", entry + "label ", content, count=1, flags=re.M)
content = re.sub(r"timeout \d+", "timeout 50", content, count=1)

open(path, "w").write(content)
PYEOF

built_iso="$work_dir/$DEST_ISO_NAME"
eltorito_alt=()
if [ -f "$extract_dir/images/efiboot.img" ]; then
  eltorito_alt=(-eltorito-alt-boot -e images/efiboot.img -no-emul-boot)
fi
( cd "$extract_dir" && xorrisofs -o "$built_iso" \
    -b isolinux/isolinux.bin \
    -J -R -l -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    "${eltorito_alt[@]}" \
    -graft-points \
    -V "$volume_id" \
    . >/dev/null )

remote_rel_path="$(echo "$ISO_STORAGE_PATH" | sed 's#^[A-Za-z]:\\*##')"
smbclient -U "${HYPERV_USER}%${HYPERV_PASSWORD}" "//${HYPERV_HOST}/${ISO_STORAGE_PATH%%:*}\$" \
  -c "mkdir ${remote_rel_path} ; put \"$built_iso\" \"${remote_rel_path}\\${DEST_ISO_NAME}\"" \
  >/dev/null

echo "labape: staged ${DEST_ISO_NAME} (volume ${volume_id}) to ${HYPERV_HOST}:${ISO_STORAGE_PATH}\\${DEST_ISO_NAME}" >&2
