#!/usr/bin/env bash
# Turn one running, already-configured VM into a template in the library
# (Claude_Docs/Design_Base-Images.md §5). Manual by design (§17.2): you
# decide when a VM is worth keeping.
#
# Usage: promote-to-template.sh <backend> <environment-instance> <vm-name> <template-name>
#   e.g. promote-to-template.sh libvirt test-tpl rocky1 rocky9-base-2026.10
#
# Steps: finalize the VM over its existing Ansible connection
# (cloud-init clean / sysprep — ansible/roles/template_finalize), wait for
# it to shut itself down, then copy its disk into the template storage
# pool as <template-name>.qcow2 (a full, flattened copy made by libvirt).
# The source VM is left shut off and generalized; tear its environment
# down afterwards (destroy.sh ... --test). Templates are never
# overwritten: pick a new, dated name for each build (§8).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USAGE="usage: promote-to-template.sh <backend> <environment-instance> <vm-name> <template-name> [--env-file <path>]"

BACKEND="${1:?$USAGE}"
ENV_INSTANCE="${2:?$USAGE}"
VM_NAME="${3:?$USAGE}"
TEMPLATE_NAME="${4:?$USAGE}"
shift 4
ENV_FILE="$ROOT_DIR/tofu/environment.yml"
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$(realpath "${2:?--env-file needs a path}")"; shift ;;
    *) echo "labape: unknown option \"$1\" — $USAGE" >&2; exit 1 ;;
  esac
  shift
done

if [ "$BACKEND" != "libvirt" ]; then
  echo "labape: promote-to-template.sh only supports the libvirt backend so far — got \"$BACKEND\"." >&2
  exit 1
fi
case "$TEMPLATE_NAME" in
  *[!A-Za-z0-9._-]*|"") echo "labape: template name \"$TEMPLATE_NAME\" may only contain letters, digits, '.', '_' and '-'." >&2; exit 1 ;;
esac

POOL=labape-templates
VAULT_PASS_FILE="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
LIBVIRT_URI="$(python3 "$ROOT_DIR/scripts/lib/vault_get.py" "$ROOT_DIR/secrets.vault.yml" "$VAULT_PASS_FILE" libvirt_uri)"
TEMPLATE_DIR="$(python3 -c 'import sys, yaml; e = yaml.safe_load(open(sys.argv[1])); print(e.get("template_storage_path") or e["vm_storage_path"].rstrip("/") + "/templates")' "$ENV_FILE")"
virsh_() { virsh --connect "$LIBVIRT_URI" "$@"; }

# Inventory: test environments keep theirs in ansible/inventory/<instance>/.
if [ -f "$ROOT_DIR/ansible/inventory/$ENV_INSTANCE/generated" ]; then
  INVENTORY="$ROOT_DIR/ansible/inventory/$ENV_INSTANCE/generated"
else
  INVENTORY="$ROOT_DIR/ansible/inventory/generated"
fi
if ! grep -q "^    $VM_NAME:" "$INVENTORY"; then
  echo "labape: $VM_NAME isn't in $INVENTORY — is <environment-instance> right?" >&2
  exit 1
fi

if [ "$(virsh_ desc "$VM_NAME" 2>/dev/null || true)" != "labape-workspace=$ENV_INSTANCE" ]; then
  echo "labape: VM '$VM_NAME' isn't tagged as belonging to '$ENV_INSTANCE'; refusing." >&2
  exit 1
fi

target="$TEMPLATE_DIR/$TEMPLATE_NAME.qcow2"
if [ -e "$target" ]; then
  echo "labape: $target already exists — templates are never overwritten; use a new name." >&2
  exit 1
fi

# The template library is a libvirt storage pool, so the copy is done by
# libvirtd (which can read the VM's libvirt-owned disk) rather than this
# user.
if ! virsh_ pool-info "$POOL" >/dev/null 2>&1; then
  mkdir -p "$TEMPLATE_DIR"
  virsh_ pool-define-as "$POOL" dir --target "$TEMPLATE_DIR" >/dev/null
  virsh_ pool-autostart "$POOL" >/dev/null
fi
pool_target="$(virsh_ pool-dumpxml "$POOL" | sed -n 's:.*<path>\(.*\)</path>.*:\1:p' | head -1)"
if [ "$pool_target" != "$TEMPLATE_DIR" ]; then
  echo "labape: storage pool $POOL points at $pool_target, but template_storage_path is $TEMPLATE_DIR." >&2
  exit 1
fi
if [ "$(virsh_ pool-info "$POOL" | awk '/^State:/{print $2}')" != "running" ]; then
  virsh_ pool-start "$POOL" >/dev/null
fi

src_disk="$(virsh_ domblklist "$VM_NAME" --details | awk '$2=="disk"{print $4; exit}')"
src_pool="$(virsh_ vol-pool "$src_disk")"
src_vol="$(virsh_ vol-name "$src_disk")"

echo "labape: finalizing $VM_NAME (generalize, then it shuts itself down)..." >&2
(cd "$ROOT_DIR/ansible" && ansible-playbook -i "$INVENTORY" playbooks/finalize-template.yml --limit "$VM_NAME")

echo "labape: waiting for $VM_NAME to shut down..." >&2
deadline=$((SECONDS + 1800))
until [ "$(virsh_ domstate "$VM_NAME")" = "shut off" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "labape: $VM_NAME didn't shut down within 30 minutes — check its console (sysprep errors land in C:\\Windows\\System32\\Sysprep\\Panther\\setuperr.log)." >&2
    exit 1
  fi
  sleep 10
done

echo "labape: copying $src_vol into $POOL as $TEMPLATE_NAME.qcow2..." >&2
vol_xml="$(mktemp)"
trap 'rm -f "$vol_xml"' EXIT
cat > "$vol_xml" <<EOF
<volume>
  <name>$TEMPLATE_NAME.qcow2</name>
  <target>
    <format type='qcow2'/>
    <permissions><mode>0444</mode></permissions>
  </target>
</volume>
EOF
virsh_ vol-create-from "$POOL" "$vol_xml" "$src_vol" --inputpool "$src_pool" >/dev/null
virsh_ vol-info --pool "$POOL" "$TEMPLATE_NAME.qcow2"

echo "labape: template ready: $target" >&2
echo "labape: use it with image_source = \"packer_template\", template = \"$TEMPLATE_NAME\" on a host group; tear down the source environment when done." >&2
