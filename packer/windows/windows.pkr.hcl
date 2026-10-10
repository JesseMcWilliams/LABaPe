# Windows template build, all versions (Claude_Docs/Design_Base-Images.md §3).
# Run via scripts/build-template.sh, which picks the answer-file template
# for the os key (the same one the iso_direct path uses), supplies the
# variables and imports the qcow2 into the template library.
#
# The VM is set up the way clones will attach it: q35 machine with the
# disk on IDE (q35 presents that as AHCI/SATA, matching the clones'
# bus=sata), an e1000e NIC (in-box driver), legacy BIOS (Packer's
# default; Windows 11's checks are skipped by the answer file's LabConfig
# keys). The answer file goes on a CD as Autounattend.xml, with comments
# stripped (Setup's parser rejects multi-line comments). Generalized by
# the template_finalize role; Packer's shutdown_command starts the
# role's sysprep task, then waits for the VM to power off.

packer {
  required_plugins {
    qemu    = { source = "github.com/hashicorp/qemu", version = ">= 1.1.0" }
    ansible = { source = "github.com/hashicorp/ansible", version = ">= 1.1.0" }
  }
}

variable "repo_root" { type = string }
variable "os_key" { type = string }
variable "iso_path" { type = string }
variable "answer_template" { type = string } # path relative to repo_root
variable "template_name" { type = string }
variable "output_dir" { type = string }
variable "admin_password" {
  type      = string
  sensitive = true
}
variable "ansible_vars_file" { type = string } # 0600 JSON with the WinRM password
variable "disk_gb" {
  type    = number
  default = 40
}

source "qemu" "windows" {
  iso_url      = var.iso_path
  iso_checksum = "none" # local, already-staged ISO (os_iso_paths)

  vm_name          = "${var.template_name}.qcow2"
  output_directory = var.output_dir
  format           = "qcow2"
  disk_size        = "${var.disk_gb}G"
  machine_type     = "q35"
  disk_interface   = "ide"
  net_device       = "e1000e"
  accelerator      = "kvm"
  cpus             = 2
  memory           = 4096
  headless         = true
  qemuargs         = [["-cpu", "host"]]

  cd_label = "AUTOUNATTEND"
  cd_content = {
    "Autounattend.xml" = regex_replace(
      templatefile("${var.repo_root}/${var.answer_template}", {
        hostname               = "template"
        windows_admin_password = var.admin_password
        addressing             = { mode = "dhcp", address = "", prefix_length = 0, gateway = "" }
        management_source      = ""
      }),
      "(?s)<!--.*?-->", ""
    )
  }
  boot_wait = "5s"

  communicator   = "winrm"
  winrm_username = "Administrator"
  winrm_password = var.admin_password
  winrm_use_ssl  = true
  winrm_insecure = true
  winrm_port     = 5986
  winrm_timeout  = "2h"

  # Starts the sysprep task template_finalize registered (finalize_shutdown=false).
  shutdown_command = "schtasks /Run /TN LabapeFinalize"
  shutdown_timeout = "30m"
}

build {
  sources = ["source.qemu.windows"]

  provisioner "ansible" {
    playbook_file = "${var.repo_root}/ansible/playbooks/finalize-template.yml"
    user          = "Administrator"
    use_proxy     = false
    # Our own inventory line, matching scripts/generate-inventory.py's
    # Windows hosts: Packer's default inventory adds a shell type that
    # breaks the WinRM connection plugin ("should have the shell type of
    # cmd", then "-EncodedCommand is not properly encoded").
    inventory_file_template = "default ansible_host={{ .Host }} ansible_port={{ .Port }} ansible_user=Administrator ansible_connection=winrm ansible_winrm_transport=basic ansible_winrm_server_cert_validation=ignore\n"
    extra_arguments = [
      "-e", "finalize_shutdown=false",
      "-e", "@${var.ansible_vars_file}",
    ]
    ansible_env_vars = [
      "ANSIBLE_HOST_KEY_CHECKING=False",
      "ANSIBLE_CONFIG=${var.repo_root}/ansible/ansible.cfg",
    ]
  }
}
