# Root config for the Hyper-V backend (DESIGN.md §6.3). Mirrors
# tofu/backends/libvirt/variables.tf's shape.

variable "hyperv_host" {
  description = "Hyper-V host IP/hostname (docs/credentials.md §2, vault's hyperv_host). Passed as TF_VAR_hyperv_host by scripts/deploy.sh."
  type        = string
}

variable "hyperv_user" {
  description = "Admin-capable account on the Hyper-V host (vault's hyperv_user)."
  type        = string
}

variable "hyperv_password" {
  description = "vault's hyperv_password."
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "Ansible control machine's own SSH public key, baked into every Linux VM's kickstart (docs/credentials.md §5). Passed as TF_VAR_ssh_public_key by scripts/deploy.sh."
  type        = string
  sensitive   = true
}

variable "windows_admin_password" {
  description = "Local Administrator password baked into every Windows VM's autounattend.xml — from the vault's windows_bootstrap_admin_password. Empty/unset is fine for Linux-only environments."
  type        = string
  sensitive   = true
  default     = ""
}

# --- from environment.yml (DESIGN.md §10), via environment.auto.tfvars.json ---

variable "network_mode" {
  type    = string
  default = "bridged"
}

variable "physical_nic" {
  description = "Physical NIC name on the Hyper-V host to bind the External switch to (DESIGN.md §14) — e.g. \"Ethernet\"."
  type        = string
}

variable "iso_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path, e.g. C:\\ISOs) where vendor ISOs are staged and per-VM kickstart/boot ISOs are generated (docs/base-images.md — ISO staging is a manual prerequisite, not automated)."
  type        = string
}

variable "vm_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path) where VM VHDs are created."
  type        = string
}

# --- from environments/<size>.tfvars (DESIGN.md §9) ---

variable "os_iso_paths" {
  description = "Per-OS vendor ISO path already staged on the Hyper-V host (a Windows path under var.iso_storage_path). M2 only needs a \"rocky9\" entry."
  type        = map(string)
}

variable "host_groups" {
  description = "DESIGN.md §9. M2 only exercises os = \"rocky9\" / image_source = \"iso_direct\" — see tofu/environments/small.tfvars.example."
  type = list(object({
    name         = string
    count        = number
    os           = string
    roles        = list(string)
    image_source = optional(string)
    cpu_count    = optional(number, 2)
    memory_mb    = optional(number, 4096)
    disk_gb      = optional(number, 40)
  }))
}

variable "image_source_default" {
  description = "environment.yml's image_source_default (DESIGN.md §10). M2 only implements \"iso_direct\"."
  type        = string
  default     = "iso_direct"
}

variable "static_ip_offset_start" {
  type    = number
  default = 10
}

variable "network_cidr" {
  description = "network_address/subnet_mask from environment.yml, normalized to CIDR by scripts/deploy.sh's YAML->tfvars conversion."
  type        = string
}

variable "gateway" {
  description = "Empty string means \"derive from network_cidr\" (main.tf)."
  type        = string
  default     = ""
}

variable "management_source" {
  description = "Control machine IP/CIDR for firewall scoping (docs/credentials.md §7)."
  type        = string
  default     = ""
}
