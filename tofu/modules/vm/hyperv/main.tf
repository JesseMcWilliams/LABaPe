locals {
  os_meta   = lookup(var.os_catalog, var.os, null)
  os_family = local.os_meta != null ? local.os_meta.os_family : null

  rendered_dir = "${path.module}/.rendered"

  vhd_path             = "${var.vm_storage_path}\\${var.name}.vhdx"
  debug_vhd_path        = "${var.vm_storage_path}\\${var.name}-debug.vhdx"
  kickstart_iso_name    = "${var.name}-ks.iso"
  kickstart_iso_remote_path = "${var.iso_storage_path}\\${local.kickstart_iso_name}"
}

# Fail fast and clearly on an unknown `os` key — mirrors modules/vm/libvirt.
resource "terraform_data" "validate_os" {
  count = var.image_source == "iso_direct" ? 1 : 0

  lifecycle {
    precondition {
      condition     = local.os_meta != null
      error_message = "Unknown os \"${var.os}\" — not present in var.os_catalog. See tofu/environments/small.tfvars.example."
    }
  }
}

# --- iso_direct path (M2's only implemented image_source, Linux only) ---

resource "local_file" "kickstart" {
  count    = var.image_source == "iso_direct" && local.os_family == "linux" ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-ks.cfg"

  content = templatefile(local.os_meta.kickstart_template, {
    hostname          = var.name
    ssh_public_key    = coalesce(var.admin_credential.ssh_public_key, "")
    addressing        = var.addressing
    management_source = lookup(var.template_vars, "management_source", "")
    syslog_host       = lookup(var.template_vars, "syslog_host", "")
    debug_disk        = var.debug_disk
  })

  depends_on = [terraform_data.validate_os]
}

# Per-VM kickstart ISO, staged on the Hyper-V host via smbclient — the
# taliesins/hyperv provider has no "build an ISO from a directory tree"
# primitive that reaches into an existing ISO's boot catalog the way
# this repo's Windows/libvirt path needed, but for a *fresh*, tiny,
# content-only ISO like this one, plain xorrisofs on the control machine
# plus a file push is simpler than fighting the provider's own
# hyperv_iso_image resource for a one-file payload.
resource "null_resource" "kickstart_iso" {
  count = var.image_source == "iso_direct" && local.os_family == "linux" ? 1 : 0

  triggers = {
    kickstart_md5 = local_file.kickstart[0].content_md5
  }

  provisioner "local-exec" {
    command     = "${path.module}/scripts/create-kickstart-iso.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      KICKSTART_LOCAL_PATH = local_file.kickstart[0].filename
      HYPERV_HOST          = var.hyperv_host
      HYPERV_USER           = var.hyperv_user
      HYPERV_PASSWORD       = var.hyperv_password
      ISO_STORAGE_PATH      = var.iso_storage_path
      DEST_ISO_NAME         = local.kickstart_iso_name
    }
  }

  depends_on = [local_file.kickstart]
}

resource "hyperv_vhd" "this" {
  count = var.image_source == "iso_direct" ? 1 : 0

  path = local.vhd_path
  size = var.disk_gb * 1024 * 1024 * 1024
}

# Debugging aid, off by default — see var.debug_disk. 64MB is plenty
# for a text diagnostic dump; the kickstart's own %post formats this as
# FAT (mkfs.vfat), not this resource, since the provider only creates
# an empty, unformatted VHD.
resource "hyperv_vhd" "debug" {
  count = var.image_source == "iso_direct" && var.debug_disk ? 1 : 0

  path = local.debug_vhd_path
  size = 67108864
}

