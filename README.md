# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on either Hyper-V or KVM (Rocky/Debian),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [Claude_Docs/Design_System-Overview.md](./Claude_Docs/Design_System-Overview.md)
for scope, architecture, and open decisions (milestone-by-milestone status is
in §18); `Claude_Docs/` for the detailed design of base images, networking,
credentials, the software manifest, directory objects, and future work (a
`certificate_authority` role and a web interface for composable environment
templates — §19/§20, not yet implemented); and
[Claude_Docs/Testing_Troubleshooting-Log.md](./Claude_Docs/Testing_Troubleshooting-Log.md)
for the detailed, blow-by-blow history behind every bug found along the way.

## Status

- **M1 (libvirt/KVM, Linux + Windows)** — done, end-to-end, confirmed against real infrastructure. **Recommended backend.**
- **M2 (Hyper-V, Linux)** — done, end-to-end, but needs more operational care (a manual boot-order fix after every `tofu apply`) — prefer libvirt/KVM when it's an option.
- **M4 (domain services)** — done, end-to-end: AD DS promotion, Windows + Linux domain join, software install, one playbook run.
- **M5 (directory objects half)** — done, end-to-end: OUs, domain/local groups and users, both membership directions.
- **M5 (workstation host types half)** — Windows 11 and Windows 10 clients done, end-to-end (unattended install, domain join, `site.yml`); Debian 13 installs fully unattended; Ubuntu LTS (24.04 and 26.04) still blocked by one open storage bug.

Full milestone plan and per-milestone status notes: `Claude_Docs/Design_System-Overview.md` §18.

## M1 quickstart (libvirt backend)

Prerequisites, none of which this repo automates yet:

1. A libvirt host (Rocky/Debian) reachable via `qemu+ssh://` from your
   control machine, with a bridge device already set up
   (`Claude_Docs/Reference_Networking.md` §1 — e.g. `nmcli connection add type bridge
   ifname br0`, physical NIC enslaved to it).
2. A Rocky 9 ISO staged on that host's filesystem (path goes in
   `environment.yml`'s `os_iso_paths`, `Claude_Docs/Design_Base-Images.md`).
3. `virt-install`/`virsh` reachable from wherever OpenTofu runs against
   that same `qemu+ssh://` URI (tofu/modules/vm/libvirt shells out to
   `virt-install` directly — see that module's comments for why).
4. OpenTofu, Ansible, and Python 3 with PyYAML on the control machine —
   [User_Docs/Install-OpenTofu.md](./User_Docs/Install-OpenTofu.md) and
   [User_Docs/Install-Ansible.md](./User_Docs/Install-Ansible.md) for a dedicated
   Debian 13 control machine, or
   [User_Docs/Install-OpenTofu-WSL.md](./User_Docs/Install-OpenTofu-WSL.md) /
   [User_Docs/Install-Ansible-WSL.md](./User_Docs/Install-Ansible-WSL.md)
   if the control machine is WSL2 on the same Windows/Hyper-V box.
5. An SSH keypair for the Ansible bootstrap user (`Claude_Docs/Reference_Credentials.md` §5)
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
`Claude_Docs/Design_System-Overview.md` §6.3) — pick anything; running the same
name again re-applies against that same environment instead of creating a new one.

## Validating your setup

Before the first real `scripts/deploy.sh` run, or any time something's
not working and it's unclear whether it's this repo or the underlying
tooling: [Claude_Docs/Reference_Validate-Setup.md](./Claude_Docs/Reference_Validate-Setup.md) plus

```
scripts/test/run-all.sh
```

which check that OpenTofu/Ansible are actually installed correctly, the
playbook/HCL actually parse, and (whichever backend you're using) the
libvirt or WinRM connection actually works — independently of running a
full deploy.

## Known gaps

- DHCP-mode addressing, the full OS matrix, and Packer templates are all still out of scope — `Claude_Docs/Design_System-Overview.md` §18 has the milestone plan.
- Hyper-V's `scripts/set-boot-order.sh` must currently be run by hand after every `tofu apply` — see `Claude_Docs/Testing_Troubleshooting-Log.md` § M2.
- Ubuntu LTS workstation support (24.04 and 26.04) has one open bug (guided storage/LUKS) blocking a full unattended install — see `Claude_Docs/Testing_Troubleshooting-Log.md` § M5 (workstation support, Ubuntu LTS half). Use `debian_latest` for a Linux workstation meanwhile.
- Windows 11 hosts need `disk_gb` of at least 64 (the plan fails otherwise), and Windows VMs run on legacy BIOS, so there's no Secure Boot/TPM testing.

Full bug-by-bug history for every milestone: `Claude_Docs/Testing_Troubleshooting-Log.md`.
