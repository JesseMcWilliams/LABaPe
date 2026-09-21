locals {
  network_prefix_length = tonumber(split("/", var.network_cidr)[1])
  gateway               = var.gateway != "" ? var.gateway : cidrhost(var.network_cidr, 1)

  # Every RHEL-family os_key follows a fixed "ks-<os_key>.cfg.tpl"
  # convention under rhel-family/ — that's still the default for
  # anything not explicitly listed here. Windows entries need real
  # per-entry metadata (a different answer-file format entirely, plus
  # the libosinfo short-id virt-install's --os-variant needs), so
  # they're special-cased instead of trying to force one naming
  # convention across totally different OS families. Extending this to
  # the full §5 OS matrix beyond what's actually implemented is still
  # out of scope.
  windows_catalog = {
    windows_server_2022 = {
      answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-win2022.xml.tpl"
      os_variant            = "win2k22"
    }
  }

  os_catalog = {
    for os_key, iso_path in var.os_iso_paths : os_key => (
      contains(keys(local.windows_catalog), os_key) ? {
        iso_host_path         = iso_path
        answer_file_template  = local.windows_catalog[os_key].answer_file_template
        os_family              = "windows"
        os_variant              = local.windows_catalog[os_key].os_variant
      } : {
        iso_host_path         = iso_path
        answer_file_template  = "${path.module}/../../../iso/answer-files/rhel-family/ks-${os_key}.cfg.tpl"
        os_family              = "linux"
        os_variant              = ""
      }
    )
  }

  # Flatten host_groups (each with a `count`) into one map keyed by a
  # unique per-instance name, e.g. "linsrv1", "linsrv2".
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

  # Sorted for a deterministic IP assignment order across applies —
  # `for_each` over a map doesn't guarantee iteration order on its own.
  vm_instance_names = sort(keys(local.vm_instances))

  vm_static_ips = {
    for idx, name in local.vm_instance_names :
    name => cidrhost(var.network_cidr, var.static_ip_offset_start + idx)
  }
}

module "network" {
  source = "../../modules/network/libvirt"

  environment_name = terraform.workspace
  mode             = var.network_mode
  bridge_device    = var.bridge_device
}

module "vm" {
  source   = "../../modules/vm/libvirt"
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
    ssh_public_key         = var.ssh_public_key
    windows_admin_password = var.windows_admin_password
  }

  template_vars = {
    management_source = var.management_source
  }

  libvirt_uri     = var.libvirt_uri
  os_catalog      = local.os_catalog
  vm_storage_path = var.vm_storage_path
}
