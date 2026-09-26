output "name" {
  value = var.name
}

output "roles" {
  value = var.roles
}

output "os_family" {
  description = "\"windows\" | \"linux\", derived from var.os — tells the inventory generator whether to set ansible_connection=winrm or ssh (Claude_Docs/Design_System-Overview.md §6.1)."
  value       = local.os_family
}

output "ip_address" {
  description = "For addressing.mode = \"static\" this just echoes the input (Claude_Docs/Design_System-Overview.md §6.1). mode = \"dhcp\" isn't wired end-to-end yet (§17.4) — same gap as modules/vm/libvirt."
  value       = var.addressing.mode == "static" ? var.addressing.address : null
}
