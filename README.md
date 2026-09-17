# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on either Hyper-V or KVM (Rocky/Debian),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [DESIGN.md](./DESIGN.md) for scope, architecture, and open decisions,
and `docs/` for the detailed design of base images, networking,
credentials, the software manifest, and directory objects.

## Status

M1 scaffolding is in place: the libvirt backend, Linux-only, bridged
networking, direct-ISO-boot only (DESIGN.md §18). Nothing here has been
run against real infrastructure yet — see **Known gaps** below before
trusting it.

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
   [docs/install-ansible.md](./docs/install-ansible.md) if you're
   setting these up fresh on Debian 13.
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

## Known gaps in this scaffold

- **Nothing here has been run against a real libvirt host or a real
  Ansible run.** The OpenTofu HCL passed a manual brace-balance/logic
  review and the Python/bash helper scripts were smoke-tested with
  synthetic inputs, but `tofu validate`/`tofu plan` have not been run
  (no OpenTofu binary available in the environment this was written
  in — network policy blocked fetching one). Treat this as a first
  real test candidate, not as verified-working code.
- DHCP-mode addressing, the Hyper-V backend, Windows hosts, domain
  services, the full OS matrix, Packer templates, and the software/
  directory manifests beyond the simple package-manager case are all
  out of scope for M1 — see DESIGN.md §18 for the milestone plan.
