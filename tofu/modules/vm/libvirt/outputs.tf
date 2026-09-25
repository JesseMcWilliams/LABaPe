output "name" {
  value = var.name
}

output "roles" {
  value = var.roles
}

output "os_family" {
  description = "\"windows\" | \"linux\" | \"debian\", derived from os_catalog — tells the inventory generator whether to set ansible_connection=winrm or ssh (DESIGN.md §6.1). \"debian\" connects over ssh exactly like \"linux\" — it's a distinct value because the *install* mechanism differs, not the Ansible-facing one."
  value       = local.os_family
}

output "ip_address" {
  description = "For addressing.mode = \"static\", echoes the input. For \"dhcp\", null — the post-apply discovery step this would need is deferred (DESIGN.md §17.4/§6.1), so DHCP-mode hosts don't get a usable IP for inventory generation yet."
  value       = var.addressing.mode == "static" ? var.addressing.address : null
}
