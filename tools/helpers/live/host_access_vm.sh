#!/usr/bin/env bash
# host_access_vm.sh VM_ADDRESS HOST_ID LABAPE_URL: test "Set up access" against a LABaPe VM
# (design §28.4). The VM's admin user (labape) is key-only, so this gives it a random
# password for the test, pipes that straight into host_access.py setup (never printed or
# stored), then locks the password again. LABaPe VMs allow only key sign-in, so password
# sign-in is turned on for the test (an sshd drop-in) and off again afterwards. Runs on
# the lab host, as the service user.
set -euo pipefail
VM="$1" HOST_ID="$2" URL="$3"
R="${LABAPE_LAB_REPO:-$HOME/LABaPe-web}"
D="/run/user/$(id -u)/labape-cli"
python3 "$HOME/LABaPe/scripts/lib/store_get.py" ssh --out-dir "$D/ssh" >/dev/null
K=(ssh -i "$D/ssh/labape_bootstrap" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "labape@$VM")
PW="$(openssl rand -base64 32 | tr -d '\n/+=' | cut -c1-28)"
printf 'labape:%s\n' "$PW" | "${K[@]}" sudo chpasswd
printf 'PasswordAuthentication yes\nKbdInteractiveAuthentication no\n' \
  | "${K[@]}" "sudo tee /etc/ssh/sshd_config.d/00-labape-test.conf >/dev/null && sudo systemctl reload ssh"
printf '%s' "$PW" | docker run --rm -i --network host -v "$HOME/labape-test-tools:/tools:ro" \
  -v "$R/tools/authentik-test/.env:/authentik.env:ro" -e LABAPE="$URL" -e AK_ENV=/authentik.env \
  --entrypoint python3 labape:local /tools/host_access.py setup "$HOST_ID" "$VM" labape \
  2>&1 | grep -v -E "InsecureRequestWarning|warnings.warn"
unset PW
"${K[@]}" "sudo rm -f /etc/ssh/sshd_config.d/00-labape-test.conf && sudo systemctl reload ssh"
"${K[@]}" sudo passwd -l labape >/dev/null && echo "host_access_vm: the test password on labape@$VM is locked again"
