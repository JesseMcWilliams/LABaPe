#!/usr/bin/env bash
# Bootstraps everything Claude_Docs/Reference_Credentials.md §1 and User_Docs/Install-Ansible.md
# §5-6 otherwise have you do by hand: a vault password file, a bootstrap
# SSH keypair, and secrets.vault.yml itself (M1 fields filled in, then
# vault-encrypted). M2+/M3+/M4+ fields are left as the example's
# CHANGE_ME placeholders — fill those in later with
# `ansible-vault edit secrets.vault.yml` when those milestones need them.
#
# Usage: scripts/bootstrap-secrets.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
EXAMPLE_FILE="$ROOT_DIR/secrets.vault.example.yml"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
SSH_KEY_PATH="$HOME/.ssh/labape_bootstrap"

if [ -f "$VAULT_FILE" ]; then
  echo "labape: $VAULT_FILE already exists — remove it first if you really want to re-bootstrap." >&2
  exit 1
fi

sed_escape_repl() {
  printf '%s' "$1" | sed -e 's/[&#\\]/\\&/g'
}

# 1. Vault password file (User_Docs/Install-Ansible.md §5)
if [ -f "$VAULT_PASS_FILE" ]; then
  echo "labape: using existing vault password file $VAULT_PASS_FILE" >&2
else
  echo "labape: generating vault password file $VAULT_PASS_FILE..." >&2
  openssl rand -base64 32 > "$VAULT_PASS_FILE"
  chmod 600 "$VAULT_PASS_FILE"
fi

# 2. Bootstrap SSH keypair (User_Docs/Install-Ansible.md §6)
if [ -f "$SSH_KEY_PATH" ]; then
  echo "labape: using existing SSH keypair $SSH_KEY_PATH" >&2
else
  echo "labape: generating bootstrap SSH keypair $SSH_KEY_PATH..." >&2
  ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C labape-bootstrap
fi

# 3. libvirt_uri — the one M1-required value with no sane default
read -rp "labape: libvirt URI (e.g. qemu+ssh://user@host/system): " LIBVIRT_URI
if [ -z "$LIBVIRT_URI" ]; then
  echo "labape: libvirt_uri is required for M1 — aborting." >&2
  exit 1
fi

# 4. Render secrets.vault.yml from the example, filling in what we now know
echo "labape: writing $VAULT_FILE..." >&2
sed \
  -e "s#^libvirt_uri:.*#libvirt_uri: $(sed_escape_repl "$LIBVIRT_URI")#" \
  -e "s#^ansible_ssh_private_key_path:.*#ansible_ssh_private_key_path: $(sed_escape_repl "$SSH_KEY_PATH")#" \
  "$EXAMPLE_FILE" > "$VAULT_FILE"

# 5. Encrypt in place (Claude_Docs/Reference_Credentials.md §1)
ansible-vault encrypt "$VAULT_FILE" --vault-password-file "$VAULT_PASS_FILE"

echo "labape: done. $VAULT_FILE is vault-encrypted; vault password file: $VAULT_PASS_FILE" >&2
echo "labape: M2+/M3+/M4+ fields (hyperv_*, windows_bootstrap_admin_password, domain_admin_password) are still CHANGE_ME placeholders — fill them in with 'ansible-vault edit $VAULT_FILE --vault-password-file $VAULT_PASS_FILE' when those milestones need them." >&2
