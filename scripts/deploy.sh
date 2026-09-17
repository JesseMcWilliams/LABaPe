#!/usr/bin/env bash
# vault decrypt -> check-network -> tofu apply -> generate inventory/hosts
# -> ansible-playbook (DESIGN.md §12/§15, docs/credentials.md §6).
#
# Usage: deploy.sh <backend> <environment-instance-name> <profile>
#   e.g. deploy.sh libvirt lab1 small
#
# Only the libvirt backend + small profile (Linux-only subset) are
# implemented as of M1 (DESIGN.md §18).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND="${1:?usage: deploy.sh <backend> <environment-instance-name> <profile>}"
ENV_INSTANCE="${2:?usage: deploy.sh <backend> <environment-instance-name> <profile>}"
PROFILE="${3:?usage: deploy.sh <backend> <environment-instance-name> <profile>}"

if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: only the libvirt backend is implemented as of M1 (DESIGN.md §18) — got \"$BACKEND\"." >&2
  exit 1
fi

BACKEND_DIR="$ROOT_DIR/tofu/backends/$BACKEND"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
PROFILE_FILE="$ROOT_DIR/tofu/environments/${PROFILE}.tfvars"
MANIFEST_FILE="$ROOT_DIR/ansible/software-manifest.yml"

for f in "$VAULT_FILE" "$ENV_FILE" "$PROFILE_FILE" "$MANIFEST_FILE"; do
  if [ ! -f "$f" ]; then
    echo "labape: missing $f — copy the matching *.example file first." >&2
    exit 1
  fi
done
if [ ! -f "$VAULT_PASS_FILE" ]; then
  echo "labape: missing vault password file $VAULT_PASS_FILE (override with LABAPE_VAULT_PASS_FILE)." >&2
  exit 1
fi

echo "labape: reading vault..." >&2
export TF_VAR_libvirt_uri
TF_VAR_libvirt_uri="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" libvirt_uri)"

SSH_PRIVATE_KEY_PATH="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" ansible_ssh_private_key_path)"
if [ ! -f "${SSH_PRIVATE_KEY_PATH}.pub" ]; then
  echo "labape: ${SSH_PRIVATE_KEY_PATH}.pub not found — ansible_ssh_private_key_path in the vault must point at a keypair with a matching .pub file." >&2
  exit 1
fi
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key="$(cat "${SSH_PRIVATE_KEY_PATH}.pub")"

echo "labape: rendering environment.yml -> environment.auto.tfvars.json..." >&2
python3 "$ROOT_DIR/scripts/lib/render_environment_tfvars.py" "$ENV_FILE" "$BACKEND_DIR"

cd "$BACKEND_DIR"
tofu init -input=false
tofu workspace select "$ENV_INSTANCE" 2>/dev/null || tofu workspace new "$ENV_INSTANCE"

echo "labape: planning..." >&2
tofu plan -input=false -var-file="$PROFILE_FILE" -out=tfplan.bin
tofu show -json tfplan.bin > tfplan.json

echo "labape: pre-flight network check (docs/networking.md §3)..." >&2
python3 "$ROOT_DIR/scripts/lib/extract_planned_ips.py" tfplan.json | "$ROOT_DIR/scripts/check-network.sh"

echo "labape: applying..." >&2
tofu apply -input=false tfplan.bin
rm -f tfplan.bin tfplan.json

echo "labape: generating inventory..." >&2
tofu output -json hosts > hosts.json
python3 "$ROOT_DIR/scripts/generate-inventory.py" hosts.json "$ENV_FILE" "$SSH_PRIVATE_KEY_PATH" "$ROOT_DIR/ansible/inventory"
rm -f hosts.json

echo "labape: running ansible-playbook..." >&2
cd "$ROOT_DIR/ansible"
ansible-playbook playbooks/site.yml \
  -e @software-manifest.yml \
  -e @../secrets.vault.yml --vault-password-file "$VAULT_PASS_FILE"

echo "labape: done. Paste ansible/inventory/hosts.generated into your hosts file (docs/networking.md §4)." >&2
