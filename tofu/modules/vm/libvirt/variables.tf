# Common `vm` module interface (DESIGN.md §6.1). Both backends' vm
# modules accept the same set of variables so an environment definition
# never changes based on which hypervisor is selected — only which
# backend module gets invoked does (tofu/backends/*, DESIGN.md §6.3).

variable "name" {
  description = "VM/hostname."
  type        = string
}

variable "os" {
  description = "Key into var.os_catalog, e.g. \"rocky9\". M1 ships only that one entry — DESIGN.md §5's full OS matrix lands across M2-M6."
  type        = string
}

variable "roles" {
  description = "Passthrough only — this module doesn't interpret roles, it just carries them through to the generated inventory (DESIGN.md §9)."
  type        = list(string)
  default     = []

  # Kept in sync by hand with scripts/generate-inventory.py's
  # ALL_ROLE_GROUPS — not worth a shared-file abstraction for a
  # five-item list that changes rarely. Catches a typo (e.g.
  # "domain_controler") at plan time instead of it silently producing a
  # dead inventory group no play ever matches.
  validation {
    condition = alltrue([
      for r in var.roles : contains(
        ["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"],
        r
      )
    ])
    error_message = "Each entry in roles must be one of: domain_controller, windows_server, windows_workstation, linux_server, linux_workstation."
  }
}

variable "image_source" {
  description = "\"packer_template\" | \"iso_direct\". Only iso_direct is implemented as of M1 (DESIGN.md §18) — packer_template is a documented, deliberate fail-fast until M6."
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
  description = "Output of the network module (../network/libvirt) — the bridge device name in bridged mode (DESIGN.md §6.2)."
  type        = string
}

variable "addressing" {
  description = "{ mode = \"static\"|\"dhcp\", address, prefix_length, gateway }. DHCP is accepted here but the pipeline-level IP-discovery step is deferred (DESIGN.md §17.4) — only static is exercised end-to-end as of M1."
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
  description = "Bootstrap credential from docs/credentials.md. Only ssh_public_key is used on this (Linux-only, M1) backend path."
  type = object({
    ssh_public_key         = optional(string)
    windows_admin_password = optional(string)
  })
  sensitive = true
}

variable "template_vars" {
  description = "Extra values rendered into the answer file at provisioning time — e.g. management_source for firewall scoping (docs/credentials.md §7). Deliberately NOT where domain_name/dns_forward_ip go (DESIGN.md §6.1's output table note)."
  type        = map(string)
  default     = {}
}

# --- Backend-specific inputs (beyond the common §6.1 contract) ---

variable "libvirt_uri" {
  description = "The same connection URI the libvirt provider itself is configured with (docs/credentials.md §3) — needed again here because the iso_direct path shells out to virt-install via a local-exec provisioner, which runs outside the provider's own resource management and can't introspect its connection config."
  type        = string
}

variable "os_catalog" {
  description = <<-EOT
    Per-OS metadata for the iso_direct path — the full OS matrix
    (DESIGN.md §5) is still out of scope; only what's actually
    implemented (Rocky, Windows Server 2022) is populated.

    iso_host_path must already exist on the libvirt host's filesystem —
    virt-install with a remote qemu+ssh:// connection doesn't upload a
    local ISO for you. Staging/downloading ISOs onto the host is a
    manual prerequisite for now, not something this module automates.

    answer_file_template is a kickstart .cfg.tpl for os_family "linux",
    an autounattend .xml.tpl for "windows", or a cloud-init/subiquity
    autoinstall .yaml.tpl for "debian" (Debian-family — first added
    alongside Windows 11 support; a distinct os_family from "linux"
    because the install mechanism differs — a NoCloud seed ISO, not
    kickstart's --initrd-inject — even though both connect over SSH
    the same way afterward). os_variant is the libosinfo short-id
    (e.g. "win2k22", "ubuntu24.04") virt-install's --os-variant needs
    for Windows/Debian; unused (empty string) for RHEL-family Linux,
    which relies on --os-variant detect=on,require=off instead.

    meta_data_template ("debian" only, empty string otherwise): cloud-
    init's NoCloud datasource needs a second file, meta-data, alongside
    answer_file_template's user-data — computed here (not independently
    via the vm module's own path.module) for the same reason
    answer_file_template already is: this module is invoked from
    tofu/backends/libvirt with a relative `source`, so a path built from
    ITS OWN path.module needs different ".." arithmetic than one built
    in the root module — simplest to only ever compute these paths in
    one place.
  EOT
  type = map(object({
    iso_host_path         = string
    answer_file_template  = string
    os_family             = string
    os_variant            = optional(string, "")
    meta_data_template    = optional(string, "")
  }))
}

variable "vm_storage_path" {
  description = "Directory on the libvirt host where this VM's disk (and, for Windows, the generated answer-file ISO) are created. docs/base-images.md — kept off the small root filesystem by convention."
  type        = string
}
