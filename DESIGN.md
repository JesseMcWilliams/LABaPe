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
- Deploy a fresh **Active Directory domain** as part of every
  environment and join the other Windows (and, where supported, Linux)
  hosts to it automatically.
- Idempotent, repeatable builds; environments can be torn down and
  rebuilt on demand.
- Minimal manual steps: one command (or a short pipeline) from
  "profile + software manifest" to "environment ready."

## 3. Non-Goals (v1)

- Running both hypervisor backends simultaneously for one environment
  (pick one backend per apply).
- Public cloud providers (AWS/Azure/GCP) — self-hosted only.
- Multi-domain forests / trust relationships — a single domain per
  environment.
- Ongoing lifecycle/patch management beyond initial provisioning.

## 4. High-Level Architecture

Three-stage pipeline (domain services add an ordering dependency between
provisioning and general configuration):

1. **Provisioning (OpenTofu)** — creates VMs against the chosen
   hypervisor provider and emits a dynamic Ansible inventory (grouped by
   role: `domain_controller`, `windows_server`, `linux_server`,
   `windows_workstation`, `linux_workstation`).
2. **Directory services bring-up (Ansible, ordered)** — promotes the
   `domain_controller` host(s) first, since every other host's domain
   join depends on the domain and its DNS existing.
3. **Configuration (Ansible, remaining groups)** — joins the rest of the
   Windows and Linux hosts to the domain, then installs software and
   applies base configuration, over WinRM (Windows) and SSH (Linux).

```
 profile (small/medium/custom)      software manifest (per run)
              │                               │
              ▼                               │
        OpenTofu apply                        │
   (hyperv or libvirt provider)               │
              │                               │
              ▼                               │
      generated inventory ────────────────────┤
              │                               │
              ▼                               │
   Ansible: promote domain_controller(s)      │
              │                               │
              ▼                               │
   Ansible: join windows_*/linux_* to domain  │
              │                               │
              ▼                               │
   Ansible: install software, base config ────┘
              │
              ▼
        Ready environment
```

## 5. Supported Operating Systems

| Category | Targets |
|---|---|
| Windows Server | 2019, 2022, 2025 |
| Windows Client | 10, 11 |
| Linux (RHEL family) | Rocky Linux, RHEL, AlmaLinux *(proposed addition — confirm)* |
| Linux (Debian family) | Ubuntu (LTS), Debian, Linux Mint |

Notes:
- **RHEL** needs a Red Hat subscription (or the free Red Hat Developer
  subscription) to pull packages/updates during image build and on the
  running host (`subscription-manager register`) — Rocky and AlmaLinux
  don't need this, being unencumbered rebuilds.
- **Linux Mint** is Ubuntu-based (Mint Debian Edition is Debian-based) —
  it reuses the Ubuntu/Debian build pipeline rather than needing its own.
- Domain controllers require **Windows Server** — AD DS isn't available
  on Windows client or Linux.
- Other distros considered and deliberately left out unless you want
  them added: openSUSE/SLES, Fedora, Oracle Linux. Easy to add later
  since each Linux family already has a build pipeline to extend.

## 6. Hypervisor Backends

| Backend | Host OS | Provider |
|---|---|---|
| Hyper-V | Windows Server (Hyper-V role) | `taliesins/hyperv` (community, via WinRM to the Hyper-V host) |
| KVM/libvirt | Rocky Linux / Debian | `dmacvicar/libvirt` |

Backend-specific details live behind a common module interface
(`modules/vm`) so an environment definition (profile + manifest) doesn't
change based on which backend is selected — only which backend module is
invoked does.

## 7. Base Images

Two supported paths for getting a host to a usable base OS state, chosen
per host/profile rather than globally:

1. **Packer-built golden templates** (recommended default) — built once,
   cloned for every VM. Much faster per-environment provisioning.
2. **Direct ISO boot** — the hypervisor provider boots straight from the
   vendor ISO with an answer file, no pre-built template. Slower per
   `tofu apply` (a full OS install runs every time) but no image
   pipeline/template storage to maintain — useful for one-off or rarely
   used OS versions.

Both paths use the *same* answer files (autounattend.xml / kickstart /
cloud-init autoinstall), so they never drift from each other. Full
process detailed in [`docs/base-images.md`](./docs/base-images.md).

## 8. Directory Services (Domain Controller & Domain Join)

- The AD domain is deployed **as part of the environment**, not assumed
  to pre-exist. Every profile that wants domain join needs at least one
  `domain_controller` host (Windows Server only).
- **Ordering constraint**: the domain controller must be provisioned and
  promoted (`Install-ADDSForest`), with DNS answering, before any other
  host attempts to join. This is enforced in the Ansible run — the
  `domain_controller` group's play runs to completion before the
  domain-join tasks for `windows_server`, `windows_workstation`,
  `linux_server`, or `linux_workstation` run.
- **Windows join**: standard `Add-Computer -DomainName` against the
  freshly promoted domain.
- **Linux join**: `realmd`/`sssd` (works across both the RHEL and
  Debian families) rather than a distro-specific mechanism.
- Since environments are rebuilt from scratch, each rebuild creates a
  **brand-new AD forest** — no state carried over between environment
  lifecycles. Flagged as an assumption in §16 in case that's wrong.

## 9. Environment Profiles

Profiles are data, not code:

```yaml
profiles:
  small:
    domain_controller: 1
    windows_server: 2
    linux_server: 2
  medium:
    domain_controller: 1
    windows_server: 3
    linux_server: 3
    windows_workstation: 2
    linux_workstation: 2
```

