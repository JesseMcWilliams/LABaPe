# Root config for the Hyper-V backend (Claude_Docs/Design_System-Overview.md §6.3). Mirrors
# tofu/backends/libvirt/variables.tf's shape.

variable "hyperv_host" {
  description = "Hyper-V host IP/hostname (Claude_Docs/Reference_Credentials.md §2, vault's hyperv_host). Passed as TF_VAR_hyperv_host by scripts/deploy.sh."
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
  description = "Ansible control machine's own SSH public key, baked into every Linux VM's kickstart (Claude_Docs/Reference_Credentials.md §5). Passed as TF_VAR_ssh_public_key by scripts/deploy.sh."
  type        = string
  sensitive   = true
}

variable "windows_admin_password" {
  description = "Local Administrator password baked into every Windows VM's autounattend.xml — from the vault's windows_bootstrap_admin_password. Empty/unset is fine for Linux-only environments."
  type        = string
  sensitive   = true
  default     = ""
}

# --- from environment.yml (Claude_Docs/Design_System-Overview.md §10), via environment.auto.tfvars.json ---

variable "network_mode" {
  type    = string
  default = "bridged"
}

variable "physical_nic" {
  description = "Physical NIC name on the Hyper-V host to bind the External switch to (Claude_Docs/Design_System-Overview.md §14) — e.g. \"Ethernet\"."
  type        = string
}

variable "iso_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path, e.g. C:\\ISOs) where vendor ISOs are staged and per-VM kickstart/boot ISOs are generated (Claude_Docs/Design_Base-Images.md — ISO staging is a manual prerequisite, not automated)."
  type        = string
}

variable "vm_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path) where VM VHDs are created."
  type        = string
}

# --- from environments/<size>.tfvars (Claude_Docs/Design_System-Overview.md §9) ---

variable "os_iso_paths" {
  description = "Per-OS vendor ISO path already staged on the Hyper-V host (a Windows path under var.iso_storage_path). M2 only needs a \"rocky9\" entry."
  type        = map(string)
}

variable "host_groups" {
  description = "Claude_Docs/Design_System-Overview.md §9. M2 only exercises os = \"rocky9\" / image_source = \"iso_direct\" — see tofu/environments/small.tfvars.example."
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
  description = "environment.yml's image_source_default (Claude_Docs/Design_System-Overview.md §10). M2 only implements \"iso_direct\"."
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
  description = "Control machine IP/CIDR for firewall scoping (Claude_Docs/Reference_Credentials.md §7)."
  type        = string
  default     = ""
}

variable "syslog_host" {
  description = "Control machine IP to stream install-time and post-install logs to (scripts/test/syslog-capture.py, UDP 1514) — a troubleshooting aid, off by default (README's Known Gaps, M2)."
  type        = string
  default     = ""
}

variable "debug_disk" {
  description = "Attach a small extra FAT-formatted disk that a systemd unit writes a diagnostic dump to every boot, readable from the Hyper-V host via Mount-VHD — a troubleshooting aid, off by default (README's Known Gaps, M2). See tofu/modules/vm/hyperv/variables.tf."
  type        = bool
  default     = false
}
