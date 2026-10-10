#!/usr/bin/env bash
# Build a template from ISO with Packer and add it to the template library
# (Claude_Docs/Design_Base-Images.md §3, §8). The other way to get a template
# is scripts/promote-to-template.sh; both produce the same thing, used via
# image_source = "packer_template" + template = "<name>".
#
# Usage: build-template.sh <os-key> <template-name> [--env-file <path>] [--disk-gb N] [--core]
#   e.g. build-template.sh rocky9 rocky9-base-2026.10
#
# <os-key> is an os_iso_paths key in environment.yml (the ISO to install
# from). Templates are never overwritten: pick a new, dated name (§8).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: build-template.sh <os-key> <template-name> [--env-file <path>] [--disk-gb N] [--core]"

OS_KEY="${1:?$USAGE}"
TEMPLATE_NAME="${2:?$USAGE}"
shift 2
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
DISK_GB=""
CORE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; shift ;;
    --disk-gb) DISK_GB="${2:?--disk-gb needs a number}"; shift ;;
    --core) CORE=true ;;   # Windows Server: Server Core instead of the Desktop Experience
    *) echo "labape: unknown option \"$1\" — $USAGE" >&2; exit 1 ;;
  esac
  shift
done
case "$TEMPLATE_NAME" in
  *[!A-Za-z0-9._-]*|"") echo "labape: template name \"$TEMPLATE_NAME\" may only contain letters, digits, '.', '_' and '-'." >&2; exit 1 ;;
esac

# Which Packer build handles which os key.
# Answer-file templates mirror tofu/backends/libvirt/main.tf's catalogs.
ANSWER_TEMPLATE=""
case "$OS_KEY" in
  rocky*) PACKER_DIR="$ROOT_DIR/packer/linux/rocky" ;;
  ubuntu*) PACKER_DIR="$ROOT_DIR/packer/linux/ubuntu" ;;
  debian*) PACKER_DIR="$ROOT_DIR/packer/linux/debian" ;;
  windows_server_*) PACKER_DIR="$ROOT_DIR/packer/windows"; ANSWER_TEMPLATE="iso/answer-files/windows/autounattend-windows-server.xml.tpl" ;;
  windows_11) PACKER_DIR="$ROOT_DIR/packer/windows"; ANSWER_TEMPLATE="iso/answer-files/windows/autounattend-windows-client.xml.tpl"; DISK_GB="${DISK_GB:-80}" ;;
  windows_10) PACKER_DIR="$ROOT_DIR/packer/windows"; ANSWER_TEMPLATE="iso/answer-files/windows/autounattend-windows-10.xml.tpl" ;;
  *) echo "labape: no Packer build for os \"$OS_KEY\" yet (packer/)." >&2; exit 1 ;;
esac

VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
vault() { python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$ROOT_DIR/secrets.vault.yml" "$VAULT_PASS_FILE" "$1"; }
LIBVIRT_URI="$(vault libvirt_uri)"
read -r ISO_PATH TEMPLATE_DIR BUILD_ROOT < <(python3 - "$ENV_FILE" "$OS_KEY" <<'PY'
import sys, yaml
env = yaml.safe_load(open(sys.argv[1]))
iso = (env.get("os_iso_paths") or {}).get(sys.argv[2], "")
vm = env["vm_storage_path"].rstrip("/")
print(iso or "-", env.get("template_storage_path") or f"{vm}/templates", f"{vm}/packer-build")
PY
)
if [ "$ISO_PATH" = "-" ] || [ ! -f "$ISO_PATH" ]; then
  echo "labape: no ISO for \"$OS_KEY\": add it to os_iso_paths in $ENV_FILE (and stage the file)." >&2
  exit 1
fi
target="$TEMPLATE_DIR/$TEMPLATE_NAME.qcow2"
if [ -e "$target" ]; then
  echo "labape: $target already exists — templates are never overwritten; use a new name." >&2
  exit 1
fi

OUTPUT_DIR="$BUILD_ROOT/$TEMPLATE_NAME"
rm -rf "$OUTPUT_DIR"   # Packer refuses an existing output directory
mkdir -p "$BUILD_ROOT"

packer_vars=(
  -var "repo_root=$ROOT_DIR"
  -var "os_key=$OS_KEY"
  -var "iso_path=$ISO_PATH"
  -var "template_name=$TEMPLATE_NAME"
  -var "output_dir=$OUTPUT_DIR"
)
[ -n "$DISK_GB" ] && packer_vars+=(-var "disk_gb=$DISK_GB")
case "$PACKER_DIR" in
  */linux/*)
    key="$(vault ansible_ssh_private_key_path)"; key="${key/#\~/$HOME}"
    packer_vars+=(-var "ssh_private_key_file=$key" -var "ssh_public_key=$(cat "$key.pub")")
    ;;
  */windows)
    # The password reaches Packer through the environment (PKR_VAR_*) and
    # Ansible through a 0600 vars file, never on a command line.
    export PKR_VAR_admin_password
    PKR_VAR_admin_password="$(vault windows_bootstrap_admin_password)"
    ansible_vars_file="$(mktemp)"
    trap 'rm -f "$ansible_vars_file"' EXIT
    chmod 600 "$ansible_vars_file"
    python3 -c 'import json, os, sys; json.dump({"ansible_password": os.environ["PKR_VAR_admin_password"]}, open(sys.argv[1], "w"))' "$ansible_vars_file"
    packer_vars+=(-var "answer_template=$ANSWER_TEMPLATE" -var "ansible_vars_file=$ansible_vars_file")
    if $CORE; then packer_vars+=(-var "server_image_index=1"); fi
    ;;
esac

echo "labape: building $TEMPLATE_NAME from $(basename "$ISO_PATH") with Packer (console log: $OUTPUT_DIR/console.log)..." >&2
packer init "$PACKER_DIR" >/dev/null
packer build -color=false "${packer_vars[@]}" "$PACKER_DIR"

# Same library and permissions as promote-to-template.sh: a libvirt
# storage pool, file mode 0444. The pool may not exist yet if nothing has
# been promoted before.
POOL=labape-templates
virsh_() { virsh --connect "$LIBVIRT_URI" "$@"; }
if ! virsh_ pool-info "$POOL" >/dev/null 2>&1; then
  mkdir -p "$TEMPLATE_DIR"
  virsh_ pool-define-as "$POOL" dir --target "$TEMPLATE_DIR" >/dev/null
  virsh_ pool-autostart "$POOL" >/dev/null
  virsh_ pool-start "$POOL" >/dev/null
fi
# Packer's own output is already a self-contained qcow2 (no backing file),
# so a plain move into the pool directory is enough.
mv "$OUTPUT_DIR/$TEMPLATE_NAME.qcow2" "$target"
chmod 0444 "$target"
virsh_ pool-refresh "$POOL" >/dev/null
rm -rf "$OUTPUT_DIR"
virsh_ vol-info --pool "$POOL" "$TEMPLATE_NAME.qcow2"
echo "labape: template ready: $target" >&2
