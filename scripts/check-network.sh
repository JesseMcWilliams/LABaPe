#!/usr/bin/env bash
# Pre-flight address availability check (docs/networking.md §3).
# Reads planned static IPs, one per line, from stdin — normally piped
# in from scripts/lib/extract_planned_ips.py by scripts/deploy.sh.
#
# Fails fast with the specific conflicting address(es) rather than
# letting `tofu apply` partially create VMs before hitting a collision.
#
# Usage: extract_planned_ips.py tfplan.json | scripts/check-network.sh
set -euo pipefail

conflicts=0
checked=0

check_one() {
  local ip="$1"
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -w 2 "$ip" >/dev/null 2>&1
  else
    ping -c 2 -W 2 "$ip" >/dev/null 2>&1
  fi
}

while IFS= read -r ip; do
  [ -z "$ip" ] && continue
  checked=$((checked + 1))
  if check_one "$ip"; then
    echo "labape: CONFLICT — $ip already responds. Refusing to proceed." >&2
    conflicts=$((conflicts + 1))
  fi
done

if [ "$checked" -eq 0 ]; then
  echo "labape: no static addresses to check (all hosts are DHCP-mode, or none planned)." >&2
fi

if [ "$conflicts" -gt 0 ]; then
  echo "labape: $conflicts address conflict(s) found out of $checked checked — aborting before any VM is created." >&2
  exit 1
fi

echo "labape: pre-flight address check passed ($checked address(es) checked)." >&2
