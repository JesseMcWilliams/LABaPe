# Bridged mode (default, Claude_Docs/Design_System-Overview.md §14): an External Hyper-V switch
# bound to the host's physical NIC. Unlike libvirt's bridge_device (a
# pre-existing host prerequisite, Claude_Docs/Reference_Networking.md §1), Hyper-V has no
# "just attach to the physical LAN" primitive without a switch object —
# so this module actually creates one, named per environment_name so
# multiple environments on the same Hyper-V host don't collide over one
# shared switch (a real gap the libvirt module's single fixed br0 has,
# per Claude_Docs/Design_System-Overview.md §17.4 — Hyper-V avoids it for free here).
#
# NAT mode (opt-in, M7) is where this module will manage an Internal
# switch + New-NetNat instead (Claude_Docs/Reference_Networking.md §2) — the `mode`
# variable's validation blocks that path until it's implemented.

resource "hyperv_network_switch" "this" {
  name                 = "labape-${var.environment_name}"
  # Plain ASCII only: an em-dash here once caused the provider's WinRM
  # JSON round-trip to come back corrupted ("invalid character 'D'
  # after object key:value pair") on the read-back after create — a
  # real encoding mismatch somewhere in its PowerShell/WinRM pipeline,
  # not a config mistake. The switch itself was created fine each time;
  # only the provider's own state read-back choked. Stick to ASCII in
  # any string this provider sends to the host.
  notes                = "Managed by LABaPe (tofu/modules/network/hyperv) - Claude_Docs/Design_System-Overview.md section 14."
  allow_management_os  = true
  switch_type          = "External"
  net_adapter_names    = [var.physical_nic]
}
