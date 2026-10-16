# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on KVM (libvirt; Rocky/Debian/Ubuntu and Windows guests). Hyper-V is being dropped
(see below),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [Claude_Docs/Design_System-Overview.md](./Claude_Docs/Design_System-Overview.md)
for scope, architecture, and open decisions (milestone-by-milestone status is
in §18); `Claude_Docs/` for the detailed design of base images, networking,
credentials, the software manifest, directory objects, the web interface
(`Claude_Docs/Planning_Web-Interface-Design.md`), and future work (a
`certificate_authority` role and composable environment templates —
§19/§20); and
[Claude_Docs/Testing_Troubleshooting-Log.md](./Claude_Docs/Testing_Troubleshooting-Log.md)
for the detailed, blow-by-blow history behind every bug found along the way.

## License

LABaPe is free software under the **GNU Affero General Public License, version 3** (`AGPL-3.0-only`; the full
text is in [LICENSE](./LICENSE)). In short: you may use, study, change and share it, including commercially; if you
share a changed version, or let others use one over a network, you must offer them its source under the same
license. The web interface links to the source (the line in `web/src/App.tsx`; a modified copy points it at its
own). The third-party software LABaPe uses has its own licenses: see
[Claude_Docs/Reference_SBOM.md](./Claude_Docs/Reference_SBOM.md).

## Status

- **M1 (libvirt/KVM, Linux + Windows)** — done, end-to-end, confirmed against real infrastructure. **Recommended backend.**
- **M2 (Hyper-V, Linux)** — done end to end, but **Hyper-V support will be dropped** (decision 2026-10-16): use libvirt/KVM.
- **M4 (domain services)** — done, end-to-end: AD DS promotion, Windows + Linux domain join, software install, one playbook run.
- **M5 (directory objects half)** — done, end-to-end: OUs, domain/local groups and users, both membership directions.
- **M5 (workstation host types half)** — Windows 11/10, Ubuntu 24.04/26.04 and Debian 13 workstations install fully unattended and domain-join (`windows_workstation` and `linux_workstation`).
- **M6 (base images)** — done: templates on libvirt, built with Packer (`scripts/build-template.sh`) or promoted from an ISO-built VM (`scripts/promote-to-template.sh`), cloned with `image_source = "packer_template"`, refreshed with `scripts/refresh-template.sh`. Verified for Rocky 9, Ubuntu 24.04/26.04, Debian 13, Windows Server 2019/2022/2025, Windows 10 and 11. ISO installs stay fully supported.
- **M10 (web interface), phase 10a**: a container stack ([`container/`](container/README.md)) with:
  - Authentik (OIDC) sign-in, roles from Authentik groups, and break-glass emergency access;
  - KVM host registration;
  - environment deploy and destroy from the browser, with live job logs, cancel and retry;
  - an audit log, and OpenTofu state in PostgreSQL.

  Verified under Docker on a local KVM host. Later phases are in `Claude_Docs/Planning_Web-Interface-Design.md` §17.
- **M10, usability and admin round (2026-10-11)**:
  - host names per host group, a template picker filtered by OS, and resource warnings for the KVM host;
  - a Files tab: resumable uploads, ISOs, installers, the template library, and build, refresh and promote jobs;
  - a catalog of services, applications and packages, with requests, YAML import/export, and choices per host group;
  - a Settings page: file locations, daily database backups, history retention, syslog forwarding of the audit trail, SMTP job emails, and an Authentik group search;
  - clear field errors, an admin Errors page, and masked credentials.

  Verified on the lab host. Design: `Claude_Docs/Planning_Web-Interface-Design.md` §21.