resource "hyperv_machine_instance" "this" {
  count = var.image_source == "iso_direct" ? 1 : 0

  name             = var.name
  generation       = 1
  processor_count  = var.cpu_count
  static_memory    = true
  memory_startup_bytes = var.memory_mb * 1024 * 1024
  state            = "Running"
  automatic_stop_action = "TurnOff"

  # Pins the provider's own defaults for a Gen1 VM's processor block
  # explicitly -- left undeclared, Terraform sees the provider's
  # computed values as drift on every plan/apply (same class of issue
  # as dvd_drives.resource_pool_name above) and wants to remove the
  # whole block. Given the DVD-drive version of this crashed the
  # provider on apply, better not to find out whether this one does
  # too -- pin it instead.
  vm_processor {
    compatibility_for_migration_enabled               = false
    compatibility_for_older_operating_systems_enabled = false
    enable_host_resource_protection                   = false
    expose_virtualization_extensions                  = false
    hw_thread_count_per_core                          = 0
    maximum                                           = 100
    maximum_count_per_numa_node                       = 4
    maximum_count_per_numa_socket                     = 1
    relative_weight                                   = 100
    reserve                                           = 0
  }

  hard_disk_drives {
    controller_type     = "Ide"
    controller_number   = 0
    controller_location = 0
    path                = hyperv_vhd.this[0].path
  }

  # Debugging aid, off by default (var.debug_disk) — same controller as
  # the main disk, next slot over. The kickstart's %post formats and
  # writes to this; see tofu/modules/vm/hyperv/variables.tf's
  # debug_disk description for why (README's Known Gaps, M2).
  dynamic "hard_disk_drives" {
    for_each = var.debug_disk ? [1] : []
    content {
      controller_type     = "Ide"
      controller_number   = 0
      controller_location = 1
      path                = hyperv_vhd.debug[0].path
    }
  }

  # Boot media: controller 1 so it's never fighting the disk for a slot
  # on controller 0. Location 0 is the once-per-OS boot ISO (built by
  # tofu/backends/hyperv/main.tf's prepare-boot-iso.sh, shared by every
  # VM of this os); location 1 is this VM's own kickstart ISO.
  #
  # resource_pool_name explicitly set to match what Hyper-V actually
  # assigns a freshly attached DVD drive ("Primordial") -- left unset,
  # Terraform sees permanent drift on every subsequent plan/apply
  # (actual "Primordial" vs. config's implicit null) and any apply that
  # tries to reconcile it crashes the taliesins/hyperv provider with
  # "Unable to remove resource pool from dvd drive" (confirmed
  # hands-on, 3 times, including once leaving a VM mid-fix and powered
  # off). Setting this explicitly is what stops Terraform from ever
  # wanting to change it.
  dvd_drives {
    controller_number   = 1
    controller_location = 0
    path                = local.os_meta.boot_iso_remote_path
    resource_pool_name  = "Primordial"
  }

  dvd_drives {
    controller_number   = 1
    controller_location = 1
    path                = local.kickstart_iso_remote_path
    resource_pool_name  = "Primordial"
  }

  network_adaptors {
    name        = "eth0"
    switch_name = var.network_id
  }

  depends_on = [terraform_data.validate_os, null_resource.kickstart_iso]
}

# Gen1 BIOS boot order (CD before IDE by default) has to be fixed after
# every apply -- see scripts/set-boot-order.sh for why it's needed at
# all. NOT wired in as a Terraform resource here: doing so (a
# null_resource local-exec running Set-VMBios, depends_on this VM)
# reproducibly hit a taliesins/hyperv provider bug on every attempt --
# "Unable to remove resource pool from dvd drive" on
# hyperv_machine_instance.this itself, triggered any time something
# touches this VM's state through or after Terraform (an in-place
# update, or even just Stop-VM/Start-VM run externally). Confirmed
# 3 times hands-on, including once leaving the VM powered off with the
# fix half-applied. Run scripts/set-boot-order.sh by hand after
# `tofu apply` until that provider bug is understood well enough to
# automate around.

# --- packer_template path (not implemented until M6, Claude_Docs/Design_System-Overview.md §18) ---
resource "terraform_data" "packer_template_not_implemented" {
  count = var.image_source == "packer_template" ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.image_source != "packer_template"
      error_message = "image_source = \"packer_template\" is not implemented on the Hyper-V backend yet (Claude_Docs/Design_System-Overview.md §18 schedules it for M6). Use \"iso_direct\" for now."
    }
  }
}
