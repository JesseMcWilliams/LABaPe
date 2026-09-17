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
  hosts to it automatically. Which host acts as the domain controller is
  flexible — it can be a single-purpose host or share a host with
  another role.
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
   hypervisor provider and emits a dynamic Ansible inventory. Each host
   can belong to multiple Ansible groups, one per role it carries (see
   §9 — a host isn't limited to a single role).
2. **Directory services bring-up (Ansible, ordered)** — promotes
   whichever host(s) carry the `domain_controller` role first, since
   every other host's domain join depends on the domain and its DNS
   existing. This runs before any other role's play, even on a host that
   *also* carries another role.
3. **Configuration (Ansible, remaining role plays)** — joins the rest of
   the Windows and Linux hosts to the domain, then installs software and
   applies base configuration, over WinRM (Windows) and SSH (Linux).

```
 environment config (profile + domain name + software manifest)
              │
              ▼
        OpenTofu apply
   (hyperv or libvirt provider)
              │
              ▼
      generated inventory (hosts tagged by role, possibly >1 each)
              │
              ▼
   Ansible: promote domain_controller-role host(s) first
              │
              ▼
   Ansible: join remaining hosts to domain (Windows + Linux)
              │
              ▼
   Ansible: per-role software install + base config
   (runs for every role a host carries, DC included)
              │
              ▼
        Ready environment
```

## 5. Supported Operating Systems

| Category | Targets |
|---|---|
| Windows Server | 2019, 2022, 2025 |
| Windows Client | 10, 11 |
| Linux (RHEL family) | Rocky Linux, RHEL, AlmaLinux |
| Linux (Debian family) | Ubuntu (LTS), Debian, Linux Mint |
| Linux (other) | Fedora, openSUSE/SLES, Oracle Linux |

Notes:
- **RHEL** and **Oracle Linux** need registration (Red Hat subscription
  or the free Red Hat Developer subscription; Oracle's `ULN`/`uek` repos
  are free to register) to pull packages/updates during image build and
  on the running host. Rocky, AlmaLinux, Fedora, and openSUSE don't need
  this.
- **Linux Mint** is Ubuntu-based (Mint Debian Edition is Debian-based) —
  it reuses the Ubuntu/Debian build pipeline rather than needing its own.
- **openSUSE/SLES** uses AutoYaST instead of kickstart/preseed for its
  unattended install — its own answer-file format, same role in the
  pipeline.
- Domain controllers require **Windows Server** — AD DS isn't available
  on Windows client or Linux.

## 6. Hypervisor Backends

| Backend | Host OS | Provider |
|---|---|---|
| Hyper-V | Windows Server (Hyper-V role) | `taliesins/hyperv` (community, via WinRM to the Hyper-V host) |
| KVM/libvirt | Rocky Linux / Debian | `dmacvicar/libvirt` |

Backend-specific details live behind a common module interface
(`modules/vm`) so an environment definition (profile + manifest) doesn't
change based on which backend is selected — only which backend module is
invoked does.

### 6.1 The `vm` module interface

`modules/vm/hyperv` and `modules/vm/libvirt` implement the **identical**
set of inputs and outputs — an environment definition never has to know
which one it's talking to:

**Inputs:**

| Variable | Type | Notes |
|---|---|---|
| `name` | string | VM/hostname |
| `os` | string | key into a backend-specific catalog, e.g. `windows_server_2022`, `rocky9` — resolves to that backend's actual template name or ISO+answer-file pair internally |
| `roles` | list(string) | passthrough only — the `vm` module doesn't interpret roles, it just carries them through to the generated inventory (§9) |
| `image_source` | string | `"packer_template"` \| `"iso_direct"` |
| `cpu_count`, `memory_mb`, `disk_gb` | number | |
| `network_id` | string | output of the `network` module (§6.2) — which switch/bridge/libvirt-network this VM attaches to |
| `addressing` | object | `{ mode = "static"\|"dhcp", address, prefix_length, gateway }` — `address` etc. only meaningful when `mode = "static"` |
| `admin_credential` | sensitive object | bootstrap password (Windows) or SSH public key (Linux), from `docs/credentials.md` |
| `template_vars` | map(string) | extra values rendered into the answer file/finalize scripts — `domain_name`, `dns_forward_ip`, `management_source` (docs/credentials.md §7), etc. |

**Outputs:**

| Output | Notes |
|---|---|
| `name`, `roles` | passthrough |
| `os_family` | `"windows"` \| `"linux"`, derived from `os` — tells the inventory generator whether to set `ansible_connection=winrm` or `ssh` |
| `ip_address` | for `mode = "static"`, this just echoes the input. **For `mode = "dhcp"`, this is a real gap**: the address genuinely isn't known at `apply` time. Resolved with a post-apply discovery step in the same script that generates the Ansible inventory (§11) — `Get-VMNetworkAdapter` (Hyper-V) or `virsh domifaddr` (libvirt) queried after the VM has booted and picked up a lease, before inventory generation runs. Flagged here rather than glossed over, since "both static and DHCP are supported" (§14) undersold that DHCP needs this extra step to actually work with the rest of the pipeline. |

### 6.2 The `network` module interface

`modules/network/hyperv` and `modules/network/libvirt` (§14, §12) follow
the same pattern: `environment_name`, `mode` (`bridged`\|`nat`),
`network_address`/`subnet_mask`/`gateway`, plus `physical_nic`
(bridged) or nothing extra (nat, since libvirt/Hyper-V handle DHCP/NAT
internally per `docs/networking.md`). Output: `network_id`, consumed by
every `vm` module call for that environment.

### 6.3 Backend selection and state (a real Terraform/OpenTofu constraint)

A module's `source` argument has to be a **literal string** — it can't
be a variable or an expression. That rules out one root configuration
that picks `modules/vm/hyperv` vs. `modules/vm/libvirt` at `apply` time
based on a variable; OpenTofu has no such thing as a runtime-selected
module source. So backend selection happens one level up, by which root
configuration you run, not by a variable inside one root config:

```
tofu/
  modules/
    vm/            hyperv/  libvirt/     # §6.1, identical contract
    network/       hyperv/  libvirt/     # §6.2, identical contract
  backends/
    hyperv/        # root config — hardcodes module "hosts" { source = "../../modules/vm/hyperv" ... }
      main.tf
      variables.tf
    libvirt/       # root config — hardcodes the libvirt equivalent
      main.tf
      variables.tf
  environments/
    small.tfvars.example
    medium.tfvars.example
  environment.example.yml
```

Both `backends/*/` roots consume the **same** `environments/<size>.tfvars`
host-group data and the same `environment.yml` — only which directory
`scripts/deploy.sh` runs `tofu apply` in changes with the backend. This
corrects an imprecision in §15's original phrasing ("workspace select
`<backend>`") — **workspaces don't select a backend**, they isolate
*state* within one root config. The corrected model:

- **Directory** (`backends/hyperv/` vs. `backends/libvirt/`) = which
  hypervisor.
- **Workspace** (`tofu workspace new <environment-instance-name>`)
  within that directory = which running environment *instance* — this
  is what actually isolates state between, say, two concurrent `medium`
  environments, each getting its own state file automatically
  (`terraform.tfstate.d/<workspace>/...`) without separate directories
  per instance.

**State backend**: local state (the default) is fine for v1 — a single
self-hosted control machine, one operator. If the control machine ever
stops being a single fixed machine (a laptop sometimes, WSL sometimes)
and state needs to survive that, OpenTofu's `pg` backend (state in a
Postgres database) is the natural self-hosted upgrade — no new service
needed if Postgres is already available, and it avoids pulling in a
cloud-only backend for a project that's explicitly self-hosted (§3).
Same pattern as the Vault → HashiCorp Vault progression in §13: name the
upgrade path, don't build it until it's an actual need. Either way,
`.terraform/` and any local `terraform.tfstate*` files are `.gitignore`d
— state can contain values from `template_vars`/`admin_credential`
that shouldn't end up in the repo.

