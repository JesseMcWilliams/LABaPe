locals {
  os_meta   = lookup(var.os_catalog, var.os, null)
  os_family = local.os_meta != null ? local.os_meta.os_family : null

  rendered_dir = "${path.module}/.rendered"

  # try() guards against Terraform evaluating the *other* family's
  # resource[0] reference (count = 0 in the branch not taken) while
  # building its dependency graph — coalesce then picks whichever one
  # actually exists for this instance.
  rendered_answer_file_path = coalesce(
    try(local_file.kickstart[0].filename, null),
    try(local_file.windows_answer_file[0].filename, null),
    try(local_file.debian_user_data[0].filename, null),
  )
  rendered_answer_file_md5 = coalesce(
    try(local_file.kickstart[0].content_md5, null),
    try(local_file.windows_answer_file[0].content_md5, null),
    try(local_file.debian_user_data[0].content_md5, null),
  )
  # Empty string (not null) when not debian — passed straight through to
  # create-iso-direct.sh's environment map, which requires a string.
  rendered_meta_data_path = try(local_file.debian_meta_data[0].filename, "")
}

# Fail fast and clearly on an unknown `os` key, rather than a confusing
# "attempt to index null value" error deeper in the module. Only
# meaningful for iso_direct — os_catalog isn't consumed by the
# packer_template path (which has its own, more specific precondition
# below), so this shouldn't fire and mask that clearer error.
resource "terraform_data" "validate_os" {
  count = var.image_source == "iso_direct" ? 1 : 0

  lifecycle {
    precondition {
      condition     = local.os_meta != null
      error_message = "Unknown os \"${var.os}\" — not present in var.os_catalog. See tofu/environments/small.tfvars.example."
    }
  }
}

# --- iso_direct path (M1's only implemented image_source) ---
#
# Neither the dmacvicar/libvirt provider nor virt-install has one
# unattended-install mechanism that covers every OS family, so this
# shells out to virt-install directly per os_family instead of using a
# plain libvirt_domain resource — the same practical workaround
# real-world OpenTofu/libvirt-plus-unattended-install setups use.
# Claude_Docs/Design_Base-Images.md §4 documents this as "the same mechanism Packer
# uses, just driven by OpenTofu instead."
#
# Linux: virt-install --location + --initrd-inject injects a rendered
# kickstart file straight into the boot initrd (Claude_Docs/Design_Base-Images.md
# §4). Windows: Windows Setup has no equivalent kernel-argument hook —
# it auto-detects an autounattend.xml at the root of any attached
# optical/floppy media instead, so create-iso-direct.sh builds a tiny
# ISO containing just that file and attaches it as a second CD-ROM.

resource "local_file" "kickstart" {
  # debian_preseed shares this resource — same mechanism (a single
  # answer file injected into the initrd, referenced by a kernel
  # command-line argument), just a different answer-file format and
  # kernel-arg syntax, both handled entirely in create-iso-direct.sh's
  # os_family-specific case. Not worth a second near-identical resource
  # for that.
  count    = var.image_source == "iso_direct" && contains(["linux", "debian_preseed"], local.os_family) ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-answer.cfg"

  content = templatefile(local.os_meta.answer_file_template, {
    hostname          = var.name
    ssh_public_key    = coalesce(var.admin_credential.ssh_public_key, "")
    addressing        = var.addressing
    management_source = lookup(var.template_vars, "management_source", "")
    syslog_host       = lookup(var.template_vars, "syslog_host", "")
    # The debug disk (README's Known Gaps, M2) is Hyper-V-specific —
    # modules/vm/hyperv attaches the actual extra disk this needs;
    # nothing here does, so always false. Still has to be passed:
    # templatefile() requires every variable the template references.
    debug_disk = false
  })

  depends_on = [terraform_data.validate_os]
}

resource "local_file" "windows_answer_file" {
  count    = var.image_source == "iso_direct" && local.os_family == "windows" ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-autounattend.xml"

  content = templatefile(local.os_meta.answer_file_template, {
    hostname                = var.name
    windows_admin_password  = coalesce(var.admin_credential.windows_admin_password, "")
    addressing               = var.addressing
    management_source        = lookup(var.template_vars, "management_source", "")
  })

  # Contains a real plaintext secret (the local Administrator password),
  # unlike the Linux kickstart which only ever holds a public key —
  # keep it off-limits to anyone else on the control machine.
  file_permission = "0600"

  depends_on = [terraform_data.validate_os]
}

