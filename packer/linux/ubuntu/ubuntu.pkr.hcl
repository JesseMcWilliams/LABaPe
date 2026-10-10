# Ubuntu LTS template build (Claude_Docs/Design_Base-Images.md §3). Run via
# scripts/build-template.sh.
#
# Same autoinstall user-data the iso_direct path uses
# (iso/answer-files/debian-family/user-data-ubuntu-lts.yaml.tpl), on a
# CIDATA-labeled CD so cloud-init finds it by label (`ds=nocloud`, no
# path — see create-iso-direct.sh for why a path broke it). The live
# server ISO boots GRUB; the boot command drops to its prompt and boots
# casper with the autoinstall arguments. DHCP addressing (QEMU user-mode
# network). Generalized by template_finalize, which also removes the
# installer's cloud-init overrides so clones' seeds are honoured.

packer {
  required_plugins {
    qemu    = { source = "github.com/hashicorp/qemu", version = ">= 1.1.0" }
    ansible = { source = "github.com/hashicorp/ansible", version = ">= 1.1.0" }
  }
}

variable "repo_root" { type = string }
variable "os_key" { type = string }
variable "iso_path" { type = string }
variable "template_name" { type = string }
variable "output_dir" { type = string }
variable "ssh_public_key" { type = string }
variable "ssh_private_key_file" { type = string }
variable "disk_gb" {
  type    = number
  default = 40
}

locals {
  answer_vars = {
    hostname          = "template"
    ssh_public_key    = var.ssh_public_key
    addressing        = { mode = "dhcp", address = "", prefix_length = 0, gateway = "" }
    management_source = ""
    syslog_host       = ""
  }
}

source "qemu" "ubuntu" {
  iso_url      = var.iso_path
  iso_checksum = "none"

  vm_name          = "${var.template_name}.qcow2"
  output_directory = var.output_dir
  format           = "qcow2"
  disk_size        = "${var.disk_gb}G"
  machine_type     = "q35"
  disk_interface   = "virtio"
  net_device       = "virtio-net"
  accelerator      = "kvm"
  cpus             = 2
  memory           = 4096
  headless         = true
  qemuargs = [
    ["-cpu", "host"],
    ["-serial", "file:${var.output_dir}/console.log"],
  ]

  cd_label = "CIDATA"
  cd_content = {
    "user-data" = templatefile("${var.repo_root}/iso/answer-files/debian-family/user-data-ubuntu-lts.yaml.tpl", local.answer_vars)
    "meta-data" = templatefile("${var.repo_root}/iso/answer-files/debian-family/meta-data.yaml.tpl", local.answer_vars)
  }
  boot_wait = "5s"
  boot_command = [
    "c<wait3>",
    "linux /casper/vmlinuz autoinstall ds=nocloud console=ttyS0 ---<enter><wait>",
    "initrd /casper/initrd<enter><wait>",
    "boot<enter>",
  ]

  communicator         = "ssh"
  ssh_username         = "labape"
  ssh_private_key_file = var.ssh_private_key_file
  ssh_timeout          = "60m"

  shutdown_command = "sudo shutdown -P now"
  shutdown_timeout = "10m"
}

build {
  sources = ["source.qemu.ubuntu"]

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