- **M10, round 3 (2026-10-11)**: a dashboard with host and environment load; list pages that open each entry's own page; branding; downloads from a URL with hash checks; installer profiles and remote KVM hosts (`qemu+ssh`); a platform CA (step-ca) that gives every VM a renewing certificate. Design: §22.
- **M10, operations (2026-10-11/12)**: tooltips and a confirmation for every change; database migrations; maintenance mode with a weekly window; updates from the browser through a host agent (signed releases only, one-step and automatic rollback); scheduled secret rotation for every internal secret, plus each environment's Windows Administrator and domain Administrator passwords; a configurable Linux admin account. Design: §23 and §24.
- **M10, external secret stores (2026-10-12)**: OpenBao/HashiCorp Vault and CyberArk Conjur alongside the built-in store; each secret names its store and is moved between stores from the Secrets page; LABaPe rotates its own logins to them. Both also run in the stack (`container/stores/`). Design: §25.
- **M10, OpenBao and Conjur in the stack (2026-10-12)**: both run in the LABaPe stack (installer step `Stack.Stores`); LABaPe's own secrets (vault password, VM key, master key, database and CA passwords) live in OpenBao, the command line reads them from there, and the stack refills them from OpenBao at boot. Design: §26.
- **Networks and IP addresses**: a network catalog in `environment.yml` and the web UI controls which networks and address ranges may be used, with optional limits by role or group. Each KVM host lists the networks it carries and on which bridge. Each host group picks a network and static or DHCP addressing. Static addresses are allocated automatically in the web UI, and DHCP addresses are found by MAC after boot. Verified on libvirt with both the CLI and the web UI; Hyper-V still uses one static network. Design: `Claude_Docs/Planning_Web-Interface-Design.md` §20.

Full milestone plan and per-milestone status notes: `Claude_Docs/Design_System-Overview.md` §18.

## Installing from scratch

`sudo deploy/install-labape.py` sets up a Debian KVM host end to end. It runs as named, resumable steps (`--list-steps`, `--resume`, `--step`, `--dry-run`) and saves every answer to a reusable answers file, never a password. See [`deploy/README.md`](deploy/README.md). The manual quickstart below remains the reference for what it automates.

## User guides

- [User_Docs/Web-Interface-Guide.md](User_Docs/Web-Interface-Guide.md): signing in, creating and managing environments, jobs, files and templates, the catalog.
- [User_Docs/Web-Interface-Admin.md](User_Docs/Web-Interface-Admin.md): installing, hosts, networks, settings, maintenance, secrets, rotation and external secret stores, updates.
- [User_Docs/Command-Line.md](User_Docs/Command-Line.md): `deploy.sh`/`destroy.sh`, the network catalog, templates, DHCP addresses and passwords without the web interface.

## M1 quickstart (libvirt backend)

Prerequisites (on a Debian host, `deploy/install-labape.py` sets these up; by hand otherwise):

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

cp tofu/environment.example.yml tofu/environment.yml       # fill in networks:, os_iso_paths
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

Throwaway test environment alongside a long-lived one (own inventory
directory; `--test` needs a `test-*` name and its own host-group names):

```
scripts/deploy.sh libvirt test-win11 test-win11 --test [--no-ansible] [--env-file F] [--directory-manifest F]
scripts/destroy.sh libvirt test-win11 test-win11 --test
```

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

- Hyper-V will be dropped (question 175), so these three notes won't be fixed. The Hyper-V backend still uses one static network: no network catalog or DHCP addressing (`Claude_Docs/Reference_Backend-Parity.md`). Remaining milestones: `Claude_Docs/Design_System-Overview.md` §18.
- Hyper-V's `scripts/set-boot-order.sh` must currently be run by hand after every `tofu apply` — see `Claude_Docs/Archive_Testing_Troubleshooting-Log-M1-M6.md` § M2.
- Hosts in different environments on the same libvirt host need unique host-group names: VM names are host-global, and a create refuses a VM tagged for another workspace (rendered answer files are already kept per workspace) — see `Claude_Docs/Archive_Testing_Troubleshooting-Log-M1-M6.md` § M5 (completion pass).
- Windows 11 hosts need `disk_gb` of at least 64 (the plan fails otherwise), and Windows VMs run on legacy BIOS, so there's no Secure Boot/TPM testing ([operating system notes](User_Docs/Web-Interface-Guide.md#6-operating-system-notes)).
- Windows Server installs the Desktop Experience unless a host group sets `windows_core = true` (or a template is built with `--core`); Firefox doesn't install on Server 2019 Core (on Debian it installs as `firefox-esr`).
- Updates from the web interface accept only release tags signed by a key in `deploy/allowed_signers` (`deploy/README.md` § Updates); the maintainer's key is listed there since 2026-10-12, and no signed release exists yet.
- The Hyper-V backend's `tofu init` fails: its `taliesins/hyperv` provider isn't in the OpenTofu registry.
- Environments on remote KVM hosts aren't included in VM-key pushes or password rotation, because the web stack can't reach their VMs (`Claude_Docs/Planning_Web-Interface-Design.md` §24.3).

Full bug-by-bug history: `Claude_Docs/Testing_Troubleshooting-Log.md` (current work and open findings) and `Claude_Docs/Archive_Testing_Troubleshooting-Log-M1-M6.md` (M1-M6).
