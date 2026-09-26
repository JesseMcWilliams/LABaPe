# Common `network` module interface (Claude_Docs/Design_System-Overview.md §6.2) — same variables
# as modules/network/libvirt, this is the Hyper-V implementation.

variable "environment_name" {
  description = "Name of the environment instance (matches the OpenTofu workspace, Claude_Docs/Design_System-Overview.md §6.3)."
  type        = string
}

variable "mode" {
  description = "bridged (default) or nat — Claude_Docs/Design_System-Overview.md §14. Only \"bridged\" is implemented as of M2; NAT (Internal switch + New-NetNat) lands in M7 (Claude_Docs/Reference_Networking.md §2)."
  type        = string
  default     = "bridged"

  validation {
    condition     = contains(["bridged", "nat"], var.mode)
    error_message = "mode must be \"bridged\" or \"nat\"."
  }

  validation {
    condition     = var.mode == "bridged"
    error_message = "mode = \"nat\" is not implemented yet for the Hyper-V backend (Claude_Docs/Reference_Networking.md §2 documents the design; it's scheduled for M7). Use \"bridged\" for now."
  }
}

variable "physical_nic" {
  description = <<-EOT
    Name of the physical network adapter on the Hyper-V host to bind an
    External switch to (e.g. "Ethernet") — Claude_Docs/Design_System-Overview.md §14. Only used when
    mode = "bridged". Unlike libvirt's bridge_device, this isn't a
    pre-existing prerequisite: the switch itself is what this module
    creates; physical_nic just says which host NIC it binds to.
  EOT
  type        = string
}

# Accepted for interface parity with modules/network/libvirt and the
# eventual NAT path (Claude_Docs/Design_System-Overview.md §6.2), but unused while mode = "bridged".
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
