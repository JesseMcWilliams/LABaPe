# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on either Hyper-V or KVM (Rocky/Debian),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [DESIGN.md](./DESIGN.md) for scope, architecture, and open decisions,
`docs/` for the detailed design of base images, networking, credentials,
the software manifest, directory objects, and future work (a
`certificate_authority` role and a web interface for composable
environment templates — DESIGN.md §19/§20, not yet implemented), and
[docs/troubleshooting-log.md](./docs/troubleshooting-log.md) for the
detailed, blow-by-blow history behind every bug mentioned below.

## Status

**M1 (libvirt/KVM backend, Linux + Windows) works end-to-end**,
confirmed against real infrastructure: Linux via kickstart, Windows
Server 2019/2022/2025 via `autounattend.xml`, both bootstrapped over
SSH/WinRM and configured by Ansible. **Recommended backend** — see
below.

**M2 (Hyper-V backend, Linux only) also works end-to-end** as of the
latest fixes: `tofu apply` creates a network switch, VHD, and VM; the
VM completes an unattended Rocky 9 kickstart install; reboots into the
freshly installed OS; and is reachable over SSH. Getting here took
tracking down a Hyper-V-specific BIOS default that made completed
installs loop forever, plus a `taliesins/hyperv` provider crash — see
Known Gaps.

