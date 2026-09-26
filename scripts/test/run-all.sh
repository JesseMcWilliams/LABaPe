#!/usr/bin/env bash
# Claude_Docs/Reference_Validate-Setup.md §6 — runs every check, in order, and prints a
# pass/fail/skip summary. Backend-specific checks (§4) skip themselves
# cleanly when that backend's vault keys aren't present, so this is
# safe to run regardless of which backend(s) you've set up.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_DIR="$ROOT_DIR/scripts/test"

declare -a names=()
declare -a results=()

run() {
  local name="$1"
  shift
  echo ""
  echo "########## $name ##########" >&2
  names+=("$name")
  if "$@"; then
    results+=("PASS")
  else
    results+=("FAIL")
  fi
}

run "Tools & collections"      "$TEST_DIR/test-tools-installed.sh"
run "Ansible playbook syntax"  "$TEST_DIR/test-ansible-playbook-syntax.sh"
run "OpenTofu config validate" "$TEST_DIR/test-opentofu-config.sh"
run "libvirt connectivity"     "$TEST_DIR/test-libvirt-connectivity.sh"
run "Hyper-V/WinRM connectivity" python3 "$TEST_DIR/test-winrm-connectivity.py"
run "Vault & SSH keys"         "$TEST_DIR/test-vault-and-keys.sh"

echo ""
echo "========== Summary =========="
fail=0
for i in "${!names[@]}"; do
  printf '%-32s %s\n' "${names[$i]}" "${results[$i]}"
  [ "${results[$i]}" = "FAIL" ] && fail=1
done

exit $fail
