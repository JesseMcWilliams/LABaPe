output "name" {
  value = var.name
}

output "roles" {
  value = var.roles
}

output "os_family" {
  description = "\"windows\" | \"linux\" | \"debian\" | \"debian_preseed\", derived from os_catalog — tells the inventory generator whether to set ansible_connection=winrm or ssh (Claude_Docs/Design_System-Overview.md §6.1). \"debian\"/\"debian_preseed\" connect over ssh exactly like \"linux\" — each is a distinct value because its *install* mechanism differs, not the Ansible-facing one."
  value       = local.os_family
}

output "ip_address" {
  description = "For addressing.mode = \"static\", echoes the input. For \"dhcp\", null: deploy.sh fills it in after boot (scripts/lib/discover_dhcp_ips.py, by mac_address)."
  value       = var.addressing.mode == "static" ? var.addressing.address : null
}

output "mac_address" {
  description = "The NIC's MAC, deterministic per workspace and VM name (52:54:00 prefix)."
  value       = local.clone_mac
}

output "addressing_mode" {
  value = var.addressing.mode
}

output "bridge" {
  value = var.network_id
}
