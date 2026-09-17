#!/usr/bin/env bash
# Tears down one environment instance. DESIGN.md §12/§15.
#
# Usage: destroy.sh <backend> <environment-instance-name> <profile>
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND="${1:?usage: destroy.sh <backend> <environment-instance-name> <profile>}"
ENV_INSTANCE="${2:?usage: destroy.sh <backend> <environment-instance-name> <profile>}"
PROFILE="${3:?usage: destroy.sh <backend> <environment-instance-name> <profile>}"

if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: only the libvirt backend is implemented as of M1 (DESIGN.md §18) — got \"$BACKEND\"." >&2
  exit 1
fi

BACKEND_DIR="$ROOT_DIR/tofu/backends/$BACKEND"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
PROFILE_FILE="$ROOT_DIR/tofu/environments/${PROFILE}.tfvars"

export TF_VAR_libvirt_uri
TF_VAR_libvirt_uri="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" libvirt_uri)"

SSH_PRIVATE_KEY_PATH="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" ansible_ssh_private_key_path)"
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key="$(cat "${SSH_PRIVATE_KEY_PATH}.pub")"

python3 "$ROOT_DIR/scripts/lib/render_environment_tfvars.py" "$ENV_FILE" "$BACKEND_DIR"

cd "$BACKEND_DIR"
tofu workspace select "$ENV_INSTANCE"
tofu destroy -input=false -var-file="$PROFILE_FILE"

echo "labape: '$ENV_INSTANCE' destroyed. Its workspace/state is still around — remove with" >&2
echo "  tofu workspace select default && tofu workspace delete $ENV_INSTANCE" >&2
echo "once you're sure you're done with it." >&2
