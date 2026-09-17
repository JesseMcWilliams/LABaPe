# LABaPe Design Document

## 1. Purpose

Automate the provisioning and configuration of ephemeral test/lab
environments made up of a mix of Windows and Linux servers and
workstations, self-hosted on either **Hyper-V** or **KVM (Rocky/Debian)**,
using **OpenTofu** for infrastructure provisioning and **Ansible** for
OS configuration and software installation.

## 2. Goals

- Declarative environment **profiles** (small, medium, custom) defining
  VM counts, roles, and OS types.
- Support both Hyper-V and libvirt/KVM backends behind the same
  higher-level environment definition — switching hosts shouldn't mean
  rewriting the environment.
- Per-run, variable **software manifests** per host group (the software
  list is not fixed and changes between runs).
- Idempotent, repeatable builds; environments can be torn down and
  rebuilt on demand.
- Minimal manual steps: one command (or a short pipeline) from
  "profile + software manifest" to "environment ready."

## 3. Non-Goals (v1)

- Running both hypervisor backends simultaneously for one environment
  (pick one backend per apply).
- Public cloud providers (AWS/Azure/GCP) — self-hosted only.
- Active Directory domain automation (candidate for a later milestone).
- Ongoing lifecycle/patch management beyond initial provisioning.

## 4. High-Level Architecture

Two-stage pipeline:

1. **Provisioning (OpenTofu)** — creates VMs against the chosen
   hypervisor provider and emits a dynamic Ansible inventory (grouped by
   role: `windows_server`, `linux_server`, `windows_workstation`,
   `linux_workstation`).
2. **Configuration (Ansible)** — installs software and applies base
   configuration, using per-group and per-run variables, over WinRM
   (Windows) and SSH (Linux).

```
 profile (small/medium/custom)      software manifest (per run)
              │                               │
              ▼                               │
        OpenTofu apply                        │
   (hyperv or libvirt provider)               │
              │                               │
              ▼                               │
      generated inventory ────────────────────┘
              │
              ▼
        Ansible playbook run
   (windows_common / linux_common roles)
              │
              ▼
        Ready environment
```

## 5. Hypervisor Backends

| Backend | Host OS | Provider |
|---|---|---|
| Hyper-V | Windows Server (Hyper-V role) | `taliesins/hyperv` (community, via WinRM to the Hyper-V host) |
| KVM/libvirt | Rocky Linux / Debian | `dmacvicar/libvirt` |

Backend-specific details live behind a common module interface
(`modules/vm`) so an environment definition (profile + manifest) doesn't
change based on which backend is selected — only which backend module is
invoked does.

## 6. Base Images

Both backends need golden/template images per OS (Windows Server,
Windows workstation, Rocky, Debian) with WinRM/SSH pre-enabled for
Ansible to reach them.

Recommended: build these with **Packer** (cloud-init for Linux,
sysprep/autounattend for Windows) so images are reproducible and
versioned rather than hand-built.

**Open question** — see §14.1.

## 7. Environment Profiles

Profiles are data, not code:

```yaml
profiles:
  small:
    windows_server: 2
    linux_server: 2
  medium:
    windows_server: 3
    linux_server: 3
    windows_workstation: 2
    linux_workstation: 2
```

A custom profile is just a different set of counts passed the same way
— no separate code path.

## 8. Inventory & Software Manifest

- OpenTofu outputs a dynamic Ansible inventory grouping hosts by role.
- The software list is **not** baked into the roles. It's supplied as a
  per-run manifest (e.g. `software-manifest.yml`) consumed by generic
  roles (`windows_common`, `linux_common`) that loop over a variable
  package list using `chocolatey` (Windows) and `apt`/`dnf` (Linux)
  modules — this avoids writing a new Ansible role every time the
  software list changes.

## 9. Proposed Repository Layout

```
LABaPe/
  README.md
  DESIGN.md
  tofu/
    modules/
      vm/               # common interface
        hyperv/
        libvirt/
    environments/
      small/
      medium/
    profiles.tfvars.example
  ansible/
    group_vars/
    roles/
      windows_common/
      linux_common/
    playbooks/
      site.yml
    software-manifest.example.yml
  packer/
    windows-server/
    windows-workstation/
    linux/
  scripts/
    deploy.sh            # tofu apply -> generate inventory -> ansible-playbook
    destroy.sh
```

## 10. Credentials & Secrets

WinRM credentials, SSH keys, and hypervisor host credentials must never
be committed. Baseline for v1: **Ansible Vault** for encrypted variables
plus `.gitignore`'d `*.tfvars` for OpenTofu secrets. Revisit only if a
heavier secrets tool is actually needed.

## 11. Networking

Static IP allocation per profile is recommended over DHCP, so the
generated inventory is deterministic and doesn't depend on a discovery
step. Final call depends on the target host's network setup — see
§14.3.

## 12. Workflow

```
tofu init && tofu workspace select <backend>
tofu apply -var-file=profiles/medium.tfvars
# inventory auto-generated from tofu output
ansible-playbook -i inventory/generated site.yml -e @software-manifest.yml
# ...
tofu destroy
```

Wrapped in `scripts/deploy.sh` / a `Makefile` (`make up PROFILE=medium`,
`make down`) so day-to-day use is one command.

## 13. Testing / Validation

- `tofu validate` and `tflint` for the provisioning code.
- `ansible-lint` for playbooks/roles.
- Molecule role testing is a stretch goal, not v1.

## 14. Open Questions

These are decisions only you can make — noted here so the design can
move forward with the rest while these settle:

1. **Base images**: build them ourselves with Packer, or do you already
   maintain Windows/Linux templates we should target instead?
2. **OS versions**: which Windows Server release, which Windows
   workstation release, and Rocky vs. Debian (or both) as the default
   Linux target?
3. **Networking**: static IPs per profile, or is DHCP + discovery
   acceptable on your network?
4. **Control machine**: will OpenTofu/Ansible run from a Linux box (or
   WSL), or does this need to run from Windows directly? (Ansible
   itself doesn't run natively on Windows as a control node.)
5. **Domain join / AD**: needed now, or purely a later milestone?
6. **Secrets**: is Ansible Vault sufficient, or do you want this to
   integrate with an existing vault/secrets manager?

## 15. Proposed Milestones

- **M1** — libvirt backend, small profile, Linux-only VMs, base Ansible
  config. End-to-end smoke test on one backend.
- **M2** — Hyper-V backend reaching parity with M1 for Linux VMs.
- **M3** — Windows VM support on both backends, WinRM bootstrap,
  `windows_common` role.
- **M4** — Workstation host type + medium profile.
- **M5** — Software manifest system finalized; Packer base images.
- **M6** — Secrets/vault integration, CI validation (`tflint`,
  `ansible-lint`), docs polish.
