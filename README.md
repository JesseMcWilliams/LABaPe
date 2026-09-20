# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on either Hyper-V or KVM (Rocky/Debian),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [DESIGN.md](./DESIGN.md) for scope, architecture, and open decisions,
and `docs/` for the detailed design of base images, networking,
credentials, the software manifest, and directory objects.

## Status

M1 is implemented and has passed a real end-to-end smoke test: the
libvirt backend, Linux-only, bridged networking, direct-ISO-boot only
(DESIGN.md §18). `scripts/deploy.sh libvirt lab1 small` was run against
a real Debian 13 libvirt host — two Rocky 9 VMs built from a real ISO
via kickstart, bridged onto the physical LAN, bootstrapped over SSH,
and configured by `ansible-playbook` (software manifest packages
installed, EPEL repo added) — with 0 Ansible failures on the final run.
See **Known gaps** below for what M1 still doesn't cover.

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

- **The libvirt/Linux/bridged/iso_direct path (M1) has been run
  end-to-end against real infrastructure** — see Status above. Getting
  there surfaced and fixed several real bugs the original scaffold
  couldn't have caught without a real `tofu apply`/`virt-install`/
  kickstart run: an invalid OpenTofu precondition, `virt-install`
  called with an incompatible flag combination, a libvirt-internal race
  when creating multiple VMs concurrently, a kickstart `network` line
  using an option this Anaconda version doesn't accept (silently left
  installs hanging indefinitely rather than failing fast), no DNS
  resolver for static addressing, a missing EPEL repo dependency, a
  timing race between a VM finishing its post-install reboot and
  Ansible's first connection attempt, and a pre-flight network check
  that couldn't tell its own already-running VMs apart from a real
  address conflict. None of this was reachable by static review alone.
- DHCP-mode addressing, the Hyper-V backend, Windows hosts, domain
  services, the full OS matrix, Packer templates, and the software/
  directory manifests beyond the simple package-manager case are all
  out of scope for M1 — see DESIGN.md §18 for the milestone plan.
