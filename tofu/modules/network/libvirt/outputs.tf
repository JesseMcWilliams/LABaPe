output "network_id" {
  description = "Consumed by every `vm` module call for this environment (DESIGN.md §6.1). In bridged mode this is just the bridge device name."
  value       = var.bridge_device
}
