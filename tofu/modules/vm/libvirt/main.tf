locals {
  os_meta   = lookup(var.os_catalog, var.os, null)
  os_family = local.os_meta != null ? local.os_meta.os_family : null

  # Per workspace: VM names repeat across environments (lab1 and a
  # test env can both have dc1), and one shared directory let them
  # overwrite each other's answer files.
  rendered_dir = "${path.module}/.rendered/${terraform.workspace}"

  # try() guards against Terraform evaluating the *other* family's
  # resource[0] reference (count = 0 in the branch not taken) while
  # building its dependency graph — coalesce then picks whichever one
  # actually exists for this instance.
  # Empty for packer_template VMs, which have no ISO answer file.
  rendered_answer_file_path = try(coalesce(
    try(local_file.kickstart[0].filename, null),
    try(local_file.windows_answer_file[0].filename, null),
    try(local_file.debian_user_data[0].filename, null),
  ), "")
  # Computed from the rendered content, not read back from the
  # local_file resources: a resource's content_md5 is unknown whenever the
  # file itself is being replaced (e.g. moved to another directory), and
  # an unknown trigger forces the VM to be reinstalled even though the
  # content didn't change. md5(content) is the same value content_md5 has.
  rendered_answer_file_md5 = try(coalesce(
    try(md5(local.kickstart_content), null),
    try(local.windows_answer_file_md5_masked, null),
    try(md5(local.debian_user_data_content), null),
  ), "")

  kickstart_content = contains(["linux", "debian_preseed"], coalesce(local.os_family, "none")) ? templatefile(local.os_meta.answer_file_template, {
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
  }) : null

  debian_user_data_content = local.os_family == "debian" ? templatefile(local.os_meta.answer_file_template, {
    hostname          = var.name
    ssh_public_key    = coalesce(var.admin_credential.ssh_public_key, "")
    addressing        = var.addressing
    management_source = lookup(var.template_vars, "management_source", "")
    syslog_host       = lookup(var.template_vars, "syslog_host", "")
  }) : null

  windows_answer_file_vars = {
    hostname               = var.name
    windows_admin_password = coalesce(var.admin_credential.windows_admin_password, "")
    addressing             = var.addressing
    management_source      = lookup(var.template_vars, "management_source", "")
  }
  # The reinstall trigger hashes the Windows answer file with the
  # password masked, so rotating windows_bootstrap_admin_password in the
  # vault doesn't reinstall every Windows VM on the next deploy. Template
  # or addressing changes still do. The password has to be changed on
  # running hosts separately (Claude_Docs/Reference_Credentials.md).
  windows_answer_file_md5_masked = local.os_family == "windows" ? md5(templatefile(
    local.os_meta.answer_file_template,
    merge(local.windows_answer_file_vars, { windows_admin_password = "masked" }),
  )) : null
  # Empty string (not null) when not debian — passed straight through to
  # create-iso-direct.sh's environment map, which requires a string.
  rendered_meta_data_path = try(local_file.debian_meta_data[0].filename, "")
}

# Fail fast and clearly on an unknown `os` key, rather than a confusing
# "attempt to index null value" error deeper in the module. Only
# meaningful for iso_direct — the packer_template path has its own
# validate_template below with template-specific messages.
resource "terraform_data" "validate_os" {
  count = var.image_source == "iso_direct" ? 1 : 0

  lifecycle {
    precondition {
      condition     = local.os_meta != null
      error_message = "Unknown os \"${var.os}\" — not present in var.os_catalog. See tofu/environments/small.tfvars.example."
    }
    precondition {
      condition     = local.os_meta == null || var.disk_gb >= try(local.os_meta.min_disk_gb, 0)
      error_message = "disk_gb = ${var.disk_gb} is below the ${try(local.os_meta.min_disk_gb, 0)} GB minimum for os \"${var.os}\" — set disk_gb on this host group (Claude_Docs/Design_Base-Images.md)."
    }
  }
}

# --- iso_direct path (install from ISO + answer file) ---
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

  content = local.kickstart_content

  depends_on = [terraform_data.validate_os]
}

