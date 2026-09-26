output "hosts" {
  description = "Consumed by scripts/generate-inventory.py (Claude_Docs/Design_System-Overview.md §11, Claude_Docs/Reference_Networking.md §4). Mirrors tofu/backends/libvirt/outputs.tf."
  value = {
    for name, vm in module.vm : name => {
      ip_address = vm.ip_address
      os_family  = vm.os_family
      roles      = vm.roles
    }
  }
}
