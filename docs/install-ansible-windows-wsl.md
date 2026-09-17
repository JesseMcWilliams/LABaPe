# Installing and Configuring Ansible (Windows Host + Hyper-V + WSL2)

Same control-machine role as docs/install-ansible.md (Ansible doesn't
run as a control node on Windows, DESIGN.md §15) — it just runs inside
WSL2 on the same physical box as Hyper-V, instead of on a separate
Debian machine.

## 1. Install Ansible

Inside your WSL Debian prompt, this is **identical** to a native Debian
box — follow [docs/install-ansible.md](./install-ansible.md) in full
(pipx, `ansible-core`, `pywinrm`, the four collections, vault password
file, bootstrap SSH keypair, `ansible.cfg`). Nothing in that guide
assumes a specific kind of Debian install versus a WSL one; every
command is the same.

## 2. What's different about WSL specifically

**Reaching lab VMs (once Hyper-V VMs exist, M3+)**: no special
configuration needed. Bridged networking (DESIGN.md §14, the default)
puts lab VMs on the real physical LAN via an External switch — from
WSL's point of view that's just another LAN address, reachable the same
way any outbound connection from WSL reaches the LAN today. The
WSL-specific wrinkle in docs/install-opentofu-windows-wsl.md §4
(don't use `localhost`) is specifically about reaching a service
running *on the Windows host itself* (WinRM to the Hyper-V host) — it
doesn't apply to VMs that Hyper-V creates on the bridged network, since
those aren't "the Windows host," they're ordinary LAN peers.

**Reaching the Hyper-V host itself**, if a future role ever needs to
(nothing in the current design does — domain join and software install
target the VMs, not the hypervisor) — same guidance as
docs/install-opentofu-windows-wsl.md §4: use the host's real LAN IP,
not `localhost`.

## 3. Verify pywinrm reaches a Windows target (M3+, forward-looking)

Once `tofu/backends/hyperv` and Windows VM support exist, the same
connectivity test used for the Hyper-V host itself
(`scripts/test/test-winrm-connectivity.py`, docs/validate-setup.md)
works identically against any Windows VM's IP — WinRM is WinRM,
regardless of whether the target is the hypervisor or a guest.

## 4. Troubleshooting

- **DNS resolution fails inside WSL after switching networks (e.g.
  laptop moves from office to home Wi-Fi)** — WSL2 sometimes caches a
  stale resolver config; `wsl --shutdown` from PowerShell, then reopen
  the Debian terminal, regenerates it.
- **Everything else** — see docs/install-ansible.md §9; none of those
  failure modes are WSL-specific.