**Feeding `environment.yml` to OpenTofu**: `environment.yml` is the one
human-edited file (§10), but OpenTofu doesn't read YAML directly.
`scripts/deploy.sh` converts it to `environment.auto.tfvars.json`
(OpenTofu auto-loads any `*.auto.tfvars.json` in the working directory)
before calling `tofu apply` — one source of truth for a human to edit,
without hand-maintaining a parallel `.tfvars` copy.

## 7. Base Images

Two supported paths for getting a host to a usable base OS state,
chosen per host/host-group, not globally:

1. **Packer-built golden templates** — the **default** for every host
   unless overridden. Built once, cloned for every VM — much faster
   per-environment provisioning.
2. **Direct ISO boot** — the hypervisor provider boots straight from the
   vendor ISO with an answer file, no pre-built template. Slower per
   `tofu apply` but useful to stand up an initial lab quickly, or for a
   one-off/rarely used OS version.

A host built via direct ISO boot can later be **promoted into a
template** once it's proven out — this is the intended day-one workflow:
build the first lab from ISOs, then convert the VMs you'll keep reusing
into Packer-equivalent templates rather than reinstalling from ISO every
time. Both paths use the *same* answer files, and the promotion path
reuses the same generalize/sysprep steps a Packer build would run. Full
process, including promotion, in
[`docs/base-images.md`](./docs/base-images.md).

