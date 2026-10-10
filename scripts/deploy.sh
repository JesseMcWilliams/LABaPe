#!/usr/bin/env bash
# vault decrypt -> check-network -> tofu apply -> generate inventory/hosts
# -> ansible-playbook (Claude_Docs/Design_System-Overview.md §12/§15, Claude_Docs/Reference_Credentials.md §6).
#
# Usage: deploy.sh <backend> <environment-instance-name> <profile> [options]
#   e.g. deploy.sh libvirt lab1 small
#        deploy.sh libvirt test-win11 test-win11 --test --no-ansible
#
# Options (all optional; with none, behavior is the long-standing default):
#   --test                    Throwaway test environment: the instance name
#                             must start with "test-", and the inventory goes
#                             to ansible/inventory/<instance>/ instead of
#                             ansible/inventory/ so a long-lived environment's
#                             inventory isn't overwritten. Tear down with
#                             destroy.sh ... --test.
#   --env-file <path>         Use this environment.yml instead of
#                             tofu/environment.yml (e.g. a second domain name).
#   --directory-manifest <p>  Use this directory manifest instead of
#                             ansible/directory-manifest.yml (DNs are
#                             domain-specific, so a second domain needs its own).
#   --no-ansible              Stop after tofu apply + inventory generation.
#
# Host-group names must be unique across every environment on one libvirt
# host: VM names are host-global, and create-iso-direct.sh refuses a VM
# that's tagged for another workspace (Claude_Docs/Testing_Troubleshooting-Log.md).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: deploy.sh <backend> <environment-instance-name> <profile> [--test] [--env-file <path>] [--directory-manifest <path>] [--no-ansible]"

BACKEND="${1:?$USAGE}"
ENV_INSTANCE="${2:?$USAGE}"
PROFILE="${3:?$USAGE}"
shift 3

TEST_MODE=false
RUN_ANSIBLE=true
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
DIRECTORY_MANIFEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test) TEST_MODE=true ;;
    --no-ansible) RUN_ANSIBLE=false ;;
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; shift ;;
    --directory-manifest) DIRECTORY_MANIFEST="$(realpath "${2:?--directory-manifest needs a path}")"; shift ;;
    *) echo "labape: unknown option \"$1\" — $USAGE" >&2; exit 1 ;;
  esac
  shift
done

if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: only the libvirt backend is implemented as of M1 (Claude_Docs/Design_System-Overview.md §18) — got \"$BACKEND\"." >&2
  exit 1
fi

if $TEST_MODE; then
  case "$ENV_INSTANCE" in
    test-*) ;;
    *) echo "labape: --test needs an instance name starting with \"test-\" (got \"$ENV_INSTANCE\"), so a test can't be pointed at a long-lived environment by mistake." >&2; exit 1 ;;
  esac
  INVENTORY_DIR="$ROOT_DIR/ansible/inventory/$ENV_INSTANCE"
else
  INVENTORY_DIR="$ROOT_DIR/ansible/inventory"
fi

BACKEND_DIR="$ROOT_DIR/tofu/backends/$BACKEND"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
PROFILE_FILE="$ROOT_DIR/tofu/environments/${PROFILE}.tfvars"
MANIFEST_FILE="$ROOT_DIR/ansible/software-manifest.yml"

for f in "$VAULT_FILE" "$ENV_FILE" "$PROFILE_FILE" "$MANIFEST_FILE" ${DIRECTORY_MANIFEST:+"$DIRECTORY_MANIFEST"}; do
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

# LABAPE_SSH_PRIVATE_KEY_PATH overrides the vault's path (the web UI's job runner
# keeps its own copy of the key; the vault's path belongs to the CLI host).
SSH_PRIVATE_KEY_PATH="${LABAPE_SSH_PRIVATE_KEY_PATH:-$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" ansible_ssh_private_key_path)}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH/#\~/$HOME}"
if [ ! -f "${SSH_PRIVATE_KEY_PATH}.pub" ]; then
  echo "labape: ${SSH_PRIVATE_KEY_PATH}.pub not found — ansible_ssh_private_key_path in the vault must point at a keypair with a matching .pub file." >&2
  exit 1
fi
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key="$(cat "${SSH_PRIVATE_KEY_PATH}.pub")"

