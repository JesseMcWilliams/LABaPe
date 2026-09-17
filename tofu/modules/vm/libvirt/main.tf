locals {
  os_meta   = lookup(var.os_catalog, var.os, null)
  os_family = local.os_meta != null ? local.os_meta.os_family : null

  kickstart_rendered_path = "${path.module}/.rendered/${var.name}-ks.cfg"
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
      error_message = "Unknown os \"${var.os}\" — not present in var.os_catalog. M1 only ships \"rocky9\"; see tofu/environments/small.tfvars.example."
    }
  }
}

# --- iso_direct path (M1's only implemented image_source) ---
#
# The dmacvicar/libvirt provider has no native equivalent of
# virt-install's `--initrd-inject` (injecting a kickstart file into the
# initrd and passing `inst.ks=` on the kernel command line), so there's
# no plain `libvirt_domain` resource that does an unattended kickstart
# install on its own. This shells out to virt-install directly instead
# — the same practical workaround real-world OpenTofu/libvirt-plus-
# kickstart setups use. docs/base-images.md §4 documents this as "the
# same mechanism Packer uses, just driven by OpenTofu instead."

resource "local_file" "kickstart" {
  count    = var.image_source == "iso_direct" ? 1 : 0
  filename = local.kickstart_rendered_path

  content = templatefile(local.os_meta.kickstart_template, {
    hostname          = var.name
    ssh_public_key    = coalesce(var.admin_credential.ssh_public_key, "")
    addressing        = var.addressing
    management_source = lookup(var.template_vars, "management_source", "")
  })

  depends_on = [terraform_data.validate_os]
}

resource "null_resource" "vm_iso_direct" {
  count = var.image_source == "iso_direct" ? 1 : 0

  triggers = {
    name          = var.name
    libvirt_uri   = var.libvirt_uri
    iso_host_path = local.os_meta.iso_host_path
    bridge_device = var.network_id
    cpu_count     = tostring(var.cpu_count)
    memory_mb     = tostring(var.memory_mb)
    disk_gb       = tostring(var.disk_gb)
    # Recreate the VM if the rendered kickstart changes — this is a
    # one-shot install, not a reconciled resource, so "changed" means
    # "tear down and reinstall," not "patch in place."
    kickstart_md5 = local_file.kickstart[0].content_md5
  }

  provisioner "local-exec" {
    command     = "${path.module}/scripts/create-iso-direct.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      LIBVIRT_URI    = var.libvirt_uri
      VM_NAME        = var.name
      CPU_COUNT      = tostring(var.cpu_count)
      MEMORY_MB      = tostring(var.memory_mb)
      DISK_GB        = tostring(var.disk_gb)
      ISO_HOST_PATH  = local.os_meta.iso_host_path
      KICKSTART_PATH = local_file.kickstart[0].filename
      BRIDGE_DEVICE  = var.network_id
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
  # local_file.kickstart) so a bad `os` key surfaces as that resource's
  # clear precondition message, not a raw "attempt to index null value"
  # from this resource's own triggers evaluating local.os_meta first.
  depends_on = [terraform_data.validate_os, local_file.kickstart]
}

# --- packer_template path (not implemented until M6, DESIGN.md §18) ---
#
# Deliberate fail-fast rather than a silent no-op or a half-working
# clone: better to error clearly now than to produce a "successful"
# apply that didn't actually create anything.
resource "terraform_data" "packer_template_not_implemented" {
  count = var.image_source == "packer_template" ? 1 : 0

  lifecycle {
    precondition {
      condition     = false
      error_message = "image_source = \"packer_template\" is not implemented on the libvirt backend yet (DESIGN.md §18 schedules it for M6). Use \"iso_direct\" for now."
    }
  }
}
