#!/usr/bin/env bash
# Refresh an existing template into a new one without reinstalling from
# ISO (Claude_Docs/Design_Base-Images.md §6, Option B): clone the template into
# a throwaway test environment, run an update playbook against it, promote
# the result under a new name, destroy the throwaway environment. The old
# template is never touched.
#
# Usage: refresh-template.sh <backend> <template> <new-template> --os <os-key>
#          [--playbook <path>] [--ip-offset N] [--env-file <path>]
#   e.g. refresh-template.sh libvirt rocky9-base-2026.10 rocky9-base-2026.10.1 --os rocky9
#
# --os         the template's os key (an os_iso_paths key, e.g. rocky9,
#              windows_server_2022): tofu needs it for the OS family, and a
#              template name doesn't reliably encode it.
# --playbook   what to apply (default ansible/playbooks/refresh-template.yml:
#              all OS updates, rebooting as needed).
# --ip-offset  static_ip_offset_start for the throwaway VM (default 120);
#              deploy.sh's pre-flight check refuses an address in use.
#
# On failure the throwaway environment is left running for inspection; the
# script prints how to remove it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: refresh-template.sh <backend> <template> <new-template> --os <os-key> [--playbook <path>] [--ip-offset N] [--env-file <path>]"

BACKEND="${1:?$USAGE}"
TEMPLATE="${2:?$USAGE}"
NEW_TEMPLATE="${3:?$USAGE}"
shift 3
OS_KEY=""
PLAYBOOK="$ROOT_DIR/ansible/playbooks/refresh-template.yml"
IP_OFFSET=120
ENV_ARGS=()
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
while [ $# -gt 0 ]; do
  case "$1" in
    --os) OS_KEY="${2:?--os needs an os key}"; shift ;;
    --playbook) PLAYBOOK="$(realpath "${2:?--playbook needs a path}")"; shift ;;
    --ip-offset) IP_OFFSET="${2:?--ip-offset needs a number}"; shift ;;
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; ENV_ARGS=(--env-file "$ENV_FILE"); shift ;;
    *) echo "labape: unknown option \"$1\" — $USAGE" >&2; exit 1 ;;
  esac
  shift
done
[ -n "$OS_KEY" ] || { echo "labape: --os <os-key> is required — $USAGE" >&2; exit 1; }
if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: refresh-template.sh only supports the libvirt backend so far — got \"$BACKEND\"." >&2
  exit 1
fi

TEMPLATE_DIR="$(python3 -c 'import sys, yaml; e = yaml.safe_load(open(sys.argv[1])); print(e.get("template_storage_path") or e["vm_storage_path"].rstrip("/") + "/templates")' "$ENV_FILE")"
src="$TEMPLATE_DIR/$TEMPLATE.qcow2"
[ -f "$src" ] || { echo "labape: template not found: $src" >&2; exit 1; }
[ -e "$TEMPLATE_DIR/$NEW_TEMPLATE.qcow2" ] && { echo "labape: $NEW_TEMPLATE already exists — templates are never overwritten; use a new name." >&2; exit 1; }

# The clone's disk can't be smaller than the template's virtual size.
disk_gb="$(qemu-img info --output=json "$src" | python3 -c 'import json, math, sys; print(math.ceil(json.load(sys.stdin)["virtual-size"] / 2**30))')"

case "$OS_KEY" in
  windows_10|windows_11) role=windows_workstation ;;
  windows_*) role=windows_server ;;
  *) role=linux_server ;;
esac

# Unique per run: libvirt VM names are host-global, and refreshes may run
# side by side. "rf" + 6 hex stays well under Windows' 15-character limit.
id="$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')"
ENV_NAME="test-refresh-$id"
GROUP="rf$id"
VM="${GROUP}1"
TFVARS="$ROOT_DIR/tofu/environments/$ENV_NAME.tfvars"
cat > "$TFVARS" <<EOF
# Throwaway: scripts/refresh-template.sh $TEMPLATE -> $NEW_TEMPLATE
static_ip_offset_start = $IP_OFFSET
host_groups = [
  { name = "$GROUP", count = 1, os = "$OS_KEY", roles = ["$role"], image_source = "packer_template", template = "$TEMPLATE", disk_gb = $disk_gb },
]
EOF

cleanup_hint() {
  echo "labape: refresh failed; the throwaway environment is left for inspection. Remove it with:" >&2
  echo "  scripts/destroy.sh libvirt $ENV_NAME $ENV_NAME --test ${ENV_ARGS[*]} && rm -f $TFVARS" >&2
}
trap cleanup_hint ERR

echo "labape: cloning $TEMPLATE into $ENV_NAME ($VM, ${disk_gb} GB)..." >&2
"$ROOT_DIR/scripts/deploy.sh" libvirt "$ENV_NAME" "$ENV_NAME" --test --no-ansible "${ENV_ARGS[@]}"

echo "labape: applying $(basename "$PLAYBOOK") to $VM..." >&2
(cd "$ROOT_DIR/ansible" && ansible-playbook -i "inventory/$ENV_NAME/generated" "$PLAYBOOK" \
  -e @../secrets.vault.yml --vault-password-file "${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}")

"$ROOT_DIR/scripts/promote-to-template.sh" libvirt "$ENV_NAME" "$VM" "$NEW_TEMPLATE" "${ENV_ARGS[@]}"

trap - ERR
"$ROOT_DIR/scripts/destroy.sh" libvirt "$ENV_NAME" "$ENV_NAME" --test "${ENV_ARGS[@]}"
rm -f "$TFVARS"
echo "labape: refreshed $TEMPLATE -> $NEW_TEMPLATE; the old template is unchanged." >&2
