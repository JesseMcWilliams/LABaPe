#!/usr/bin/env bash
# Claude_Docs/Reference_Validate-Setup.md §4 — confirms virsh/virt-install are present
# locally and that the vault's libvirt_uri actually connects. Skips
# (not fails) if the vault has no libvirt_uri configured at all, so
# run-all.sh can call this unconditionally.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VAULT_FILE="$ROOT_DIR/secrets.vault.yml"
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"

if [ ! -f "$VAULT_FILE" ] || [ ! -f "$VAULT_PASS_FILE" ]; then
  echo "labape: libvirt — $VAULT_FILE or $VAULT_PASS_FILE not set up yet, skipping (Claude_Docs/Reference_Credentials.md §1)." >&2
  exit 0
fi

for cmd in virsh virt-install; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "FAIL: $cmd not found — User_Docs/Install-OpenTofu.md §4 (sudo apt-get install -y libvirt-clients virtinst)." >&2
    exit 1
  fi
done
echo "OK: virsh and virt-install are installed." >&2

libvirt_uri="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$VAULT_FILE" "$VAULT_PASS_FILE" libvirt_uri 2>/dev/null)"
if [ -z "$libvirt_uri" ]; then
  echo "labape: libvirt — no libvirt_uri in the vault, skipping (this box may be Hyper-V-only)." >&2
  exit 0
fi

echo "== virsh --connect $libvirt_uri list --all ==" >&2
if virsh --connect "$libvirt_uri" list --all; then
  echo "OK: connected to $libvirt_uri." >&2
  exit 0
else
  echo "FAIL: could not connect to $libvirt_uri — User_Docs/Install-OpenTofu.md §6 (check ssh-copy-id was run, libvirtd is running on the host, and the URI itself is correct)." >&2
  exit 1
fi