export TF_VAR_windows_admin_password
TF_VAR_windows_admin_password="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" windows_bootstrap_admin_password 2>/dev/null || echo '')"
if [ "$TF_VAR_windows_admin_password" = "CHANGE_ME" ] && grep -q '"windows' "$PROFILE_FILE" 2>/dev/null; then
  echo "labape: $PROFILE_FILE deploys a Windows host but windows_bootstrap_admin_password in the vault is still the CHANGE_ME placeholder — set a real one first: ansible-vault edit $VAULT_FILE --vault-password-file $VAULT_PASS_FILE" >&2
  exit 1
fi

echo "labape: rendering $(basename "$ENV_FILE") -> environment.auto.tfvars.json..." >&2
python3 "$ROOT_DIR/scripts/lib/render_environment_tfvars.py" "$ENV_FILE" "$BACKEND_DIR"

cd "$BACKEND_DIR"
# `tofu init` contacts registry.opentofu.org on every run even when the
# providers are already installed; a transient registry timeout once
# failed two unattended runs at the same moment. Retry before giving up.
for attempt in 1 2 3; do
  tofu init -input=false && break
  [ "$attempt" -eq 3 ] && { echo "labape: tofu init failed 3 times; giving up." >&2; exit 1; }
  echo "labape: tofu init failed (attempt $attempt); retrying in $((attempt * 20))s..." >&2
  sleep $((attempt * 20))
done
tofu workspace select "$ENV_INSTANCE" 2>/dev/null || tofu workspace new "$ENV_INSTANCE"

echo "labape: planning..." >&2
tofu plan -input=false -var-file="$PROFILE_FILE" -out=tfplan.bin
tofu show -json tfplan.bin > tfplan.json

echo "labape: pre-flight network check (Claude_Docs/Reference_Networking.md §3)..." >&2
python3 "$ROOT_DIR/scripts/lib/extract_planned_ips.py" tfplan.json | "$ROOT_DIR/scripts/check-network.sh"

echo "labape: applying..." >&2
# -parallelism=1: concurrent virt-install invocations race to define the
# same libvirt "boot-scratch" scratch-storage pool (a virt-install-
# internal TOCTOU bug, not something this repo's script can fix from the
# outside) and one of them fails outright. Serializing VM creation avoids
# it — slower for multi-host environments, but M1's smoke test surfaced
# this as a 100%-reproducible failure with even two concurrent hosts.
tofu apply -input=false -parallelism=1 tfplan.bin
rm -f tfplan.bin tfplan.json

echo "labape: generating inventory in ${INVENTORY_DIR#"$ROOT_DIR"/}..." >&2
tofu output -json hosts > hosts.json
python3 "$ROOT_DIR/scripts/generate-inventory.py" hosts.json "$ENV_FILE" "$SSH_PRIVATE_KEY_PATH" "$INVENTORY_DIR" "$TF_VAR_windows_admin_password"
chmod 600 "$INVENTORY_DIR/generated" "$INVENTORY_DIR/credentials.generated" 2>/dev/null || true
rm -f hosts.json

if ! $RUN_ANSIBLE; then
  echo "labape: --no-ansible: stopping after provisioning. Inventory: ${INVENTORY_DIR#"$ROOT_DIR"/}/generated" >&2
  exit 0
fi

# site.yml loads ansible/directory-manifest.yml itself; a play variable is
# overridden by extra vars, so a different manifest goes in as one.
extra_vars_file=""
if [ -n "$DIRECTORY_MANIFEST" ]; then
  extra_vars_file="$(mktemp)"
  trap 'rm -f "$extra_vars_file"' EXIT
  python3 -c 'import json, sys, yaml; json.dump({"directory_manifest": yaml.safe_load(open(sys.argv[1])) or {}}, open(sys.argv[2], "w"))' \
    "$DIRECTORY_MANIFEST" "$extra_vars_file"
fi

echo "labape: running ansible-playbook..." >&2
cd "$ROOT_DIR/ansible"
ansible-playbook -i "$INVENTORY_DIR/generated" playbooks/site.yml \
  -e @software-manifest.yml \
  ${extra_vars_file:+-e "@$extra_vars_file"} \
  -e @../secrets.vault.yml --vault-password-file "$VAULT_PASS_FILE"

echo "labape: done. Paste ${INVENTORY_DIR#"$ROOT_DIR"/}/hosts.generated into your hosts file (Claude_Docs/Reference_Networking.md §4)." >&2
echo "labape: tester access credentials for this environment: ${INVENTORY_DIR#"$ROOT_DIR"/}/credentials.generated (Claude_Docs/Reference_Credentials.md §8)." >&2