## 8. Directory Services (Domain Controller & Domain Join)

- The AD domain is deployed **as part of the environment**, not assumed
  to pre-exist. Every profile that wants domain join needs at least one
  host carrying the `domain_controller` role (Windows Server only).
- **The domain controller role is just a role, not a dedicated host
  type.** A host group can carry `[domain_controller]` alone (a
  single-purpose DC) or `[domain_controller, windows_server]` (a host
  that's both the DC and a general-purpose member server) — this is a
  per-environment config choice, not something fixed by the tooling. See
  §9 for how roles are expressed.
- **Ordering constraint**: whichever host(s) carry `domain_controller`
  must be provisioned and promoted (`Install-ADDSForest`), with DNS
  answering, before any host attempts to join — including that host's
  *own* other role plays, if it carries more than one role.
- **Domain naming** is configurable per environment, not hardcoded —
  see §10. Placeholder default: `company.com`.
- **Windows join**: standard `Add-Computer -DomainName` against the
  freshly promoted domain.
- **Linux join**: `realmd`/`sssd` (works across the RHEL, Debian, and
  other Linux families) rather than a distro-specific mechanism.
- Since environments are rebuilt from scratch, each rebuild creates a
  **brand-new AD forest** — no state carried over between environment
  lifecycles.

## 9. Environment Profiles & Host Roles

A profile is a list of **host groups**, each with a count, an OS, and a
**list of roles** — a host can carry more than one role, which is how DC
placement stays flexible (§8) instead of being a fixed slot:

```yaml
profiles:
  small:
    host_groups:
      - name: dc
        count: 1
        os: windows_server_2022
        roles: [domain_controller]        # single-purpose DC
      - name: winsrv
        count: 2
        os: windows_server_2022
        roles: [windows_server]
      - name: linsrv
        count: 2
        os: rocky9
        roles: [linux_server]

  medium:
    host_groups:
      - name: dc
        count: 1
        os: windows_server_2022
        roles: [domain_controller, windows_server]   # DC doubles as a member server
      - name: winsrv
        count: 2
        os: windows_server_2022
        roles: [windows_server]
      - name: linsrv
        count: 3
        os: rocky9
        roles: [linux_server]
      - name: winws
        count: 2
        os: windows_11
        roles: [windows_workstation]
      - name: linws
        count: 2
        os: ubuntu_lts
        roles: [linux_workstation]
```

Each role a host carries becomes an Ansible inventory group membership,
so a host with multiple roles simply runs multiple role plays against
it — the `domain_controller` play always runs first for that host,
regardless of what else is in its role list (§8). A custom profile is
just a different `host_groups` list — no separate code path.

## 10. Environment Configuration

Settings that describe a *specific* environment instance rather than its
topology (which is what a profile describes) live in their own config,
e.g. `environment.yml`:

```yaml
domain_name: company.com      # placeholder — set per environment
netbios_name: COMPANY         # optional override; derived from domain_name if omitted
image_source_default: packer_template   # can be overridden per host group
network:
  mode: bridged                # bridged (default) | nat — see §14
  network_address: 192.168.1.0 # freely configurable — no imposed slicing convention;
  subnet_mask: 255.255.255.0   # a /24 on the real LAN (bridged), an isolated
                                # range (nat), a VLAN-specific block, whatever
                                # you actually use. Normalized to CIDR internally
                                # for the OpenTofu resources.
  gateway: 192.168.1.1         # optional; sensible per-mode default if omitted
  dns_forward_ip:               # optional — see §14 "DNS forwarding"
    - 1.1.1.1
    - 9.9.9.9
  management_source: 192.168.1.50   # control machine IP/CIDR — WinRM/SSH
                                     # firewall scoping, docs/credentials.md §7
software_store_path: ./software-store   # optional override — local installer
                                         # files, docs/software-manifest.md §8
```

`domain_name` is never hardcoded — `company.com` above is only the
example/placeholder value shipped in `environment.example.yml`.
`network_address`/`subnet_mask` resolve §17's earlier open question:
there's no assumed `/28`-style convention — you set whatever address and
mask actually match how you carve out lab space.

## 11. Inventory & Software Manifest

- OpenTofu outputs a dynamic Ansible inventory; each host lands in one
  group per role it carries (§9).
- The software list is **not** baked into the roles. It's supplied as a
  per-run manifest consumed by generic roles (`windows_common`,
  `linux_common`) — this avoids writing a new Ansible role every time
  the software list changes, which is the whole reason this needs a
  manifest rather than fixed roles per package.

Package naming isn't consistent across `chocolatey`/`apt`/`dnf`/`zypper`
for "the same" software, and some packages need extra setup (a repo
added first) or aren't in any package manager at all. Full design,
including exactly how a multi-role host's package list is resolved, is
in [`docs/software-manifest.md`](./docs/software-manifest.md). Summary:

- **Two files, two lifecycles**: `ansible/package_catalog.yml`
  (repo-committed, stable — generic name → per-package-manager name,
  optional repo/custom-installer info) vs. `software-manifest.yml`
  (per-run — which catalog entries apply to which role; also accepts
  one-off inline package definitions not worth cataloging).
- **Missing platform fields are a silent skip, not an error** — the OS
  matrix (§5) is wide enough that "not packaged for this platform" is
  normal.
- **Repo setup** (VS Code, Docker, etc.) is a named, reusable task per
  repo, run once per host for the unique set of repos its resolved
  packages actually need.
- **Multi-role resolution is explicit**, not left to Ansible's default
  variable behavior — group_vars lists don't merge across groups by
  default, so a host with more than one role (§9) needs its roles'
  package lists explicitly unioned via `group_names`, or software
  silently goes missing on exactly the hosts §9 was designed to support.
- **Custom installers** (no package-manager entry at all) use the same
  catalog-entry shape with a `type: msi/exe/deb/rpm` + `source: url`
  instead of a package name, so `windows_common`/`linux_common` have one
  lookup path regardless of how a given package installs.
- **Locally-sourced installers** (`source: local`) cover software with
  no repo *and* no reachable URL — an MSI/EXE/.deb/.rpm that only exists
  as a file on your own machine. `win_copy`/`copy` pushes it from the
  control machine to the target over the existing WinRM/SSH connection
  before installing, from a `.gitignore`d **software store** directory
  (`docs/software-manifest.md` §8) rather than committing binaries to
  git.
- **Versioning** is optional per entry; omitted means "latest," which is
  the practical default for a disposable environment.

## 12. Proposed Repository Layout

```
LABaPe/
  README.md
  DESIGN.md
  docs/
    base-images.md
    networking.md
    credentials.md
    software-manifest.md
  secrets.vault.example.yml   # unencrypted shape only — see docs/credentials.md §1
  software-store/             # .gitignore'd — local installer files, docs/software-manifest.md §8
  tofu/
    modules/
      vm/               # common interface, §6.1
        hyperv/
        libvirt/
      network/          # common interface, §6.2 — bridged (default) + opt-in NAT (§14)
        hyperv/         # External switch (bridged); Internal switch +
                         # null_resource/remote-exec New-NetNat (nat, opt-in)
        libvirt/        # bridge device (bridged); libvirt_network mode="nat" (opt-in)
    backends/           # root configs — one per hypervisor, §6.3
      hyperv/
        main.tf
        variables.tf
      libvirt/
        main.tf
        variables.tf
    environments/       # data only, consumed by either backends/*/ root
      small.tfvars.example
      medium.tfvars.example
    environment.example.yml
  ansible/
    group_vars/
    package_catalog.yml   # repo-committed, stable — §11 / docs/software-manifest.md
    roles/
      domain_controller/
      windows_common/     # resolves group_names -> package_catalog -> win_chocolatey/win_package
      linux_common/       # resolves group_names -> package_catalog -> apt/dnf/zypper + repo setup
    playbooks/
      site.yml          # ordered: domain_controller role -> domain-join -> per-role software
    software-manifest.example.yml   # per-run — §11 / docs/software-manifest.md
  packer/
    windows/
      2019/ 2022/ 2025/
    linux/
      rocky/ rhel/ almalinux/ fedora/ opensuse/ oraclelinux/ ubuntu/ debian/ mint/
  iso/
    answer-files/        # shared by Packer builds, direct-ISO-boot, and template promotion
      windows/
      rhel-family/
      debian-family/
      opensuse/
  scripts/
    deploy.sh            # vault decrypt -> check-network -> tofu apply -> generate inventory/hosts -> ansible-playbook
    destroy.sh
    check-network.sh     # pre-flight address/subnet availability check (docs/networking.md §3)
    check-network.ps1
    promote-to-template.sh   # generalize + export a live VM (from ISO or a template clone) into a template
    refresh-template.sh      # clone existing template -> apply update via Ansible -> promote as new version
```

## 13. Credentials & Secrets

WinRM credentials, SSH keys, hypervisor host credentials, and the domain
admin/local administrator passwords must never be committed.

This also covers two bootstrap problems that sit underneath everything
else: how OpenTofu authenticates to the hypervisor host itself, and how
Ansible gets its very first connection to a VM nothing has configured
yet. Both share the **same** vault rather than inventing separate
credential paths — full mechanics, including the exact `deploy.sh`
credential flow, are in
[`docs/credentials.md`](./docs/credentials.md). Summary:

- Hyper-V auth (WinRM to the host) and libvirt auth (SSH URI) both come
  from `secrets.vault.yml`, exported as `TF_VAR_*` before `tofu apply`.
- Windows VMs bootstrap on a shared local Administrator password
  (rendered into the answer file at build/apply time, never hardcoded in
  the checked-in XML); Linux VMs bootstrap on the control machine's own
  SSH key baked into cloud-init/kickstart. Both are also what Ansible
  uses for its first connection — no separate initial-vs-later
  credential model.
- Given bridged-by-default networking (§14), WinRM is more exposed than
  it would be behind NAT — prefer HTTPS and firewall-scoping the WinRM
  listener where practical (`docs/credentials.md` §7).

Ansible's own secrets-at-rest options, from simplest to most integrated:

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

## 14. Networking

Default mode is **bridged**: environment VMs go straight on the
physical LAN, matching how the lab is actually used — heavy hands-on
testing against it, with hostnames managed by hand-editing a hosts file
(§14.4) rather than via DNS lookups. **NAT isolation remains available
as an opt-in** (`network.mode: nat`) for an environment that should stay
off the physical network entirely.

Full mechanics — the exact Hyper-V and libvirt steps for both modes, the
pre-flight availability check, and hosts-file generation — are in
[`docs/networking.md`](./docs/networking.md). Summary:

- **Bridged (default)**: an `External` Hyper-V switch or a bridge device
  (`br0`) on libvirt — both providers support this natively, no
  workaround needed. Caution: since the domain controller (§8) and its
  DNS are directly reachable on the segment, pick a `domain_name` that
  won't collide with a real corporate domain on the same network, and
  don't enable the DC's DHCP role unless that's actually wanted.
- **NAT (opt-in)**: `libvirt_network` (`mode = "nat"`) is native and
  needs no extra steps. Hyper-V has no single resource for this —
  creating the Internal switch, assigning the gateway address, and
  binding `New-NetNat` is a WinRM-executed provisioner sequence, fully
  documented in `docs/networking.md` §2 since it's the more involved
  path of the two.
- **Pre-flight check** (`scripts/check-network.sh`/`.ps1`): before
  `tofu apply`, verifies the environment's planned static addresses
  (bridged) or subnet/prefix (NAT) aren't already in use — an ARP/ping
  check for bridged addresses, `Get-NetNat`/`virsh net-list` comparison
  for NAT — so a collision (with another running environment, or
  anything else on the LAN) fails fast instead of mid-`apply`. This is
  the direct mitigation for running multiple environments concurrently
  (§17.4).
- **Hosts-file generation**: `scripts/deploy.sh` writes
  `inventory/hosts.generated` — a ready-to-paste snippet mapping every
  VM's IP to its hostname/domain — after `tofu apply`, matching the
  existing manual workflow rather than replacing it.

**Network address and subnet are a plain configuration option** —
`network.network_address`/`network.subnet_mask` in `environment.yml`
(§10). There's no imposed slicing convention (no assumed `/28`, no
fixed static/DHCP split baked into the tooling); you set whatever
address and mask match how the LAN is actually carved up for the lab, or
whatever isolated range you want in NAT mode.

