#!/usr/bin/env bash
# Shared by create-iso-direct.sh and create-from-template.sh: what to do
# when a VM with this name already exists, before creating it. One copy
# on purpose (safe-undefine.sh's header explains why duplicated teardown
# logic bit this repo once already).
#
# A domain merely *existing* isn't enough to call a create idempotent —
# an interrupted/failed previous create (crash, kill, timeout) can leave
# a defined-but-not-running domain behind. Only a running domain is
# treated as "already done"; anything else is assumed broken and removed
# so it can be recreated, since a VM create is one-shot, not a reconciled
# resource (see the null_resource triggers in main.tf).
#
# libvirt domain names are host-global but tofu workspaces aren't: a
# second environment reusing a host-group name (dc1, winsrv1, ...) would
# otherwise "adopt" another environment's running VM here, and that
# environment's later destroy would delete it. Every VM is created with
# --metadata description=<workspace tag>; only a VM carrying this
# workspace's tag is treated as ours.
#
# Requires safe-undefine.sh to be sourced first.
#
# Usage: handle_existing_domain <libvirt-uri> <vm-name> <workspace-tag>
#   returns normally  -> no such VM (or a leftover was removed): create it
#   exits 0           -> it's ours and running: nothing to do
#   exits 1           -> it belongs to another environment: refuse
handle_existing_domain() {
  local uri="$1" name="$2" tag="$3" state desc

  state="$(virsh --connect "$uri" domstate "$name" 2>/dev/null)" || return 0

  desc="$(virsh --connect "$uri" desc "$name" 2>/dev/null || true)"
  if [ "$desc" != "$tag" ]; then
    echo "labape: a VM named '$name' already exists on $uri but isn't tagged '$tag' (found: '${desc}'). It belongs to another environment or predates tagging; refusing to adopt or replace it. Rename this host group, or tag an untagged VM you own with: virsh desc $name '$tag'" >&2
    exit 1
  fi
  if [ "$state" = "running" ]; then
    echo "labape: VM '$name' already exists and is running on $uri — skipping create (idempotent no-op)." >&2
    exit 0
  fi
  echo "labape: VM '$name' exists but is not running (state: $state) — treating as a leftover from an interrupted create and removing it before recreating." >&2
  virsh --connect "$uri" destroy "$name" >/dev/null 2>&1 || true
  safe_undefine "$uri" "$name"
}
