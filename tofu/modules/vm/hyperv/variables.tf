# Common `vm` module interface (DESIGN.md §6.1) — same variables as
# modules/vm/libvirt, this is the Hyper-V implementation. M2 scope:
# Linux only, image_source = "iso_direct" only (mirrors M1's libvirt
# scope exactly).

variable "name" {
  description = "VM/hostname."
  type        = string
}

variable "os" {
  description = "Key into var.os_catalog, e.g. \"rocky9\". M2 ships only that one entry."
  type        = string
}

variable "roles" {
  description = "Passthrough only — this module doesn't interpret roles, it just carries them through to the generated inventory (DESIGN.md §9)."
  type        = list(string)
  default     = []
}

variable "image_source" {
  description = "\"packer_template\" | \"iso_direct\". Only iso_direct is implemented (DESIGN.md §18) — packer_template is a documented, deliberate fail-fast until M6."
  type        = string

  validation {
    condition     = contains(["packer_template", "iso_direct"], var.image_source)
    error_message = "image_source must be \"packer_template\" or \"iso_direct\"."
  }
}

variable "cpu_count" {
  type    = number
  default = 2
}

variable "memory_mb" {
  type    = number
  default = 4096
}

variable "disk_gb" {
  type    = number
  default = 40
}

variable "network_id" {
  description = "Output of the network module (../network/hyperv) — the Hyper-V virtual switch name (DESIGN.md §6.2)."
  type        = string
}

variable "addressing" {
  description = "{ mode = \"static\"|\"dhcp\", address, prefix_length, gateway }. DHCP is accepted here but the pipeline-level IP-discovery step is deferred (DESIGN.md §17.4) — only static is exercised end-to-end."
  type = object({
    mode          = string
    address       = optional(string)
    prefix_length = optional(number)
    gateway       = optional(string)
  })

  validation {
    condition     = contains(["static", "dhcp"], var.addressing.mode)
    error_message = "addressing.mode must be \"static\" or \"dhcp\"."
  }
}

variable "admin_credential" {
  description = "Bootstrap credential from docs/credentials.md. Only ssh_public_key is used on this (Linux-only, M2) backend path."
  type = object({
    ssh_public_key         = optional(string)
    windows_admin_password = optional(string)
  })
  sensitive = true
}

variable "template_vars" {
  description = "Extra values rendered into the kickstart at provisioning time — e.g. management_source for firewall scoping (docs/credentials.md §7)."
  type        = map(string)
  default     = {}
}

# --- Backend-specific inputs (beyond the common §6.1 contract) ---

variable "hyperv_host" {
  description = "Hyper-V host IP/hostname — needed again here (beyond the provider's own connection config) because the ISO-staging steps shell out via smbclient over a local-exec provisioner, which runs outside the provider's own connection management."
  type        = string
}

variable "hyperv_user" {
  type = string
}

variable "hyperv_password" {
  type      = string
  sensitive = true
}

variable "os_catalog" {
  description = <<-EOT
    Per-OS metadata for the iso_direct path. M2 populates only "rocky9".

    boot_iso_remote_path is the *modified* boot ISO's path on the
    Hyper-V host (a Windows path) — built once per distinct os value by
    tofu/backends/hyperv/main.tf's prepare-boot-iso.sh, not by this
    module (every VM of the same OS shares one boot ISO; only the
    per-VM kickstart ISO differs, which this module builds itself).

    kickstart_template is a kickstart .cfg.tpl — the exact same
    template files modules/vm/libvirt uses (iso/answer-files/
    rhel-family/), since kickstart content has nothing hypervisor-
    specific in it.
  EOT
  type = map(object({
    boot_iso_remote_path = string
    kickstart_template   = string
    os_family             = string
  }))
}

variable "iso_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path, e.g. C:\\ISOs) where this VM's per-instance kickstart ISO is staged."
  type        = string
}

variable "vm_storage_path" {
  description = "Directory on the Hyper-V host (a Windows path, e.g. C:\\VMs) where this VM's VHD is created."
  type        = string
}
