# Common `network` module interface (Claude_Docs/Design_System-Overview.md §6.2). Both backends'
# network modules accept the same variables; this is the libvirt
# implementation.

variable "environment_name" {
  description = "Name of the environment instance (matches the OpenTofu workspace, Claude_Docs/Design_System-Overview.md §6.3)."
  type        = string
}

variable "mode" {
  description = "bridged (default) or nat — Claude_Docs/Design_System-Overview.md §14. Only \"bridged\" is implemented as of M1; NAT lands in M7 (Claude_Docs/Reference_Networking.md §2)."
  type        = string
  default     = "bridged"

  validation {
    condition     = contains(["bridged", "nat"], var.mode)
    error_message = "mode must be \"bridged\" or \"nat\"."
  }

  validation {
    condition     = var.mode == "bridged"
    error_message = "mode = \"nat\" is not implemented yet for the libvirt backend (Claude_Docs/Reference_Networking.md §2 documents the design; it's scheduled for M7). Use \"bridged\" for now."
  }
}

variable "bridge_device" {
  description = <<-EOT
    Name of the pre-existing Linux bridge device on the libvirt host that
    already carries the physical NIC (Claude_Docs/Reference_Networking.md §1 — a one-time
    host prerequisite created with `nmcli connection add type bridge ...`,
    not something OpenTofu creates). Only used when mode = "bridged".
  EOT
  type        = string
  default     = "br0"
}

# Accepted for interface parity with the Hyper-V module and the eventual
# NAT path (Claude_Docs/Design_System-Overview.md §6.2), but unused while mode = "bridged" — bridged
# networking attaches directly to the existing physical LAN, so there's
# no virtual network object for these to configure.
variable "network_address" {
  description = "Unused in bridged mode. Reserved for the NAT implementation (M7)."
  type        = string
  default     = null
}

variable "subnet_mask" {
  description = "Unused in bridged mode. Reserved for the NAT implementation (M7)."
  type        = string
  default     = null
}

variable "gateway" {
  description = "Unused in bridged mode. Reserved for the NAT implementation (M7)."
  type        = string
  default     = null
}
