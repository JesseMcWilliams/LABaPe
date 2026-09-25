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

# Debian-family only (subiquity/cloud-init): confirmed via real testing
# (docs/troubleshooting-log.md) that subiquity's serial-console TUI
# blocks indefinitely on several one-time confirmation screens even
# with `interactive-sections: []` in the autoinstall config — a
# "Serial console started in basic mode" splash, a welcome/language
# picker, and a network-configuration review screen were all hit in
# testing, none suppressed by the autoinstall config itself, each
# needing one Enter keypress. Rather than hardcode a marker string per
# screen (fragile — the exact set of unavoidable pre-autoinstall
# screens isn't documented and may differ across subiquity versions),
# this detects "idle" generically: the console log only grows while
# the TUI is actively animating/redrawing, so if its size hasn't
# changed across two checks, something is very likely waiting on
# input. A spurious Enter during a genuinely-automatic quiet patch
# (e.g. curtin writing to disk) is assumed harmless — those screens
# have no focused actionable control — so this errs toward sending
# too many rather than too few, up to a bounded cap. Requires the
# deploying account to have passwordless sudo for `cp` and `tee`
# specifically (docs/base-images.md §4) — both the console log and the
# console pty device are root-owned (0600) by libvirt/QEMU's own
# defaults, unrelated to this repo's own config.
dismiss_subiquity_prompts() {
  local deadline=$((SECONDS + install_timeout_seconds))
  local pty="" last_size=-1 idle_polls=0 sent_count=0
  # max_sends started at 10 and was confirmed too low via real testing
  # (docs/troubleshooting-log.md): idle gaps of 8s+ occur routinely
  # during ordinary boot (disk-allocation progress ticks, kernel/udev
  # messages) well before subiquity's TUI ever appears, and each one
  # consumes a "send" — the budget was fully exhausted on boot noise
  # before reaching the real interactive screens, silently disabling
  # this function for the rest of the install. A stray Enter during
  # boot is harmless (nothing focused/actionable to accidentally
  # trigger), so raising the cap generously is a safe fix; the real
  # constraint is still install_timeout_seconds overall.
  local max_sends=50 idle_polls_required=2 poll_interval=4

  while [ "$SECONDS" -lt "$deadline" ] && [ "$sent_count" -lt "$max_sends" ]; do
    sleep "$poll_interval"
    if [ -z "$pty" ]; then
      # `|| true` is load-bearing, not defensive: with `pipefail` (set
      # at the top of this script), grep finding no match yet (routine
      # — the console element doesn't exist until the domain starts)
      # makes the whole pipeline "fail", which set -e then treats as
      # this entire script failing — silently orphaning the backgrounded
      # virt-install and leaving OpenTofu's local-exec hung reading its
      # now-parentless-but-still-open output pipe. Confirmed by hitting
      # exactly that hang in real testing (docs/troubleshooting-log.md).
      pty="$(virsh --connect "$LIBVIRT_URI" dumpxml "$VM_NAME" 2>/dev/null \
        | grep -oP "(?<=<console type='pty' tty=')[^']+" | head -1 || true)"
    fi
    [ -z "$pty" ] && continue

    cur_size="$(sudo -n /usr/bin/cp "$console_log" /dev/stdout 2>/dev/null | wc -c || true)"
    if [ -n "$cur_size" ] && [ "$cur_size" = "$last_size" ]; then
      idle_polls=$((idle_polls + 1))
      if [ "$idle_polls" -ge "$idle_polls_required" ]; then
        ( printf '\r'; sleep 2 ) | sudo -n tee "$pty" >/dev/null
        sent_count=$((sent_count + 1))
        idle_polls=0
      fi
    else
      idle_polls=0
    fi
    last_size="$cur_size"
  done
}

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

