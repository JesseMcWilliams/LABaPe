# Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() —
# cloud-init's NoCloud datasource requires this file to exist at the
# seed volume's root alongside user-data, even though nothing here is
# actually consulted beyond instance-id/local-hostname.
instance-id: ${hostname}
local-hostname: ${hostname}