# Debian-family autoinstall (cloud-init/subiquity) needs TWO files at
# the seed ISO's root — user-data and meta-data (cloud-init's NoCloud
# datasource convention) — unlike kickstart's single injected file or
# Windows' single autounattend.xml, hence two resources instead of one.
resource "local_file" "debian_user_data" {
  count    = var.image_source == "iso_direct" && local.os_family == "debian" ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-user-data"

  content = templatefile(local.os_meta.answer_file_template, {
    hostname          = var.name
    ssh_public_key    = coalesce(var.admin_credential.ssh_public_key, "")
    addressing        = var.addressing
    management_source = lookup(var.template_vars, "management_source", "")
    syslog_host       = lookup(var.template_vars, "syslog_host", "")
  })

  depends_on = [terraform_data.validate_os]
}

resource "local_file" "debian_meta_data" {
  count    = var.image_source == "iso_direct" && local.os_family == "debian" ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-meta-data"

  content = templatefile(local.os_meta.meta_data_template, {
    hostname = var.name
  })

  depends_on = [terraform_data.validate_os]
}

resource "null_resource" "vm_iso_direct" {
  count = var.image_source == "iso_direct" ? 1 : 0

  triggers = {
    name            = var.name
    libvirt_uri     = var.libvirt_uri
    iso_host_path   = local.os_meta.iso_host_path
    bridge_device   = var.network_id
    cpu_count       = tostring(var.cpu_count)
    memory_mb       = tostring(var.memory_mb)
    disk_gb         = tostring(var.disk_gb)
    vm_storage_path = var.vm_storage_path
    # Recreate the VM if the rendered answer file changes — this is a
    # one-shot install, not a reconciled resource, so "changed" means
    # "tear down and reinstall," not "patch in place."
    answer_file_md5 = local.rendered_answer_file_md5
  }

  provisioner "local-exec" {
    command     = "${path.module}/scripts/create-iso-direct.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      LIBVIRT_URI     = var.libvirt_uri
      VM_NAME         = var.name
      CPU_COUNT       = tostring(var.cpu_count)
      MEMORY_MB       = tostring(var.memory_mb)
      DISK_GB         = tostring(var.disk_gb)
      ISO_HOST_PATH   = local.os_meta.iso_host_path
      ANSWER_FILE_PATH = local.rendered_answer_file_path
      META_DATA_PATH  = local.rendered_meta_data_path
      BRIDGE_DEVICE   = var.network_id
      OS_FAMILY       = local.os_family
      OS_VARIANT      = try(local.os_meta.os_variant, "")
      VM_STORAGE_PATH = var.vm_storage_path
    }
  }

  provisioner "local-exec" {
    when        = destroy
    command     = "${path.module}/scripts/destroy-vm.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      LIBVIRT_URI = self.triggers.libvirt_uri
      VM_NAME     = self.triggers.name
    }
  }

  # Also depends on validate_os directly (not just transitively via
  # local_file.kickstart/windows_answer_file) so a bad `os` key surfaces
  # as that resource's clear precondition message, not a raw "attempt to
  # index null value" from this resource's own triggers evaluating
  # local.os_meta first.
  depends_on = [
    terraform_data.validate_os,
    local_file.kickstart,
    local_file.windows_answer_file,
    local_file.debian_user_data,
    local_file.debian_meta_data,
  ]
}

# --- packer_template path (not implemented until M6, Claude_Docs/Design_System-Overview.md §18) ---
#
# Deliberate fail-fast rather than a silent no-op or a half-working
# clone: better to error clearly now than to produce a "successful"
# apply that didn't actually create anything.
resource "terraform_data" "packer_template_not_implemented" {
  count = var.image_source == "packer_template" ? 1 : 0

  lifecycle {
    precondition {
      # count above already gates this resource's existence on
      # image_source == "packer_template", so this always fails when it
      # runs — but a bare `false` literal is rejected at validate time
      # ("must refer to at least one object from elsewhere in the
      # configuration"), so the condition re-checks the same value.
      condition     = var.image_source != "packer_template"
      error_message = "image_source = \"packer_template\" is not implemented on the libvirt backend yet (Claude_Docs/Design_System-Overview.md §18 schedules it for M6). Use \"iso_direct\" for now."
    }
  }
}