debian)
  : "${OS_VARIANT:?}"
  : "${META_DATA_PATH:?}"
  install_timeout_seconds=1800

  # Debian-family autoinstall (subiquity/cloud-init) has no kernel-
  # argument equivalent to kickstart's inst.ks=file:/<injected file> —
  # its NoCloud datasource instead expects a labeled CIDATA volume
  # containing exact-named user-data/meta-data files at its root, so
  # this borrows the windows) branch's second-CD-ROM pattern (build a
  # small ISO, attach it alongside the real install media) rather than
  # the linux) branch's --initrd-inject. Still boots via --location
  # (extracting the installer kernel/initrd) the same way linux) does,
  # and keeps that branch's serial-console observability (--graphics
  # none + a logged console) rather than windows)'s VNC, since
  # subiquity, like Anaconda, is a text-mode installer.
  #
  # UNVERIFIED as of this writing: the exact ds= seed-URI form. If the
  # install falls through to subiquity's interactive "no autoinstall
  # config found" prompt (visible in the console log), try
  # "ds=nocloud-net;s=file:///cdrom/" instead, or dropping ds= entirely
  # (some cloud-init versions auto-detect a CIDATA-labeled attached
  # volume by label alone) — don't assume this form works untested.
  #
  # kernel=/initrd= override on --location is REQUIRED, not optional,
  # for Ubuntu 24.04 specifically (confirmed via real testing,
  # docs/troubleshooting-log.md): osinfo-db's ubuntu24.04 entry marks
  # its live/casper media installer-script="false" (its own comment:
  # subiquity's autoinstall style "isn't supported yet in libosinfo and
  # associated tools"), so virt-install's automatic kernel/initrd
  # detection never consults that entry's declared
  # casper/vmlinuz+casper/initrd paths at all — it falls back to a
  # short hardcoded list of legacy locations (e.g. /install/vmlinuz),
  # none of which exist on this ISO, and fails outright with "Couldn't
  # find kernel for install tree." Passing the paths explicitly (which
  # virt-install supports precisely for this "OS doesn't have tree
  # metadata" case) bypasses that detection entirely.
  seed_iso="${VM_STORAGE_PATH}/${VM_NAME}-seed.iso"
  seed_stage_dir="$(mktemp -d)"
  trap 'rm -rf "$seed_stage_dir"' EXIT
  cp "$ANSWER_FILE_PATH" "$seed_stage_dir/user-data"
  cp "$META_DATA_PATH" "$seed_stage_dir/meta-data"
  xorrisofs -o "$seed_iso" -V CIDATA -J -r "$seed_stage_dir" >/dev/null
  sync "$seed_iso"

  # model=virtio (unlike Windows' sata/e1000e) — Ubuntu has in-box
  # virtio drivers, so there's no equivalent reason to avoid it here.
  #
  # virt-install runs backgrounded (not `--wait -1` in the foreground)
  # so dismiss_subiquity_prompts can run concurrently and unblock it —
  # see that function's own comment for why this is needed at all.
  timeout "$install_timeout_seconds" virt-install \
    --connect "$LIBVIRT_URI" \
    --name "$VM_NAME" \
    --vcpus "$CPU_COUNT" \
    --memory "$MEMORY_MB" \
    --disk "path=${disk_path},size=${DISK_GB},format=qcow2" \
    --disk "path=${seed_iso},device=cdrom" \
    --location "${ISO_HOST_PATH},kernel=casper/vmlinuz,initrd=casper/initrd" \
    --extra-args "autoinstall ds=nocloud;s=file:///cdrom/ console=ttyS0" \
    --network "bridge=${BRIDGE_DEVICE},model=virtio" \
    --os-variant "$OS_VARIANT" \
    --graphics none \
    --console "pty,target_type=serial,log.file=${console_log},log.append=off" \
    --noautoconsole \
    --wait -1 &
  vi_pid=$!

  dismiss_subiquity_prompts

  if ! wait "$vi_pid"; then
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
  # Named autounattend.xml, not unattend.xml — that alternate name was
  # tried and hit the identical failure (README's Known Gaps, round 7),
  # as expected since unattend.xml isn't the documented name for this
  # windowsPE-pass/removable-media search position anyway.
  # Root cause, finally confirmed directly rather than inferred (README's
  # Known Gaps, round 10): pulled Windows Setup's own
  # X:\Windows\setupact.log via a WinPE Shift+F10 shell during a live
  # failure. It isn't a detection problem at all — Setup finds the file
  # every time — it's *deserialization*: "UnattendSearchExplicitPath:
  # Found unattend file at [...] but unable to deserialize it; status =
  # 0x1, hrResult = 0x800705b9", a generic XML-parse failure. A quick
  # `sync` before virt-install (ruling out an un-flushed write racing
  # the guest's very early read) made no difference — same error,
  # byte-identical. What did line up: this repo's answer-file .tpl has
  # several multi-line XML comments (`<!-- ... -->` spanning several
  # physical lines) documenting the file for developers, and a Windows
  # 10-era forum thread found during round 1 of this investigation
  # (tenforums.com) had already identified exactly this — Windows
  # Setup's XML deserializer chokes on multi-line comments despite them
  # being perfectly valid XML — as a real, reproducible cause of this
  # same "unable to deserialize" failure. Strip comments from the copy
  # that actually reaches Setup; the .tpl itself keeps its full
  # documentation for anyone reading the source.
  answer_iso="${VM_STORAGE_PATH}/${VM_NAME}-autounattend.iso"
  answer_stage_dir="$(mktemp -d)"
  trap 'rm -rf "$answer_stage_dir"' EXIT
  perl -0777 -pe 's/<!--.*?-->//gs' "$ANSWER_FILE_PATH" > "$answer_stage_dir/autounattend.xml"
  xorrisofs -o "$answer_iso" -V AUTOUNATTEND -J -r "$answer_stage_dir" >/dev/null
  sync "$answer_iso"

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
  echo "labape: unknown OS_FAMILY \"$OS_FAMILY\" — expected \"linux\", \"debian\", or \"windows\"." >&2
  exit 1
  ;;
esac

echo "labape: '$VM_NAME' install complete (virt-install returned after the post-install reboot)." >&2
