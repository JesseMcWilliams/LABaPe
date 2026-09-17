# Installing and Configuring OpenTofu (Windows Host + Hyper-V + WSL2)

Architecture: **one physical Windows box** runs both Hyper-V (the
hypervisor) and WSL2 (the control machine, DESIGN.md §15). This is a
different shape from docs/install-opentofu.md (a dedicated Debian
control machine talking to a separate libvirt host) but the same
underlying idea — OpenTofu and this repo's bash/Python scripts need a
Linux environment, and WSL2 provides it on the same box that's also the
hypervisor.

**Where this repo actually stands**: `tofu/backends/hyperv` isn't
implemented yet (DESIGN.md §18 schedules it for M2 — only
`tofu/backends/libvirt` exists today). This guide covers what's usable
right now regardless (WSL2 itself, OpenTofu inside it) plus the
Hyper-V-host-side WinRM setup that `tofu/backends/hyperv` will need
once it lands, so that part of the work doesn't have to be redone later.

## 1. Enable Windows features

From an elevated PowerShell prompt:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
wsl --install -d Debian
```

These don't conflict — WSL2 itself runs as a lightweight VM under
Hyper-V/the Virtual Machine Platform, so having "real" Hyper-V enabled
alongside WSL2 is the normal, expected configuration, not a resource
fight between two competing hypervisors.

Reboot when prompted, then finish the Debian setup (it prompts for a
UNIX username/password on first launch).

## 2. Install OpenTofu inside WSL

Once inside the WSL Debian prompt, this is **identical** to a native
Debian box — follow [docs/install-opentofu.md](./install-opentofu.md)
§§1–3 (APT repo or standalone installer, verify with `tofu version`,
set up the provider plugin cache). Nothing WSL-specific there.

Skip that guide's §4–§7 (libvirt client tools, `qemu+ssh://` setup) —
this box's target is Hyper-V, not libvirt.

## 3. Configure WinRM on the Windows/Hyper-V host

This is the part that's genuinely different. The `taliesins/hyperv`
OpenTofu provider talks to the Hyper-V host over WinRM — from an
elevated PowerShell prompt **on Windows, not inside WSL**:

```powershell
# Core WinRM + PS Remoting setup (from the provider's own setup docs)
Enable-PSRemoting -SkipNetworkProfileCheck -Force
Set-WSManInstance WinRM/Config/WinRS -ValueSet @{MaxMemoryPerShellMB = 1024}
Set-WSManInstance WinRM/Config -ValueSet @{MaxTimeoutms = 1800000}
Set-WSManInstance WinRM/Config/Client -ValueSet @{TrustedHosts = "*"}
Set-WSManInstance WinRM/Config/Service/Auth -ValueSet @{Negotiate = $true}
```

### HTTPS listener (recommended over plain HTTP)

A self-signed certificate is fine here — this is a self-hosted lab, the
same reasoning docs/credentials.md §2 already applies to the libvirt
provider's `insecure = true`:

```powershell
$hostname = hostname
$cert = New-SelfSignedCertificate -DnsName $hostname -CertStoreLocation Cert:\LocalMachine\My
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
  -CertificateThumbPrint $cert.Thumbprint -Force

New-NetFirewallRule -DisplayName "WinRM HTTPS (WSL/OpenTofu)" -Name "WinRMHTTPSIn" `
  -Profile Any -LocalPort 5986 -Protocol TCP `
  -RemoteAddress 172.16.0.0/12 -Verbose
```

The `-RemoteAddress` scope above covers the private-range subnet WSL2's
NAT typically uses — check yours with `ip addr show eth0` **inside
WSL** first and adjust if it falls outside `172.16.0.0/12` (docs/
credentials.md §7's firewall-scoping principle applied to the
Hyper-V host itself, which is more sensitive than a throwaway lab VM).

### Enable Basic auth if you're not using a domain

Since the Hyper-V host is likely a workgroup machine (not domain-joined
— it's the hypervisor itself), NTLM/Negotiate (already enabled above)
is normally enough for a non-domain client. If a connection attempt
reports an auth failure, Basic auth (over the HTTPS listener only —
never enable it on plain HTTP) is the fallback:

```powershell
winrm set winrm/config/service/auth '@{Basic="true"}'
```

## 4. Find the right IP to reach the Windows host from WSL

**Don't use `localhost`/`127.0.0.1`** from inside WSL to reach the
Windows host — under WSL2's default NAT networking mode, `localhost`
inside WSL means WSL's own loopback, not the Windows host's. (WSL's
newer "mirrored" networking mode claims to change this, but has
documented connectivity bugs as of this writing — not worth adopting
just for this.)

Use the Windows host's real LAN IP instead — from Windows:

```powershell
ipconfig
# Look for your actual Ethernet/Wi-Fi adapter, NOT
# "vEthernet (WSL)" or any Hyper-V virtual switch adapter
```

That IP is what goes in `secrets.vault.yml` as `hyperv_host`
(docs/credentials.md §1), and what `New-NetFirewallRule` above should
actually be reachable on from WSL.

## 5. Test the WinRM connection before involving OpenTofu

Confirm connectivity from WSL directly — `scripts/test/test-winrm-connectivity.py`
(docs/validate-setup.md) does exactly this:

```bash
# from inside WSL
python3 scripts/test/test-winrm-connectivity.py <hyperv-host-ip> <username>
```

If that fails, fix it before touching OpenTofu — a provider-level error
message will be much less direct about which of these steps is wrong.

## 6. What's ready now vs. pending

- **Usable today**: OpenTofu itself, `tofu/backends/libvirt` (if you
  also have a separate/nested libvirt target), and everything in §§1–5
  above regardless of backend.
- **Pending M2** (DESIGN.md §18): `tofu/backends/hyperv` and
  `tofu/modules/vm/hyperv` / `tofu/modules/network/hyperv` don't exist
  yet. Once they land, they'll consume the same `hyperv_host`/
  `hyperv_user`/`hyperv_password` vault keys this guide's WinRM setup
  prepares.

## 7. Troubleshooting

- **`Enable-PSRemoting` fails on a "public" network profile** — that's
  what `-SkipNetworkProfileCheck` above is for; without it, Windows
  refuses on networks it considers public.
- **Connection times out from WSL but works from a Windows PowerShell
  prompt on the same machine** — almost always the firewall rule's
  `-RemoteAddress` scope (§3) not covering WSL's actual subnet, or the
  IP used (§4) being a WSL-internal address instead of the host's real
  LAN IP.
- **`wsl --shutdown` then restart** — if WSL's networking gets into a
  bad state (stale routes after the host's IP changes, e.g. DHCP
  renewal), a full WSL shutdown/restart from PowerShell
  (`wsl --shutdown`, then reopen the Debian terminal) resets it more
  reliably than restarting just the distro.
- **Certificate errors from the provider** — expected with a
  self-signed cert; the provider config's `insecure = true` (once
  `tofu/backends/hyperv` exists) is what accepts that, matching the
  reasoning docs/credentials.md §2 already documents for this exact
  situation.
