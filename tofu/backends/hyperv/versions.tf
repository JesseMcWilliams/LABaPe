terraform {
  required_version = ">= 1.6.0"

  required_providers {
    hyperv = {
      source  = "taliesins/hyperv"
      version = "~> 1.2"
    }
  }
}

# WinRM to the Hyper-V host itself (DESIGN.md §6, docs/credentials.md
# §2) — insecure = true accepts the self-signed cert docs/
# install-opentofu-windows-wsl.md §3 walks through setting up, same
# reasoning as the libvirt backend's own self-hosted-lab posture.
provider "hyperv" {
  user     = var.hyperv_user
  password = var.hyperv_password
  host     = var.hyperv_host
  port     = 5986
  https    = true
  insecure = true
  timeout  = "30s"
}