**DNS forwarding**: `network.dns_forward_ip` (§10) is the upstream DNS
server(s) configured as **Forwarders** on the environment's own DNS
server — which, in this design, is whatever host got promoted to
`domain_controller` (§8), since that's where AD-integrated DNS runs.
This setting only does something if a profile's host groups actually
include that role; it's silently unused on, say, a Linux-only
environment with no domain controller. It applies the same way
regardless of network mode (bridged or NAT) — it's about what the
DC forwards non-domain queries to, not about the environment's own
network topology.

Per-host addressing (unchanged): both static IP and DHCP are supported,
chosen per host or per host group (e.g.
`network: {mode: static, address: ...}` vs `{mode: dhcp}`). The domain
controller should generally be static (it's also serving DNS). In
bridged mode, DHCP-mode hosts get their lease from whatever DHCP server
already serves that LAN segment — LABaPe doesn't manage it. In NAT mode,
DHCP is either the libvirt network's built-in `dnsmasq`, or, on Hyper-V,
a Windows DHCP Server role scoped to the environment (`docs/networking.md`
§2 step 4) since a custom Internal+NAT switch has no DHCP of its own.

## 15. Workflow

Corrected per §6.3 — the backend is which directory you run in, not a
workspace:

```
cd tofu/backends/<hyperv|libvirt>
tofu init
tofu workspace new <environment-instance-name>   # or `select` if it already exists
# environment.yml -> environment.auto.tfvars.json, generated by deploy.sh (§6.3)
tofu apply -var-file=../../environments/medium.tfvars
# inventory (+ hosts.generated) auto-generated from tofu output, including
# post-apply DHCP-lease discovery for any host using addressing.mode=dhcp (§6.1)
ansible-playbook -i inventory/generated site.yml -e @software-manifest.yml
# ...
tofu destroy
```

Control machine is Linux or WSL (Ansible doesn't run natively as a
control node on Windows). Wrapped end-to-end in `scripts/deploy.sh` /
`scripts/destroy.sh` so day-to-day use is one command per backend
directory.

## 16. Testing / Validation

- `tofu validate` and `tflint` for the provisioning code.
- `ansible-lint` for playbooks/roles.
- Molecule role testing is a stretch goal, not v1.

## 17. Open Questions

None blocking further scaffolding right now.

1. ~~NetBIOS derivation~~ — resolved: first label of `domain_name`,
   uppercased, with `netbios_name` as an explicit override. Confirmed.
2. ~~Promote-to-template trigger~~ — resolved as manual (docs/base-images.md
   §5), and extended: `promote-to-template.sh` is now one building block
   of two distinct, both-manual workflows — promoting a fresh ISO-built
   VM (§5) and refreshing an *existing* template when a package or patch
   needs to land in it (§6, `scripts/refresh-template.sh`) — rather than
   a single one-shot "onboarding" operation.
3. ~~Bridged address range~~ — resolved: §14 makes
   `network_address`/`subnet_mask` a plain, unopinionated config option
   rather than assuming any particular slicing convention.
4. **DHCP-mode IP discovery timing** (§6.1): the design calls for a
   post-apply discovery step (`Get-VMNetworkAdapter`/`virsh domifaddr`)
   before inventory generation for any host using `addressing.mode:
   dhcp`. Not blocking — static addressing works today without it — but
   worth deciding during M1/M2 whether that discovery step is worth
   building right away or whether static-only is fine until a real need
   for DHCP-mode hosts shows up.

## 18. Proposed Milestones

- **M1** — `modules/vm`/`modules/network` interface implemented for
  libvirt (§6.1/§6.2), `tofu/backends/libvirt` root config, small
  profile, Linux-only VMs, bridged networking (default), pre-flight
  address check (`scripts/check-network.sh`), base Ansible config,
  direct-ISO-boot path only. End-to-end smoke test on one backend.
- **M2** — Hyper-V backend reaching parity with M1 for Linux VMs
  (External-switch bridged networking, `check-network.ps1`).
- **M3** — Windows VM support on both backends, WinRM bootstrap,
  `windows_common` role.
- **M4** — Domain controller role: AD DS promotion, configurable domain
  name, Windows domain join (server + workstation), Linux realm join
  (`realmd`/`sssd`), hosts-file snippet generation (`docs/networking.md`
  §4). Validate the flexible-role model (DC as single-purpose vs.
  dual-role host).
- **M5** — Workstation host type + medium profile, validated with
  domain join across all host types.
- **M6** — Packer base images for the full OS matrix in §5, set as the
  default image source; `promote-to-template.sh` for turning an
  ISO-built lab VM into a reusable template; `refresh-template.sh`
  (docs/base-images.md §6) for updating an existing template without a
  full rebuild; software manifest system finalized.
- **M7** — NAT isolation mode (opt-in) for both backends, including the
  Hyper-V Internal-switch + `New-NetNat` provisioning sequence
  (`docs/networking.md` §2).
- **M8** — Secrets/vault integration, CI validation (`tflint`,
  `ansible-lint`), docs polish.
