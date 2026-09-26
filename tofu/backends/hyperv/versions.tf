terraform {
  required_version = ">= 1.6.0"

  required_providers {
    hyperv = {
      source  = "taliesins/hyperv"
      version = "~> 1.2"
    }
  }
}

# WinRM to the Hyper-V host itself (Claude_Docs/Design_System-Overview.md §6, Claude_Docs/Reference_Credentials.md
# §2) — insecure = true accepts the self-signed cert;
# User_Docs/Install-OpenTofu-WSL.md §3 walks through setting up, same
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
