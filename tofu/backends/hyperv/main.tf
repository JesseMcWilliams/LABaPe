locals {
  network_prefix_length = tonumber(split("/", var.network_cidr)[1])
  gateway               = var.gateway != "" ? var.gateway : cidrhost(var.network_cidr, 1)

  # M2 scope: Linux (RHEL-family) only — same fixed "ks-<os_key>.cfg.tpl"
  # convention modules/vm/libvirt's os_catalog uses, and the exact same
  # template files (kickstart content has nothing hypervisor-specific
  # in it).
  os_catalog = {
    for os_key, iso_path in var.os_iso_paths : os_key => {
      kickstart_template   = "${path.module}/../../../iso/answer-files/rhel-family/ks-${os_key}.cfg.tpl"
      os_family             = "linux"
      boot_iso_remote_path = "${var.iso_storage_path}\\${os_key}-boot.iso"
    }
  }

  # Flatten host_groups (each with a `count`) into one map keyed by a
  # unique per-instance name — identical shape to backends/libvirt.
  vm_instances = merge([
    for hg in var.host_groups : {
      for i in range(hg.count) : "${hg.name}${i + 1}" => {
        os           = hg.os
        roles        = hg.roles
        image_source = coalesce(hg.image_source, var.image_source_default)
        cpu_count    = hg.cpu_count
        memory_mb    = hg.memory_mb
        disk_gb      = hg.disk_gb
      }
    }
  ]...)

  vm_instance_names = sort(keys(local.vm_instances))

  vm_static_ips = {
    for idx, name in local.vm_instance_names :
    name => cidrhost(var.network_cidr, var.static_ip_offset_start + idx)
  }

  # Distinct os values actually used — the boot ISO is built once per
  # OS, not once per VM (every VM of the same OS shares identical boot
  # media; tofu/modules/vm/hyperv/main.tf only builds the per-VM
  # kickstart ISO itself).
  os_keys_in_use = distinct([for name, vm in local.vm_instances : vm.os])
}

module "network" {
  source = "../../modules/network/hyperv"

  environment_name = terraform.workspace
  mode             = var.network_mode
  physical_nic     = var.physical_nic
}

# Once per distinct os value — see tofu/modules/vm/hyperv/scripts/
# prepare-boot-iso.sh for what this actually does and why (docs/
# base-images.md §4, Hyper-V section).
resource "null_resource" "boot_iso" {
  for_each = toset(local.os_keys_in_use)

  triggers = {
    vendor_iso_local_path = var.os_iso_paths[each.key]
  }

  provisioner "local-exec" {
    command     = "${path.module}/../../modules/vm/hyperv/scripts/prepare-boot-iso.sh"
    interpreter = ["/usr/bin/env", "bash"]
    environment = {
      VENDOR_ISO_LOCAL_PATH = var.os_iso_paths[each.key]
      HYPERV_HOST           = var.hyperv_host
      HYPERV_USER            = var.hyperv_user
      HYPERV_PASSWORD        = var.hyperv_password
      ISO_STORAGE_PATH       = var.iso_storage_path
      DEST_ISO_NAME          = "${each.key}-boot.iso"
    }
  }
}

module "vm" {
  source   = "../../modules/vm/hyperv"
  for_each = local.vm_instances

  name         = each.key
  os           = each.value.os
  roles        = each.value.roles
  image_source = each.value.image_source
  cpu_count    = each.value.cpu_count
  memory_mb    = each.value.memory_mb
  disk_gb      = each.value.disk_gb
  network_id   = module.network.network_id

  addressing = {
    mode          = "static"
    address       = local.vm_static_ips[each.key]
    prefix_length = local.network_prefix_length
    gateway       = local.gateway
  }

  admin_credential = {
    ssh_public_key = var.ssh_public_key
  }

  template_vars = {
    management_source = var.management_source
    syslog_host        = var.syslog_host
  }

  hyperv_host      = var.hyperv_host
  hyperv_user      = var.hyperv_user
  hyperv_password  = var.hyperv_password
  os_catalog       = local.os_catalog
  iso_storage_path = var.iso_storage_path
  vm_storage_path  = var.vm_storage_path
  debug_disk       = var.debug_disk

  depends_on = [null_resource.boot_iso]
}
