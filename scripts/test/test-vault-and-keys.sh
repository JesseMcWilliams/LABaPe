#!/usr/bin/env bash
# Claude_Docs/Reference_Validate-Setup.md §5 — the vault decrypts, and the bootstrap SSH
# keypair it points at is actually a matching pair.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"

if [ ! -f "$VAULT_FILE" ]; then
  echo "FAIL: $VAULT_FILE doesn't exist — cp secrets.vault.example.yml secrets.vault.yml, fill it in, then ansible-vault encrypt it." >&2
  exit 1
fi
if [ ! -f "$VAULT_PASS_FILE" ]; then
  echo "FAIL: vault password file $VAULT_PASS_FILE not found (override with LABAPE_VAULT_PASS_FILE)." >&2
  exit 1
fi

if ! ansible-vault view "$VAULT_FILE" --vault-password-file "$VAULT_PASS_FILE" >/tmp/labape-vault-view.$$ 2>/tmp/labape-vault-view-err.$$; then
  echo "FAIL: ansible-vault couldn't decrypt $VAULT_FILE with $VAULT_PASS_FILE." >&2
  cat /tmp/labape-vault-view-err.$$ >&2
  rm -f /tmp/labape-vault-view.$$ /tmp/labape-vault-view-err.$$
  exit 1
fi
echo "OK: $VAULT_FILE decrypts with $VAULT_PASS_FILE." >&2

key_path="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" ansible_ssh_private_key_path 2>/dev/null)"
rm -f /tmp/labape-vault-view.$$ /tmp/labape-vault-view-err.$$

if [ -z "$key_path" ]; then
  echo "FAIL: vault has no ansible_ssh_private_key_path key (Claude_Docs/Reference_Credentials.md §1)." >&2
  exit 1
fi

key_path="${key_path/#\~/$HOME}"
fail=0

if [ ! -f "$key_path" ]; then
  echo "FAIL: private key $key_path not found." >&2
  fail=1
fi
if [ ! -f "${key_path}.pub" ]; then
  echo "FAIL: ${key_path}.pub not found — User_Docs/Install-Ansible.md §6 (ssh-keygen -f $key_path)." >&2
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  priv_fp="$(ssh-keygen -lf "$key_path" 2>/dev/null | awk '{print $2}')"
  pub_fp="$(ssh-keygen -lf "${key_path}.pub" 2>/dev/null | awk '{print $2}')"
  if [ -n "$priv_fp" ] && [ "$priv_fp" = "$pub_fp" ]; then
    echo "OK: $key_path and ${key_path}.pub are a matching pair (fingerprint $priv_fp)." >&2
  else
    echo "FAIL: $key_path and ${key_path}.pub don't match (fingerprints: '$priv_fp' vs '$pub_fp') — .pub may have been regenerated separately." >&2
    fail=1
  fi
fi

exit $fail
