# Networking: Bridged (default), NAT (opt-in), and Pre-flight Checks

This documents the mechanics behind DESIGN.md §14. Default mode is
**bridged** — environment VMs go straight on your physical LAN. NAT
isolation is supported as an opt-in for environments that want it, and
its Hyper-V setup is the more involved of the two, so it's documented in
full below.

## 1. Bridged mode (default)

VMs get a network adapter on your existing physical LAN — no separate
virtual network to build, no routing to set up, and reachability is
exactly whatever reachability already exists between your admin machine
and that LAN. This matches how you already work with the lab: static
IPs (or a reserved range) that you drop into your own hosts file (§4).

### Hyper-V

Bridged is the simple case — no NAT resource is needed at all:

```powershell
# One-time per Hyper-V host, if you don't already have an External switch
New-VMSwitch -Name "lab-external" -NetAdapterName "<physical-NIC-name>" -AllowManagementOS $true
```

Every VM's network adapter attaches to that switch. OpenTofu's
`hyperv_network_switch` (type `External`) and the network adapter block
on `hyperv_machine_instance` cover this natively — no provisioner or
workaround needed, unlike the NAT path below.

### libvirt

Bridged means attaching VMs to a Linux bridge device on the host (e.g.
`br0`) that already carries the physical NIC:

```bash
# One-time per libvirt host, if you don't already have a bridge
nmcli connection add type bridge ifname br0
nmcli connection add type ethernet ifname <physical-NIC-name> master br0
```

`libvirt_network` with `mode = "bridge"` referencing `br0` (or a
`libvirt_domain` network interface pointed straight at the bridge) is
all OpenTofu needs — again, no extra provisioner required.

### Caution

Because the domain controller and its DNS/AD services are directly
reachable on the physical segment in bridged mode, two things are worth
being deliberate about, not because they're forbidden:

- Pick a `domain_name` (DESIGN.md §8) that can't collide with a real
  corporate domain reachable on the same network.
- Don't enable the DC's DHCP Server role unless you actually want it
  answering DHCP for that physical segment — AD DS promotion alone
  doesn't turn on DHCP, so this only happens if you explicitly add it.

## 2. NAT mode (opt-in)

For an environment that should stay off the physical LAN entirely
(isolated testing, or simply not wanting a throwaway AD forest visible
to the rest of the network), set `network.mode: nat` (DESIGN.md §10).

### libvirt (native, no extra steps)

```hcl
resource "libvirt_network" "env" {
  name      = var.environment_name
  mode      = "nat"
  domain    = var.domain_name
  addresses = [var.subnet_cidr]   # e.g. "10.50.0.0/24"
  dhcp {
    enabled = true
  }
  dns {
    enabled = true
  }
}
```

libvirt's own `dnsmasq` instance handles DHCP and basic DNS for this
network; NAT (via `iptables`/`nftables` MASQUERADE rules) is set up by
libvirt itself when the network is started. Nothing else to configure.

### Hyper-V (needs an explicit binding — this is the real work)

Hyper-V has no single resource that creates an Internal switch **and**
binds NAT to it — `New-NetNat` is a separate object from the vSwitch.
`taliesins/hyperv` manages the switch but not `New-NetNat`, so the
binding runs as a WinRM-executed provisioner. Full sequence, in order:

**1. Create the Internal switch** (native `hyperv_network_switch` resource):

```powershell
New-VMSwitch -Name "<env>-internal" -SwitchType Internal
```

**2. Assign the gateway address to the host's new vNIC.** Creating an
Internal switch adds a `vEthernet (<env>-internal)` adapter on the host
itself with no IP configured — this step gives the host (and therefore
the NAT gateway) an address inside the environment's subnet:

```powershell
New-NetIPAddress `
  -IPAddress 10.50.0.1 `
  -PrefixLength 24 `
  -InterfaceAlias "vEthernet (<env>-internal)"
```

**3. Bind NAT to that subnet:**

```powershell
New-NetNat `
  -Name "<env>-nat" `
  -InternalIPInterfaceAddressPrefix "10.50.0.0/24"