**Recommendation: prefer the libvirt/KVM backend.** M2 needed several
real, hard-to-diagnose workarounds (a Hyper-V BIOS boot-order default,
multiple distinct provider crashes, WinRM's general fragility vs. SSH)
to reach the same end-to-end state M1 got to more directly. Use Hyper-V
when KVM genuinely isn't an option, and budget more operational care —
in particular, `scripts/set-boot-order.sh` must currently be run by
hand after every `tofu apply` (Known Gaps).

**M4 (domain services) works end-to-end**, confirmed against real
infrastructure on the libvirt backend: a single, unmodified
`ansible-playbook site.yml` run takes bare VMs through AD DS promotion
(`domain_controller` role), Windows domain join
(`microsoft.ad.membership`), Linux domain join (`realmd`/`sssd`), and
software install, with zero failures. Getting there needed two
non-obvious fixes: a `become: true` bug on Windows WinRM plays, and
domain-join credentials that turned out to need *opposite* formats per
platform (Linux: bare username; Windows: NetBIOS-qualified,
`DOMAIN\user`) — see Known Gaps.

## M1 quickstart (libvirt backend)

Prerequisites, none of which this repo automates yet:

1. A libvirt host (Rocky/Debian) reachable via `qemu+ssh://` from your
   control machine, with a bridge device already set up
   (docs/networking.md §1 — e.g. `nmcli connection add type bridge
   ifname br0`, physical NIC enslaved to it).
2. A Rocky 9 ISO staged on that host's filesystem (path goes in
   `environment.yml`'s `os_iso_paths`, docs/base-images.md).
3. `virt-install`/`virsh` reachable from wherever OpenTofu runs against
   that same `qemu+ssh://` URI (tofu/modules/vm/libvirt shells out to
   `virt-install` directly — see that module's comments for why).
4. OpenTofu, Ansible, and Python 3 with PyYAML on the control machine —
   [docs/install-opentofu.md](./docs/install-opentofu.md) and
   [docs/install-ansible.md](./docs/install-ansible.md) for a dedicated
   Debian 13 control machine, or
   [docs/install-opentofu-windows-wsl.md](./docs/install-opentofu-windows-wsl.md) /
   [docs/install-ansible-windows-wsl.md](./docs/install-ansible-windows-wsl.md)
   if the control machine is WSL2 on the same Windows/Hyper-V box.
5. An SSH keypair for the Ansible bootstrap user (docs/credentials.md §5)
   — `ssh-keygen -f ~/.ssh/labape_bootstrap`.

Setup:

```
cp secrets.vault.example.yml secrets.vault.yml     # fill in libvirt_uri,
                                                    # ansible_ssh_private_key_path
ansible-vault encrypt secrets.vault.yml
echo "your-vault-password" > ~/.labape-vault-pass  # or set LABAPE_VAULT_PASS_FILE

cp tofu/environment.example.yml tofu/environment.yml       # fill in network.*, os_iso_paths
cp tofu/environments/small.tfvars.example tofu/environments/small.tfvars
cp ansible/software-manifest.example.yml ansible/software-manifest.yml
```

Deploy / destroy:

```
scripts/deploy.sh libvirt lab1 small
scripts/destroy.sh libvirt lab1 small
```

`lab1` is the environment instance name (an OpenTofu workspace,
DESIGN.md §6.3) — pick anything; running the same name again re-applies
against that same environment instead of creating a new one.

## Validating your setup

Before the first real `scripts/deploy.sh` run, or any time something's
not working and it's unclear whether it's this repo or the underlying
tooling: [docs/validate-setup.md](./docs/validate-setup.md) plus
`scripts/test/run-all.sh` check that OpenTofu/Ansible are actually
installed correctly, the playbook/HCL actually parse, and (whichever
backend you're using) the libvirt or WinRM connection actually works —
independently of running a full deploy.

```
scripts/test/run-all.sh
```

## Known gaps in this scaffold

- **M1 (libvirt/Linux)** works end-to-end; getting there fixed several
  real bugs static review couldn't have caught (an invalid OpenTofu
  precondition, a `virt-install` flag incompatibility, a libvirt race
  on concurrent VM creation, a kickstart option this Anaconda version
  silently hangs on, missing DNS/EPEL, a post-reboot timing race, and a
  pre-flight network check confused by its own VMs). Full details:
  [troubleshooting-log.md § M1](./docs/troubleshooting-log.md#m1-libvirt-bugs-found-getting-the-first-end-to-end-run-working).
- **M1 (libvirt/Windows)** works end-to-end (2019/2022/2025, ahead of
  DESIGN.md §18's original M3 schedule) after resolving a long-standing
  intermittent install failure: Windows Setup's XML deserializer chokes
  on multi-line comments in the answer file, so `create-iso-direct.sh`
  now strips them from the copy Setup actually reads. Ten rounds of
  investigation, most of them ruling out plausible-looking dead ends,
  are kept in full at
  [troubleshooting-log.md § Windows answer-file deserialization failure](./docs/troubleshooting-log.md#windows-answer-file-deserialization-failure-libvirt-backend).
- **M2 (Hyper-V)** now works end-to-end. The long "SSH unreachable"/
  "install stalled" mystery turned out to be neither: Generation 1
  Hyper-V VMs default their BIOS boot order to DVD before hard disk, so
  every completed install just rebooted straight back into itself,
  forever — indistinguishable from a stall without a live console.
  Fixed with `Set-VMBios -StartupOrder` (`scripts/set-boot-order.sh`),
  which currently has to be run **by hand** after every `tofu apply` —
  wiring it in as a Terraform resource hit a reproducible
  `taliesins/hyperv` provider crash, root-caused and worked around
  separately (pinning `dvd_drives.resource_pool_name` and an undeclared
  `vm_processor` block that were causing permanent drift). A
  debug-disk diagnostic aid (`var.debug_disk`, off by default) was
  built and then shelved along the way. Full investigation, including
  the three logging/capture channels that turned out to be chasing a
  false premise:
  [troubleshooting-log.md § M2](./docs/troubleshooting-log.md#m2-hyper-v-the-ssh-unreachable--install-stalled-investigation).
- **M4 (domain services)** now works end-to-end: AD DS promotion, both
  platforms' domain join, confirmed with a real forest and real joins
  on the libvirt backend. Two bugs found along the way: a leftover
  `become: true` on Windows WinRM plays (broke the instant the
  previously-placeholder `domain_controller`/`domain_directory` roles
  ran for real), and domain-join credentials needing *opposite* formats
  per platform — Linux's `realm join` wants a bare username, Windows'
  `Add-Computer`/`microsoft.ad.membership` wants a NetBIOS-qualified one
  (`DOMAIN\user`) — each confirmed by reproducing the failure outside
  Ansible entirely to rule out a module bug. Full investigation:
  [troubleshooting-log.md § M4](./docs/troubleshooting-log.md#m4-domain-services-ad-ds-promotion-and-domain-join-bugs).
- DHCP-mode addressing, the full OS matrix, Packer templates, and the
  software/directory manifests beyond the simple package-manager case
  are all out of scope for M1/M2/M4 — see DESIGN.md §18 for the
  milestone plan.
