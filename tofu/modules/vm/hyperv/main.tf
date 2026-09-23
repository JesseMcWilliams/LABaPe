locals {
  os_meta   = lookup(var.os_catalog, var.os, null)
  os_family = local.os_meta != null ? local.os_meta.os_family : null

  rendered_dir = "${path.module}/.rendered"

  vhd_path             = "${var.vm_storage_path}\\${var.name}.vhdx"
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

resource "hyperv_machine_instance" "this" {
  count = var.image_source == "iso_direct" ? 1 : 0

  name             = var.name
  generation       = 1
  processor_count  = var.cpu_count
  static_memory    = true
  memory_startup_bytes = var.memory_mb * 1024 * 1024
  state            = "Running"
  automatic_stop_action = "TurnOff"

  hard_disk_drives {
    controller_type     = "Ide"
    controller_number   = 0
    controller_location = 0
    path                = hyperv_vhd.this[0].path
  }

  # Boot media: controller 1 so it's never fighting the disk for a slot
  # on controller 0. Location 0 is the once-per-OS boot ISO (built by
  # tofu/backends/hyperv/main.tf's prepare-boot-iso.sh, shared by every
  # VM of this os); location 1 is this VM's own kickstart ISO.
  dvd_drives {
    controller_number   = 1
    controller_location = 0
    path                = local.os_meta.boot_iso_remote_path
  }

  dvd_drives {
    controller_number   = 1
    controller_location = 1
    path                = local.kickstart_iso_remote_path
  }

  network_adaptors {
    name        = "eth0"
    switch_name = var.network_id
  }

  depends_on = [terraform_data.validate_os, null_resource.kickstart_iso]
}

# --- packer_template path (not implemented until M6, DESIGN.md §18) ---
resource "terraform_data" "packer_template_not_implemented" {
  count = var.image_source == "packer_template" ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.image_source != "packer_template"
      error_message = "image_source = \"packer_template\" is not implemented on the Hyper-V backend yet (DESIGN.md §18 schedules it for M6). Use \"iso_direct\" for now."
    }
  }
}
