# Root config for the libvirt backend (Claude_Docs/Design_System-Overview.md §6.3). The OpenTofu
# workspace IS the environment instance — see terraform.workspace usage
# in main.tf — so there's deliberately no separate "environment_name"
# variable duplicating that.

variable "libvirt_uri" {
  description = "e.g. qemu:///system (control machine is the libvirt host) or qemu+ssh://user@host/system (Claude_Docs/Reference_Credentials.md §3)."
  type        = string
}

variable "ssh_public_key" {
  description = "Ansible control machine's own SSH public key, baked into every Linux VM's kickstart (Claude_Docs/Reference_Credentials.md §5). Passed as TF_VAR_ssh_public_key by scripts/deploy.sh."
  type        = string
  sensitive   = true
}

variable "windows_admin_password" {
  description = "Local Administrator password baked into every Windows VM's autounattend.xml (Claude_Docs/Reference_Credentials.md §4) — from the vault's windows_bootstrap_admin_password, passed as TF_VAR_windows_admin_password by scripts/deploy.sh. Empty/unset is fine for Linux-only environments; deploy.sh refuses to proceed if it's still the vault's CHANGE_ME placeholder and a Windows host is actually being deployed."
  type        = string
  sensitive   = true
  default     = ""
}

# --- from environment.yml (Claude_Docs/Design_System-Overview.md §10), via environment.auto.tfvars.json ---

variable "network_mode" {
  type    = string
  default = "bridged"
}

variable "network_cidr" {
  description = "network_address/subnet_mask from environment.yml, normalized to CIDR by scripts/deploy.sh's YAML->tfvars conversion (Claude_Docs/Design_System-Overview.md §6.3)."
  type        = string
}

variable "gateway" {
  description = "Empty string means \"derive from network_cidr\" (main.tf) — environment.yml's gateway field is optional."
  type        = string
  default     = ""
}

variable "bridge_device" {
  type    = string
  default = "br0"
}

variable "management_source" {
  description = "Control machine IP/CIDR for firewall scoping (Claude_Docs/Reference_Credentials.md §7). Empty string disables scoping — not recommended beyond a fully trusted personal LAN."
  type        = string
  default     = ""
}

variable "static_ip_offset_start" {
  description = "Static-addressed hosts get network_cidr's .N, .N+1, ... starting here, in host_groups declaration order. Kept low but not .1/.2 to leave room for the gateway and other infrastructure."
  type        = number
  default     = 10
}

variable "vm_storage_path" {
  description = "Directory on the libvirt host where VM disks (and, for Windows, the generated answer-file ISO) are created — from environment.yml's vm_storage_path, via environment.auto.tfvars.json (Claude_Docs/Design_System-Overview.md §6.3)."
  type        = string
}

# --- from environments/<size>.tfvars (Claude_Docs/Design_System-Overview.md §9) ---

variable "os_iso_paths" {
  description = "Per-OS ISO path already staged on the libvirt host (Claude_Docs/Design_Base-Images.md — ISO staging is a manual prerequisite, not automated). M1 only needs a \"rocky9\" entry."
  type        = map(string)
}

variable "host_groups" {
  description = "Claude_Docs/Design_System-Overview.md §9. M1 only exercises os = \"rocky9\" / image_source = \"iso_direct\" — see tofu/environments/small.tfvars.example."
  type = list(object({
    name         = string
    count        = number
    os           = string
    roles        = list(string)
    image_source = optional(string) # null means "use var.image_source_default", main.tf
    cpu_count    = optional(number, 2)
    memory_mb    = optional(number, 4096)
    disk_gb      = optional(number, 40)
  }))
}

variable "image_source_default" {
  description = "environment.yml's image_source_default (Claude_Docs/Design_System-Overview.md §10) — applies to any host_group that doesn't set its own image_source. M1 only implements \"iso_direct\"."
  type        = string
  default     = "iso_direct"
}
