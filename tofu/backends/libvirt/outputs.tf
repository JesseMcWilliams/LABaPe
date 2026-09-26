output "hosts" {
  description = <<-EOT
    Consumed by scripts/generate-inventory.py to build the Ansible
    inventory + hosts.generated (Claude_Docs/Design_System-Overview.md §11, Claude_Docs/Reference_Networking.md §4).
    domain_name for building FQDNs comes from environment.yml directly
    in that script, not from here — this config never reads domain_name
    at all, by design (Claude_Docs/Design_System-Overview.md §6.1's output-table note: domain
    config belongs to the domain_controller Ansible role, not to
    OS-provisioning-time OpenTofu resources).
  EOT
  value = {
    for name, vm in module.vm : name => {
      ip_address = vm.ip_address
      os_family  = vm.os_family
      roles      = vm.roles
    }
  }
}
