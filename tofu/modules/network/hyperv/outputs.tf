output "network_id" {
  description = "Consumed by every `vm` module call for this environment (Claude_Docs/Design_System-Overview.md §6.1). For Hyper-V this is the virtual switch name."
  value       = hyperv_network_switch.this.name
}
