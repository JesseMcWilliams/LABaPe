# Rocky Linux template build (Claude_Docs/Design_Base-Images.md §3). Run via
# scripts/build-template.sh, which supplies the variables, then imports
# the qcow2 into the template library like promote-to-template.sh does.
#
# Same kickstart template the iso_direct path uses
# (iso/answer-files/rhel-family/ks-<os_key>.cfg.tpl), rendered here with
# Packer's templatefile() (same syntax as OpenTofu's) and served over
# Packer's HTTP server; DHCP addressing, since the build VM sits on
# QEMU's user-mode network. Generalized by the same Ansible role promote
# uses (template_finalize), so a Packer-built template and a promoted
# one are interchangeable.

packer {
  required_plugins {
    qemu    = { source = "github.com/hashicorp/qemu", version = ">= 1.1.0" }
    ansible = { source = "github.com/hashicorp/ansible", version = ">= 1.1.0" }
  }
}

variable "repo_root" { type = string }
variable "os_key" { type = string }        # e.g. rocky9 -> ks-rocky9.cfg.tpl
variable "iso_path" { type = string }
variable "template_name" { type = string }
variable "output_dir" { type = string }
variable "ssh_public_key" { type = string }
variable "ssh_private_key_file" { type = string }
variable "disk_gb" {
  type    = number
  default = 40
}

source "qemu" "rocky" {
  iso_url      = var.iso_path
  iso_checksum = "none" # local, already-staged ISO (os_iso_paths)

  vm_name          = "${var.template_name}.qcow2"
  output_directory = var.output_dir
  format           = "qcow2"
  disk_size        = "${var.disk_gb}G"
  disk_interface   = "virtio" # what clones attach it as
  net_device       = "virtio-net"
  accelerator      = "kvm"
  cpus             = 2
  memory           = 4096
  headless         = true
  # -cpu host: QEMU's default CPU model (qemu64) lacks x86-64-v2, which
  # EL9's glibc requires ("Fatal glibc error: CPU does not support
  # x86-64-v2" -> kernel panic). libvirt picks a modern model for the
  # iso_direct path on its own.
  qemuargs = [
    ["-cpu", "host"],
    ["-serial", "file:${var.output_dir}/console.log"],
  ]

  http_content = {
    "/ks.cfg" = templatefile("${var.repo_root}/iso/answer-files/rhel-family/ks-${var.os_key}.cfg.tpl", {
      hostname          = "template"
      ssh_public_key    = var.ssh_public_key
      addressing        = { mode = "dhcp", address = "", prefix_length = 0, gateway = "" }
      management_source = ""
      syslog_host       = ""
      debug_disk        = false
    })
  }
  # BIOS isolinux menu: <up> to "Install Rocky Linux", <tab> to edit the
  # kernel line, append the kickstart URL.
  boot_wait    = "5s"
  boot_command = ["<up><tab> inst.text inst.ks=http://{{ .HTTPIP }}:{{ .HTTPPort }}/ks.cfg console=ttyS0<enter>"]

  communicator         = "ssh"
  ssh_username         = "labape"
  ssh_private_key_file = var.ssh_private_key_file
  ssh_timeout          = "45m"

  # template_finalize (finalize_shutdown=false) leaves the shutdown to Packer.
  shutdown_command = "sudo shutdown -P now"
  shutdown_timeout = "10m"
}

build {
  sources = ["source.qemu.rocky"]

  provisioner "ansible" {
    playbook_file   = "${var.repo_root}/ansible/playbooks/finalize-template.yml"
    user            = "labape"
    use_proxy       = false
    extra_arguments = ["-e", "finalize_shutdown=false"]
    ansible_env_vars = [
      "ANSIBLE_HOST_KEY_CHECKING=False",
      "ANSIBLE_CONFIG=${var.repo_root}/ansible/ansible.cfg",
    ]
  }
}
