module "network" {
  source = "../../modules/network/hyperv"

  environment_name = terraform.workspace
  mode             = var.network_mode
  physical_nic     = var.physical_nic
}

# module "vm" wiring lands once tofu/modules/vm/hyperv exists (M2,
# in progress) — mirrors tofu/backends/libvirt/main.tf's shape.
