#!/usr/bin/env bash
# Tears down one environment instance. Claude_Docs/Design_System-Overview.md §12/§15.
#
# Usage: destroy.sh <backend> <environment-instance-name> <profile> [options]
#
# Options:
#   --test             Throwaway test environment (instance name must start
#                      with "test-"): also deletes its tofu workspace, its
#                      ansible/inventory/<instance>/ directory and its
#                      rendered answer files, and skips the confirmation.
#   --env-file <path>  The environment.yml it was deployed with, if not
#                      tofu/environment.yml.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: destroy.sh <backend> <environment-instance-name> <profile> [--test] [--env-file <path>]"

BACKEND="${1:?$USAGE}"
ENV_INSTANCE="${2:?$USAGE}"
PROFILE="${3:?$USAGE}"
shift 3

TEST_MODE=false
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
while [ $# -gt 0 ]; do
  case "$1" in
    --test) TEST_MODE=true ;;
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; shift ;;
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
    *) echo "labape: --test only tears down instances named \"test-*\" (got \"$ENV_INSTANCE\")." >&2; exit 1 ;;
  esac
fi

BACKEND_DIR="$ROOT_DIR/tofu/backends/$BACKEND"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
PROFILE_FILE="$ROOT_DIR/tofu/environments/${PROFILE}.tfvars"

export TF_VAR_libvirt_uri
TF_VAR_libvirt_uri="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" libvirt_uri)"

SSH_PRIVATE_KEY_PATH="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" ansible_ssh_private_key_path)"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH/#\~/$HOME}"
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key="$(cat "${SSH_PRIVATE_KEY_PATH}.pub")"

# Not used during destroy, but the variable is required.
export TF_VAR_windows_admin_password
TF_VAR_windows_admin_password="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" windows_bootstrap_admin_password 2>/dev/null || echo 'unused-for-destroy')"

python3 "$ROOT_DIR/scripts/lib/render_environment_tfvars.py" "$ENV_FILE" "$BACKEND_DIR"

cd "$BACKEND_DIR"
tofu workspace select "$ENV_INSTANCE"

if $TEST_MODE; then
  tofu destroy -input=false -auto-approve -var-file="$PROFILE_FILE"
  tofu workspace select default
  tofu workspace delete "$ENV_INSTANCE"
  rm -rf "$ROOT_DIR/ansible/inventory/$ENV_INSTANCE" "$ROOT_DIR/tofu/modules/vm/libvirt/.rendered/$ENV_INSTANCE"
  echo "labape: test environment '$ENV_INSTANCE' destroyed; workspace, inventory and rendered answer files removed." >&2
  exit 0
fi

tofu destroy -input=false -var-file="$PROFILE_FILE"

echo "labape: '$ENV_INSTANCE' destroyed. Its workspace/state is still around — remove with" >&2
echo "  tofu workspace select default && tofu workspace delete $ENV_INSTANCE" >&2
echo "once you're sure you're done with it." >&2
