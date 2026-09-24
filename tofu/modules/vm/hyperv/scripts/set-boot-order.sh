#!/usr/bin/env bash
# Generation 1 Hyper-V VMs boot from a legacy BIOS whose device order
# defaults to CD before IDE (hard disk) -- confirmed via Get-VMBios on a
# freshly created VM. Since this module leaves the boot ISO and
# kickstart ISO permanently attached (tofu/modules/vm/hyperv/main.tf's
# dvd_drives blocks), that default silently re-runs the unattended
# kickstart install on every single reboot instead of ever booting the
# newly installed OS -- observed hands-on as a "stall": Anaconda
# actually completes the install and reboots correctly every ~20
# minutes, then boots straight back into the same install again,
# forever, because the CD is still first in boot order.
#
# The taliesins/hyperv provider's hyperv_machine_instance resource has
# no attribute for this -- its only boot_order block (vm_firmware) is
# documented as Generation 2 (UEFI) only. Gen1 boot order is BIOS-level
# state (Set-VMBios), outside anything the provider models, so this
# runs it directly over WinRM the same way the rest of this module's
# tooling (Ansible) already talks to the Hyper-V host.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

: "${HYPERV_HOST:?}"
: "${HYPERV_USER:?}"
: "${HYPERV_PASSWORD:?}"
: "${VM_NAME:?}"

# Set-VMBios -StartupOrder only works while the VM is off (confirmed
# hands-on: "Cannot modify the boot order property while the virtual
# machine is running") -- but hyperv_machine_instance's state="Running"
# already started it by the time this runs. Stop, fix, restart.
ansible all -i "${HYPERV_HOST}," \
  -m ansible.windows.win_shell \
  -a "Stop-VM -Name '${VM_NAME}' -TurnOff -Force; Set-VMBios -VMName '${VM_NAME}' -StartupOrder @('IDE','CD','LegacyNetworkAdapter','Floppy'); Start-VM -Name '${VM_NAME}'" \
  -e ansible_connection=winrm \
  -e ansible_winrm_transport=basic \
  -e ansible_winrm_server_cert_validation=ignore \
  -e ansible_port=5986 \
  -e ansible_user="${HYPERV_USER}" \
  -e ansible_password="${HYPERV_PASSWORD}"