A custom profile is just a different set of counts passed the same way
— no separate code path.

**Open question**: does `domain_controller` count against the
originally stated Windows Server total (e.g. small = 2 Windows Server
VMs total, one of which is the DC), or is it additional to it (e.g.
small = 2 member servers + 1 dedicated DC = 3 Windows Server VMs)? The
example above assumes *additional* — flagged in §16 to confirm.

## 10. Inventory & Software Manifest

- OpenTofu outputs a dynamic Ansible inventory grouping hosts by role.
- The software list is **not** baked into the roles. It's supplied as a
  per-run manifest (e.g. `software-manifest.yml`) consumed by generic
  roles (`windows_common`, `linux_common`) that loop over a variable
  package list using `chocolatey` (Windows) and `apt`/`dnf` (Linux)
  modules — this avoids writing a new Ansible role every time the
  software list changes.

## 11. Proposed Repository Layout

```
LABaPe/
  README.md
  DESIGN.md
  docs/
    base-images.md
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
      domain_controller/
      windows_common/
      linux_common/
    playbooks/
      site.yml          # ordered: domain_controller -> domain-join -> software
    software-manifest.example.yml
  packer/
    windows/
      2019/ 2022/ 2025/
    linux/
      rocky/ rhel/ almalinux/ ubuntu/ debian/ mint/
  iso/
    answer-files/        # shared by Packer builds and direct-ISO-boot path
      windows/
      rhel-family/
      debian-family/
  scripts/
    deploy.sh            # tofu apply -> generate inventory -> ansible-playbook
    destroy.sh
```

## 12. Credentials & Secrets

WinRM credentials, SSH keys, hypervisor host credentials, and the domain
admin/local administrator passwords must never be committed.

Ansible's options, from simplest to most integrated:

- **Ansible Vault** (built-in) — encrypts variables/files with a
  password, password file, or a **vault password script**: any external
  command that prints the password to stdout. That script hook is the
  integration point for sourcing the vault password from somewhere
  external (a password manager CLI, a self-hosted vault, an env var)
  instead of typing/storing it directly.
- **Lookup plugins that resolve secrets at run time**, so nothing
  sensitive is ever encrypted-and-committed at all:
  - `community.hashi_vault` — HashiCorp Vault (OSS, free, self-hostable)
    for dynamic secrets and an audit trail. Fits this project's
    self-hosted scope well if you want a central secrets store later.
  - `community.general.passwordstore` — the Unix `pass` manager.
  - `community.general.bitwarden` / `keyring` / `onepassword` — if you
    already use one of these.
  - `ansible.builtin.env` — pull from an environment variable.

**Answering directly**: yes, Ansible can call an external secrets
provider, either indirectly (vault password sourced from a script) or
directly (a lookup plugin resolves the secret at playbook run time).

Recommendation: start with **Ansible Vault** for v1 (zero extra
infrastructure to stand up). `community.hashi_vault` is the natural
upgrade path if a self-hosted HashiCorp Vault OSS instance gets added
later — the mechanism is pluggable, so this isn't a decision that needs
to be locked in now.

## 13. Networking

Both static IP and DHCP are supported, chosen per host or per profile
(e.g. `network: {mode: static, address: ...}` vs `{mode: dhcp}`). The
domain controller should generally be static (it's also serving DNS),
but nothing in the design forces static addressing on every host.

## 14. Workflow

```
tofu init && tofu workspace select <backend>
tofu apply -var-file=profiles/medium.tfvars
# inventory auto-generated from tofu output
ansible-playbook -i inventory/generated site.yml -e @software-manifest.yml
# ...
tofu destroy
```

Control machine is Linux or WSL (Ansible doesn't run natively as a
control node on Windows). Wrapped in `scripts/deploy.sh` / a `Makefile`
(`make up PROFILE=medium`, `make down`) so day-to-day use is one
command.

## 15. Testing / Validation

- `tofu validate` and `tflint` for the provisioning code.
- `ansible-lint` for playbooks/roles.
- Molecule role testing is a stretch goal, not v1.

## 16. Open Questions

1. **Domain controller count**: additional to each profile's stated
   Windows Server count, or counted against it? (§9 assumes additional.)
2. **Additional Linux distros**: add AlmaLinux alongside Rocky/RHEL as
   proposed in §5? Any interest in openSUSE, Fedora, or Oracle Linux, or
   leave those out for now?
3. **Domain naming**: any preference for the AD DNS domain name/NetBIOS
   name per environment, or is a generated default (e.g.
   `lab.local`/`LAB`) fine?
4. **Base image default**: should new profiles default to the Packer
   golden-image path, the direct-ISO path, or should that default be
   per-OS (e.g. Packer for the OSes you rebuild often, direct ISO for
   ones you only need occasionally)?

## 17. Proposed Milestones

- **M1** — libvirt backend, small profile, Linux-only VMs, base Ansible
  config. End-to-end smoke test on one backend.
- **M2** — Hyper-V backend reaching parity with M1 for Linux VMs.
- **M3** — Windows VM support on both backends, WinRM bootstrap,
  `windows_common` role.
- **M4** — Domain controller role: AD DS promotion, Windows domain join
  (server + workstation), Linux realm join (`realmd`/`sssd`).
- **M5** — Workstation host type + medium profile, validated with
  domain join across all host types.
- **M6** — Software manifest system finalized; Packer base images for
  the full OS matrix in §5; direct-ISO-boot path implemented.
- **M7** — Secrets/vault integration, CI validation (`tflint`,
  `ansible-lint`), docs polish.
