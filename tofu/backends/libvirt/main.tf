locals {
  network_prefix_length = tonumber(split("/", var.network_cidr)[1])
  gateway               = var.gateway != "" ? var.gateway : cidrhost(var.network_cidr, 1)

  # M1 only has one kickstart family (RHEL), so the template path
  # follows a fixed "ks-<os_key>.cfg.tpl" convention under
  # rhel-family/. Generalizing this per §5's full OS matrix is an
  # M2+ concern, not needed for the M1 smoke test.
  os_catalog = {
    for os_key, iso_path in var.os_iso_paths : os_key => {
      iso_host_path      = iso_path
      kickstart_template = "${path.module}/../../../iso/answer-files/rhel-family/ks-${os_key}.cfg.tpl"
      os_family          = "linux"
    }
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
    ssh_public_key = var.ssh_public_key
  }

  template_vars = {
    management_source = var.management_source
  }

  libvirt_uri = var.libvirt_uri
  os_catalog  = local.os_catalog
}