resource "local_file" "windows_answer_file" {
  count    = var.image_source == "iso_direct" && local.os_family == "windows" ? 1 : 0
  filename = "${local.rendered_dir}/${var.name}-autounattend.xml"

  content = templatefile(local.os_meta.answer_file_template, local.windows_answer_file_vars)

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

  content = local.debian_user_data_content

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
      LABAPE_WORKSPACE = terraform.workspace
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

# --- packer_template path (M6, Claude_Docs/Design_Base-Images.md §5/§8) ---
#
# Clones a template from the library as a thin qcow2 overlay (the
# template is the read-only backing file, never modified) and hands the
# VM its identity on a small CD on first boot: a cloud-init NoCloud seed
# for Linux (hostname, static network, SSH key, root-partition growth),
# an oobeSystem answer file for a sysprepped Windows template (computer
# name, Administrator password, static IP, WinRM). Either way the result
# looks to Ansible exactly like an iso_direct VM.

locals {
  is_template       = var.image_source == "packer_template"
  template_path     = "${var.template_storage_path}/${var.template_name}.qcow2"
  clone_seed_dir    = "${local.rendered_dir}/${var.name}-clone"
  clone_is_windows  = local.os_family == "windows"
  # Fixed per environment + VM so cloud-init can match the NIC by MAC:
  # its sysconfig renderer (Rocky) can't do name globs (Testing_Troubleshooting-Log.md).
  # 52:54:00 is QEMU/KVM's locally administered prefix.
  clone_mac_hex = md5("${terraform.workspace}/${var.name}")
  clone_mac     = format("52:54:00:%s:%s:%s", substr(local.clone_mac_hex, 0, 2), substr(local.clone_mac_hex, 2, 2), substr(local.clone_mac_hex, 4, 2))

  clone_linux_files = local.is_template && !local.clone_is_windows && local.os_meta != null ? {
    "user-data" = templatefile("${path.module}/../../../../iso/answer-files/cloud-init/user-data-clone.yaml.tpl", {
      hostname       = var.name
      ssh_public_key = coalesce(var.admin_credential.ssh_public_key, "")
    })
    "meta-data" = templatefile("${path.module}/../../../../iso/answer-files/cloud-init/meta-data-clone.yaml.tpl", {
      hostname = var.name
    })
    "network-config" = templatefile("${path.module}/../../../../iso/answer-files/cloud-init/network-config.yaml.tpl", {
      addressing  = var.addressing
      mac_address = local.clone_mac
      # Rename to eth0 only where the renderer needs a device name
      # (sysconfig on Rocky, eni on Debian). Netplan (Ubuntu, os_family
      # "debian") matches by MAC natively, and on 26.04 the rename fails
      # anyway (dracut brings the NIC up first).
      set_name = local.os_family != "debian"
    })
  } : {}

  clone_windows_vars = local.windows_answer_file_vars
  clone_windows_template = "${path.module}/../../../../iso/answer-files/windows/autounattend-windows-clone.xml.tpl"
  clone_windows_firstboot = local.is_template && local.clone_is_windows ? templatefile(
    "${path.module}/../../../../iso/answer-files/windows/clone-firstboot.ps1.tpl",
    { addressing = var.addressing },
  ) : ""

  # Reinstall trigger for clones: same idea as rendered_answer_file_md5
  # (content-derived, password masked).
  clone_seed_md5 = !local.is_template ? "" : local.clone_is_windows ? md5(join("", [templatefile(
    local.clone_windows_template,
    merge(local.clone_windows_vars, { windows_admin_password = "masked" }),
  ), local.clone_windows_firstboot])) : md5(join("\n", [for k in sort(keys(local.clone_linux_files)) : local.clone_linux_files[k]]))
}

resource "terraform_data" "validate_template" {
  count = local.is_template ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.template_name != ""
      error_message = "Host \"${var.name}\" uses image_source = \"packer_template\" but its host group sets no template (e.g. template = \"rocky9-base-2026.10\")."
    }
    precondition {
      condition     = local.os_meta != null
      error_message = "Unknown os \"${var.os}\" — not present in var.os_catalog (the template's os key still needs an os_iso_paths entry so its os_family is known)."
    }
    precondition {
      condition     = local.os_meta == null || var.disk_gb >= try(local.os_meta.min_disk_gb, 0)
      error_message = "disk_gb = ${var.disk_gb} is below the ${try(local.os_meta.min_disk_gb, 0)} GB minimum for os \"${var.os}\"."
    }
  }
}

resource "local_file" "clone_linux_seed" {
  for_each = local.clone_linux_files
  filename = "${local.clone_seed_dir}/${each.key}"
  content  = each.value

  depends_on = [terraform_data.validate_template]
}

resource "local_file" "clone_windows_answer_file" {
  count    = local.is_template && local.clone_is_windows ? 1 : 0
  filename = "${local.clone_seed_dir}/autounattend.xml"
  content  = templatefile(local.clone_windows_template, local.clone_windows_vars)

  # Plaintext Administrator password, same as the iso_direct answer file.
  file_permission = "0600"

  depends_on = [terraform_data.validate_template]
}

resource "local_file" "clone_windows_firstboot" {
  count    = local.is_template && local.clone_is_windows ? 1 : 0
  filename = "${local.clone_seed_dir}/firstboot.ps1"
  content  = local.clone_windows_firstboot

  depends_on = [terraform_data.validate_template]
}

resource "null_resource" "vm_from_template" {
  count = local.is_template ? 1 : 0

  triggers = {
    name            = var.name
    libvirt_uri     = var.libvirt_uri
    template_path   = local.template_path
    bridge_device   = var.network_id
    cpu_count       = tostring(var.cpu_count)
    memory_mb       = tostring(var.memory_mb)
    disk_gb         = tostring(var.disk_gb)
    vm_storage_path = var.vm_storage_path
    seed_md5        = local.clone_seed_md5
  }

  provisioner "local-exec" {
    command     = "${path.module}/scripts/create-from-template.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      LIBVIRT_URI      = var.libvirt_uri
      VM_NAME          = var.name
      CPU_COUNT        = tostring(var.cpu_count)
      MEMORY_MB        = tostring(var.memory_mb)
      DISK_GB          = tostring(var.disk_gb)
      TEMPLATE_PATH    = local.template_path
      SEED_DIR         = local.clone_seed_dir
      BRIDGE_DEVICE    = var.network_id
      MAC_ADDRESS      = local.clone_mac
      OS_FAMILY        = local.os_family
      OS_VARIANT       = try(local.os_meta.os_variant, "")
      VM_STORAGE_PATH  = var.vm_storage_path
      LABAPE_WORKSPACE = terraform.workspace
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

  depends_on = [
    terraform_data.validate_template,
    local_file.clone_linux_seed,
    local_file.clone_windows_answer_file,
    local_file.clone_windows_firstboot,
  ]
}