```

This is the step that fails if another NAT already claims an
overlapping prefix on the host — see §3, the pre-flight check exists
specifically to catch this before it happens mid-`apply`.

**4. DHCP is not automatic here — unlike libvirt or Hyper-V's built-in
"Default Switch," a custom Internal+NAT switch has no DHCP server of its
own.** Two ways to handle it, pick one per environment:

- Simplest: every host in this environment uses static addressing
  (DESIGN.md §14, "Per-host addressing") — no DHCP server needed at all.
- If DHCP-mode hosts are wanted anyway, install the Windows **DHCP
  Server** role on the Hyper-V host and scope it to the environment's
  subnet:
  ```powershell
  Install-WindowsFeature DHCP -IncludeManagementTools
  Add-DhcpServerV4Scope -Name "<env>" -StartRange 10.50.0.20 `
    -EndRange 10.50.0.199 -SubnetMask 255.255.255.0
  Set-DhcpServerV4OptionValue -ScopeId 10.50.0.0 -Router 10.50.0.1 `
    -DnsServer 10.50.0.1
  ```

**5. Teardown, on `tofu destroy`** (order matters — reverse of creation):

```powershell
Remove-NetNat -Name "<env>-nat" -Confirm:$false
Remove-VMSwitch -Name "<env>-internal" -Confirm:$false
```

(Removing the switch also removes the vNIC and its IP; a DHCP scope
added in step 4 should be removed separately with
`Remove-DhcpServerV4Scope` if it was added.)

### OpenTofu sketch tying it together

```hcl
resource "hyperv_network_switch" "env" {
  name        = "${var.environment_name}-internal"
  switch_type = "Internal"
}

resource "null_resource" "nat_binding" {
  depends_on = [hyperv_network_switch.env]

  connection {
    type     = "winrm"
    host     = var.hyperv_host
    user     = var.hyperv_user
    password = var.hyperv_password
    https    = true
    insecure = true
  }

  provisioner "remote-exec" {
    inline = [
      "powershell.exe -Command \"New-NetIPAddress -IPAddress ${var.gateway_ip} -PrefixLength ${var.prefix_length} -InterfaceAlias 'vEthernet (${var.environment_name}-internal)'\"",
      "powershell.exe -Command \"New-NetNat -Name '${var.environment_name}-nat' -InternalIPInterfaceAddressPrefix '${var.subnet_cidr}'\"",
    ]
  }

  provisioner "remote-exec" {
    when = destroy
    inline = [
      "powershell.exe -Command \"Remove-NetNat -Name '${var.environment_name}-nat' -Confirm:$false\"",
    ]
  }
}
```

The WinRM `remote-exec` connection defaults to `cmd.exe`, so each inline
command is wrapped in `powershell.exe -Command "..."` explicitly.

## 3. Pre-flight network/address availability check

Running more than one environment, or re-running against a physical LAN
you don't fully control, both raise the same question before any VM is
created: **is the address space this environment is about to use already
taken?** Two different failure modes, depending on mode:

- **Bridged**: a static IP the environment plans to assign might already
  be in use by something else on the physical network.
- **NAT (Hyper-V)**: `New-NetNat -InternalIPInterfaceAddressPrefix` fails
  outright if another NAT already claims an overlapping prefix — the
  exact scenario from §2 step 3.
- **NAT (libvirt)**: a `libvirt_network` with an already-used name or an
  overlapping address range fails to define/start.

`scripts/check-network.sh` (Linux/libvirt control machine) and
`scripts/check-network.ps1` (when the control machine or a check is run
from Windows) run **before** `tofu apply`, take the environment's
planned static addresses (and subnet, for NAT mode) from
`environment.yml`, and fail fast with a clear message instead of letting
`tofu apply` partially create VMs before hitting a collision:

- For every static address the environment plans to assign: ARP-scan or
  ping it (`arping -c 2 -w 2 <ip>` / `Test-Connection -Count 2 <ip>`) —
  anything answering means it's already in use.
- For NAT mode: on Hyper-V, `Get-NetNat` and compare prefixes; on
  libvirt, `virsh net-list --all` plus `ip route` on the host, and
  compare against the planned subnet.
- Exit non-zero with the specific conflicting address/prefix on any hit,
  so the failure is actionable rather than a generic apply error.

`scripts/deploy.sh` (DESIGN.md §15) runs this check as its first step,
before `tofu apply` — a rejected environment definition should never get
as far as creating a VM.

## 4. Hosts-file snippet generation

Since lab access is normally managed by hand-editing a hosts file rather
than via DNS, `scripts/deploy.sh` also emits a ready-to-paste snippet
after `tofu apply` succeeds, generated from the same inventory Ansible
consumes:

```
# LABaPe: <environment name>, generated <timestamp>
10.50.0.10   dc1.company.com dc1
10.50.0.11   winsrv1.company.com winsrv1
10.50.0.12   lnxsrv1.company.com lnxsrv1
...
```

Written to `inventory/hosts.generated` — copy/paste into your system
hosts file as you already do today. Not applied automatically (editing
`/etc/hosts` or `C:\Windows\System32\drivers\etc\hosts` needs elevated
permissions and is squarely something you should review before
accepting), just generated so there's nothing to hand-transcribe from
the OpenTofu output.
