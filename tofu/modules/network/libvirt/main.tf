# Bridged mode (default, Claude_Docs/Design_System-Overview.md §14) needs no libvirt resource at
# all: VMs attach straight to the pre-existing bridge device via a
# `network_interface { bridge = ... }` block on `libvirt_domain` in the
# vm module (../vm/libvirt), not through a `libvirt_network` resource.
# This module's only job is to hand that bridge name through as
# `network_id`, per the common network-module contract (Claude_Docs/Design_System-Overview.md §6.2).
#
# The bridge itself (`nmcli connection add type bridge ...`) is a
# one-time host prerequisite documented in Claude_Docs/Reference_Networking.md §1, not
# something OpenTofu creates or manages. If it doesn't exist,
# `libvirt_domain` fails loudly when it tries to attach to it — there's
# no separate existence check here to duplicate that.
#
# NAT mode (opt-in, M7) is where this module will actually manage a
# `libvirt_network` resource (Claude_Docs/Reference_Networking.md §2) — the `mode`
# variable's validation blocks that path until it's implemented.
