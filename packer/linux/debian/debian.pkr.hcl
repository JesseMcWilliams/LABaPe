# Debian template build (Claude_Docs/Design_Base-Images.md §3). Run via
# scripts/build-template.sh.
#
# Same preseed the iso_direct path uses
# (iso/answer-files/debian-family/preseed-debian.cfg.tpl), served over
# Packer's HTTP server and loaded with preseed/url from the installer's
# boot prompt (the iso_direct path injects it into the initrd instead;
# same file either way). The preseed partitions /dev/vda, so the disk is
# virtio here too. DHCP addressing (QEMU user-mode network). Generalized
# by template_finalize, which also installs cloud-init (d-i doesn't).

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

source "qemu" "debian" {
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

  http_content = {
    "/preseed.cfg" = templatefile("${var.repo_root}/iso/answer-files/debian-family/preseed-debian.cfg.tpl", {
      hostname          = "template"
      ssh_public_key    = var.ssh_public_key
      addressing        = { mode = "dhcp", address = "", prefix_length = 0, gateway = "" }
      management_source = ""
      syslog_host       = ""
    })
  }
  # isolinux menu: <esc> to the boot: prompt, then a fully automatic
  # install pointed at the preseed.
  boot_wait = "5s"
  boot_command = [
    "<esc><wait>",
    "install auto=true priority=critical preseed/url=http://{{ .HTTPIP }}:{{ .HTTPPort }}/preseed.cfg ",
    "netcfg/get_hostname=template netcfg/get_domain= console=ttyS0<enter>",
  ]

  communicator         = "ssh"
  ssh_username         = "labape"
  ssh_private_key_file = var.ssh_private_key_file
  ssh_timeout          = "60m"

  shutdown_command = "sudo shutdown -P now"
  shutdown_timeout = "10m"
}

build {
  sources = ["source.qemu.debian"]

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
