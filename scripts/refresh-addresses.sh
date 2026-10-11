#!/usr/bin/env bash
# Re-discover DHCP-addressed VMs' leases and regenerate the inventory,
# without touching any VM (Claude_Docs/Planning_Web-Interface-Design.md §20.5).
# For when a lease changed after deploy (a VM rebooted onto a new address,
# or the DHCP server was reset). Static VMs keep their addresses.
#
# Usage: refresh-addresses.sh <backend> <environment-instance-name> [options]
#
# Options:
#   --test               the instance is a test environment (inventory in
#                        ansible/inventory/<instance>/), as with deploy.sh --test
#   --env-file <path>    the environment.yml it was deployed with
#   --timeout <seconds>  how long to look for each DHCP VM (default 600)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: refresh-addresses.sh <backend> <environment-instance-name> [--test] [--env-file <path>] [--timeout <seconds>]"

BACKEND="${1:?$USAGE}"
ENV_INSTANCE="${2:?$USAGE}"
shift 2

TEST_MODE=false
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
TIMEOUT=600
while [ $# -gt 0 ]; do
  case "$1" in
    --test) TEST_MODE=true ;;
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; shift ;;
    --timeout) TIMEOUT="${2:?--timeout needs seconds}"; shift ;;
    *) echo "labape: unknown option \"$1\" — $USAGE" >&2; exit 1 ;;
  esac
  shift
done

if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: DHCP address discovery is libvirt-only so far (Claude_Docs/Reference_Backend-Parity.md) — got \"$BACKEND\"." >&2
  exit 1
fi
if $TEST_MODE; then
  INVENTORY_DIR="$ROOT_DIR/ansible/inventory/$ENV_INSTANCE"
else
  INVENTORY_DIR="$ROOT_DIR/ansible/inventory"
fi

BACKEND_DIR="$ROOT_DIR/tofu/backends/$BACKEND"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
vault() { python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" "$1"; }

export TF_VAR_libvirt_uri
TF_VAR_libvirt_uri="$(vault libvirt_uri)"
SSH_PRIVATE_KEY_PATH="${LABAPE_SSH_PRIVATE_KEY_PATH:-$(vault ansible_ssh_private_key_path)}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH/#\~/$HOME}"
WINDOWS_PASSWORD="$(vault windows_bootstrap_admin_password 2>/dev/null || echo '')"

cd "$BACKEND_DIR"
if [ ! -d .terraform ]; then
  for attempt in 1 2 3; do
    tofu init -input=false >/dev/null && break
    [ "$attempt" -eq 3 ] && { echo "labape: tofu init failed 3 times; giving up." >&2; exit 1; }
    sleep $((attempt * 20))
  done
fi
tofu workspace select "$ENV_INSTANCE" >/dev/null

hosts_json="$(mktemp)"
trap 'rm -f "$hosts_json"' EXIT
tofu output -json hosts > "$hosts_json"
# The state holds the libvirt URI each VM was created with; a profile's
# libvirt_uri (the web UI's) wins over the vault's for the lookup too.
export LIBVIRT_URI="${LIBVIRT_URI:-$TF_VAR_libvirt_uri}"

python3 "$ROOT_DIR/scripts/lib/discover_dhcp_ips.py" "$hosts_json" "$ENV_FILE" --timeout "$TIMEOUT"
python3 "$ROOT_DIR/scripts/generate-inventory.py" "$hosts_json" "$ENV_FILE" "$SSH_PRIVATE_KEY_PATH" "$INVENTORY_DIR" "$WINDOWS_PASSWORD"
chmod 600 "$INVENTORY_DIR/generated" "$INVENTORY_DIR/credentials.generated" 2>/dev/null || true
echo "labape: addresses refreshed; inventory: ${INVENTORY_DIR#"$ROOT_DIR"/}/hosts.generated" >&2
