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
  # The three Server versions share one answer-file template — confirmed
  # via `wiminfo` against each real eval ISO that install image index 1
  # ("SERVERSTANDARDCORE") is consistent across all of them, so nothing
  # in the XML itself is actually version-specific there. Windows 11
  # (client) is a genuinely different template, not just a different
  # os_variant — see iso/answer-files/windows/autounattend-windows-
  # client.xml.tpl's own comments for why (image selection by name, not
  # index; LabConfig hardware-check bypasses a Server eval ISO never
  # needed).
  windows_catalog = {
    windows_server_2019 = { os_variant = "win2k19", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_server_2022 = { os_variant = "win2k22", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_server_2025 = { os_variant = "win2k25", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_11           = { os_variant = "win11",   answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-client.xml.tpl" }
    windows_10           = { os_variant = "win10",   answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-10.xml.tpl" }
  }

  # First-ever non-RHEL Linux family (M5's deferred workstation-support
  # half) — a distinct os_family ("debian"), not folded into "linux",
  # since the *install mechanism* genuinely differs (a NoCloud seed ISO,
  # not kickstart's --initrd-inject) even though the Ansible-facing
  # side (SSH, labape bootstrap user) is identical. Verified safe: every
  # existing os_family branch is `== "windows" ? ... : ...` (an else,
  # not an explicit `== "linux"` check), so a third value doesn't break
  # anything outside the two vm/*/main.tf modules that need to know
  # about it directly.
  debian_catalog = {
    # os_variant is the libosinfo short-id virt-install's --os-variant
    # needs — verify against the actual libvirt host if installs start
    # failing on this specifically (osinfo-query isn't installed there
    # as of this writing, so this hasn't been independently confirmed
    # beyond being the documented current-Ubuntu-LTS short-id).
    ubuntu_lts = { os_variant = "ubuntu24.04", answer_file_template = "${path.module}/../../../iso/answer-files/debian-family/user-data-ubuntu-lts.yaml.tpl" }

    # ubuntu_26: added to test whether a newer subiquity build resolves
    # the still-open guided-storage bug documented against ubuntu_lts
    # (24.04.3) in docs/troubleshooting-log.md. os_variant falls back to
    # "ubuntu24.04" — this host's osinfo-db (dated mid-2025) has no
    # ubuntu-26.04 entry yet; harmless here since kernel/initrd are
    # passed explicitly on --location rather than relying on osinfo
    # tree/media detection anyway. Same answer-file template as
    # ubuntu_lts — the subiquity/cloud-init install mechanism is
    # unchanged between releases.
    ubuntu_26 = { os_variant = "ubuntu24.04", answer_file_template = "${path.module}/../../../iso/answer-files/debian-family/user-data-ubuntu-lts.yaml.tpl" }
  }

  # Real Debian (not Ubuntu) — classic debian-installer/preseed, a
  # genuinely different install mechanism from "debian" above (initrd-
  # inject + kernel append, like "linux"/kickstart, not a NoCloud seed
  # ISO), hence its own os_family rather than reusing either existing
  # one. Added specifically to test whether it avoids the still-open
  # guided-storage bug hit on Ubuntu (docs/troubleshooting-log.md) —
  # d-i's preseed is a mature, fully-scriptable installer with no
  # subiquity-style TUI confirmation screens at all.
  debian_preseed_catalog = {
    debian_latest = { os_variant = "debian13", answer_file_template = "${path.module}/../../../iso/answer-files/debian-family/preseed-debian.cfg.tpl" }
  }

  os_catalog = {
    for os_key, iso_path in var.os_iso_paths : os_key => (
      contains(keys(local.windows_catalog), os_key) ? {
        iso_host_path         = iso_path
        answer_file_template  = local.windows_catalog[os_key].answer_file_template
        os_family              = "windows"
        os_variant              = local.windows_catalog[os_key].os_variant
      } : contains(keys(local.debian_catalog), os_key) ? {
        iso_host_path         = iso_path
        answer_file_template  = local.debian_catalog[os_key].answer_file_template
        os_family              = "debian"
        os_variant              = local.debian_catalog[os_key].os_variant
        meta_data_template     = "${path.module}/../../../iso/answer-files/debian-family/meta-data.yaml.tpl"
      } : contains(keys(local.debian_preseed_catalog), os_key) ? {
        iso_host_path         = iso_path
        answer_file_template  = local.debian_preseed_catalog[os_key].answer_file_template
        os_family              = "debian_preseed"
        os_variant              = local.debian_preseed_catalog[os_key].os_variant
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
