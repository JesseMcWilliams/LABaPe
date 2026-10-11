locals {
  # The network catalog from environment.yml (Planning_Web-Interface-
  # Design.md §20), resolved: gateway defaults to the network's .1, DNS to
  # the gateway, bridge to var.bridge_device. scripts/lib/
  # render_environment_tfvars.py turns an older single `network:` block
  # into a one-entry catalog named "default".
  networks = {
    for name, n in var.networks : name => {
      cidr          = n.cidr
      prefix_length = tonumber(split("/", n.cidr)[1])
      gateway       = n.gateway != "" ? n.gateway : cidrhost(n.cidr, 1)
      dns_servers   = length(n.dns_servers) > 0 ? n.dns_servers : [n.gateway != "" ? n.gateway : cidrhost(n.cidr, 1)]
      bridge        = n.bridge != "" ? n.bridge : var.bridge_device
    }
  }
  default_network = var.default_network != "" ? var.default_network : sort(keys(var.networks))[0]

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
  # via `wiminfo` against each real eval ISO that the image indexes are
  # identical (1 = Standard Core, 2 = Standard with Desktop Experience),
  # so nothing in the XML itself is version-specific; the vm module picks
  # index 2 unless the host group sets windows_core = true. Windows 11
  # (client) is a genuinely different template, not just a different
  # os_variant — see iso/answer-files/windows/autounattend-windows-
  # client.xml.tpl's own comments for why (image selection by name, not
  # index; LabConfig hardware-check bypasses a Server eval ISO never
  # needed).
  # windows_11 deliberately uses os_variant "win10", not "win11": the
  # win11 libosinfo profile makes virt-install select OVMF/UEFI firmware
  # plus an emulated TPM, and UEFI's "Press any key to boot from CD or
  # DVD" prompt times out unattended, dropping into the OVMF Boot
  # Manager. win10 is the same device model on plain SeaBIOS, matching
  # Server. (`--boot firmware=bios` would say this explicitly, but libvirt
  # then needs a SeaBIOS firmware descriptor, which this Debian host's
  # qemu packages don't ship; only edk2 ones are in
  # /usr/share/qemu/firmware/.) Windows 11 Setup's own TPM/Secure Boot/CPU
  # checks are skipped by the LabConfig keys in the client template.
  # Tradeoff: no Secure Boot/TPM-realistic testing on these VMs (not
  # needed).
  # min_disk_gb (optional, checked by the vm module's validate_os):
  # Windows 11 Setup refuses a disk below its own floor ("The system
  # drive needs to be at least 52 GB" on 24H2 — hit on the repo-wide 40
  # GB default); 64 is Microsoft's published minimum. Everything else
  # installs fine at 40.
  windows_catalog = {
    windows_server_2019 = { os_variant = "win2k19", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_server_2022 = { os_variant = "win2k22", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_server_2025 = { os_variant = "win2k25", answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-server.xml.tpl" }
    windows_11           = { os_variant = "win10",   answer_file_template = "${path.module}/../../../iso/answer-files/windows/autounattend-windows-client.xml.tpl", min_disk_gb = 64 }
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
    # (24.04.3) in Claude_Docs/Testing_Troubleshooting-Log.md. os_variant falls back to
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
  # guided-storage bug hit on Ubuntu (Claude_Docs/Testing_Troubleshooting-Log.md) —
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
        min_disk_gb            = try(local.windows_catalog[os_key].min_disk_gb, 0)
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
        template     = hg.template == null ? "" : hg.template
        windows_core = hg.windows_core
        cpu_count    = hg.cpu_count
        memory_mb    = hg.memory_mb
        disk_gb      = hg.disk_gb
        network      = coalesce(hg.network, local.default_network)
        addressing   = hg.addressing
        # An explicit address (the web UI's allocation, Planning_Web-
        # Interface-Design.md §20.4) wins over the offset scheme below.
        address = try(hg.addresses[i], "")
      }
    }
  ]...)

  # Sorted for a deterministic IP assignment order across applies —
  # `for_each` over a map doesn't guarantee iteration order on its own.
  vm_instance_names = sort(keys(local.vm_instances))

  # Static VMs without an explicit address get <network>'s .N, .N+1, ...
  # from the profile's offset for that network, in name order, counted
  # per network. With one network and every VM static (every environment
  # before networks existed), this is exactly the old single-network
  # scheme, so existing environments plan no changes.
  vm_offset_ips = merge([
    for net in keys(local.networks) : {
      for idx, name in [
        for n in local.vm_instance_names : n
        if local.vm_instances[n].network == net && local.vm_instances[n].addressing == "static" && local.vm_instances[n].address == ""
      ] : name => cidrhost(local.networks[net].cidr, lookup(var.static_ip_offsets, net, var.static_ip_offset_start) + idx)
    }
  ]...)

  vm_static_ips = {
    for name, vm in local.vm_instances : name =>
    vm.addressing != "static" ? null : vm.address != "" ? vm.address : local.vm_offset_ips[name]
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
  template_name = each.value.template
  windows_core = each.value.windows_core
  template_storage_path = var.template_storage_path != "" ? var.template_storage_path : "${var.vm_storage_path}/templates"
  cpu_count    = each.value.cpu_count
  memory_mb    = each.value.memory_mb
  disk_gb      = each.value.disk_gb
  # The host group's network's bridge. (module.network only validates
  # the network mode and passes var.bridge_device through: bridged is the
  # only mode until NAT lands, M7.)
  network_id = local.networks[each.value.network].bridge

  addressing = {
    mode          = each.value.addressing
    address       = local.vm_static_ips[each.key]
    prefix_length = local.networks[each.value.network].prefix_length
    gateway       = local.networks[each.value.network].gateway
    dns           = local.networks[each.value.network].dns_servers
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
