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
  mode: nat                   # nat (default) | bridged — see §14.1
  subnet: 10.50.0.0/24        # this environment's reserved subnet, see §14.2
  dns_forwarders: [1.1.1.1, 9.9.9.9]
```

`domain_name` is never hardcoded — `company.com` above is only the
example/placeholder value shipped in `environment.example.yml`.

## 11. Inventory & Software Manifest

- OpenTofu outputs a dynamic Ansible inventory; each host lands in one
  group per role it carries (§9).
- The software list is **not** baked into the roles. It's supplied as a
  per-run manifest (e.g. `software-manifest.yml`) consumed by generic
  roles (`windows_common`, `linux_common`) that loop over a variable
  package list using `chocolatey` (Windows) and `apt`/`dnf`/`zypper`
  (Linux) modules — this avoids writing a new Ansible role every time
  the software list changes.

## 12. Proposed Repository Layout

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
      network/          # per-environment NAT network, common interface (§14.1)
        hyperv/         # Internal switch + null_resource/remote-exec New-NetNat
        libvirt/        # libvirt_network, mode = "nat"
    environments/
      small/
      medium/
    profiles.tfvars.example
    environment.example.yml
  ansible/
    group_vars/
    roles/
      domain_controller/
      windows_common/
      linux_common/
    playbooks/
      site.yml          # ordered: domain_controller role -> domain-join -> per-role software
    software-manifest.example.yml
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
    deploy.sh            # tofu apply -> generate inventory -> ansible-playbook
    destroy.sh
    promote-to-template.sh   # generalize + export a live ISO-built VM into a template
```

## 13. Credentials & Secrets

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

## 14. Networking

### 14.1 Isolation model: NAT'd per-environment network (default)

Each environment gets its **own virtual network, isolated from the
physical LAN and from every other environment**, with outbound internet
access via NAT — not bridged onto the physical network by default.

This matters specifically because of §8: every environment stands up its
own AD domain and DNS server. Bridging that onto the real network risks
a rogue DHCP/DNS server and domain-name/NetBIOS collisions with whatever
the physical network already runs — a materially worse failure mode than
a normal isolated test VM. NAT keeps the domain and its DNS contained to
the environment's own virtual network while still letting Ansible pull
packages (`apt`/`dnf`/`zypper`/`chocolatey`) and Windows Update reach the
internet, which the software-manifest step depends on.

Both backends have a native resource for exactly this:

| Backend | Mechanism |
|---|---|
| libvirt | `libvirt_network` resource, `mode = "nat"` — DHCP/DNS (dnsmasq) and NAT are handled by the resource itself. |
| Hyper-V | An **Internal** `hyperv_network_switch` bound to `New-NetNat`. The `taliesins/hyperv` provider manages the switch but has no native NAT resource, so the NAT binding itself is a `null_resource` + WinRM `remote-exec` provisioner running `New-NetNat` — a real gap between the two backends, not just a config difference. |

A **bridged** mode (the environment's VMs placed directly on the
physical LAN) is supported as an explicit opt-in override per
environment for cases where that's genuinely wanted (e.g. testing
network discovery against real infrastructure), but it is never the
default given the domain-collision risk above.

### 14.2 Per-environment addressing

Each environment is assigned one subnet (e.g. a `/24`) out of a range
you reserve for LABaPe, set in `environment.yml`:

```yaml
network:
  mode: nat                 # nat (default) | bridged
  subnet: 10.50.0.0/24
  dns_forwarders: [1.1.1.1, 9.9.9.9]   # upstream DNS the DC forwards to
```

Within that subnet, static-role hosts (domain controller, and any other
host configured `mode: static` per §14.3 of the original design) take
addresses from a low, fixed range; the platform's own DHCP (dnsmasq for
libvirt, the Hyper-V NAT DHCP) serves the rest — e.g. `.1` = gateway,
`.2`–`.19` reserved for static hosts, `.20`–`.199` DHCP pool,
`.200`–`.254` reserved. Both static and DHCP addressing remain supported
per-host as already noted below; this just describes how they share one
environment subnet without collision.

Subnet assignment across **concurrent** environments is manual for v1
(you pick a non-overlapping `/24` per environment instance) rather than
auto-allocated — auto-allocation would need a shared registry across
independent OpenTofu states, which isn't worth building until running
several environments side by side is an actual, not hypothetical, need.

### 14.3 DNS

The domain controller's AD-integrated DNS is authoritative for the
environment's domain and forwards everything else to
`network.dns_forwarders`. In-domain hosts get this automatically (their
DHCP/static config points at the DC). Your own admin/control machine —
which is *not* domain-joined — won't resolve lab hostnames unless it's
explicitly pointed at the DC's DNS server (or the deploy step writes out
a local hosts-file snippet from the OpenTofu-generated inventory as a
convenience — worth adding once M4 lands).

### 14.4 Reaching into the environment (admin / control machine)

Since the network is NAT'd rather than bridged, both your own management
access and the Ansible control machine (§15 — itself possibly a separate
Linux/WSL box, not the hypervisor host) need a way in. Two options, not
mutually exclusive:

- **Route via the host** — the hypervisor host already has an interface
  on each environment's virtual network (a libvirt bridge, or a Hyper-V
  internal-switch vEthernet adapter). Adding a static route on the admin
  machine pointing the environment's subnet at the host's LAN IP, plus
  enabling IP forwarding and a firewall allow rule on the host, gives
  full reachability to every VM with no per-VM configuration. Simplest
  when the admin machine is on the same LAN as the host and you can add
  routes on it.
- **NAT port-forwarding per VM** — `Add-NetNatStaticMapping` (Hyper-V) or
  a manual DNAT `iptables`/`nft` rule (libvirt, since its built-in NAT
  network doesn't include port-forwarding config on its own) exposing
  specific ports (3389/RDP, 22/SSH, 5985-5986/WinRM) on the host's own
  IP. Needed when the admin/control machine can't get a route added
  (e.g. a locked-down corporate network).

Both are documented rather than picking one as *the* answer, since which
one applies depends on your actual network permissions — see §17.

### 14.5 Per-host addressing mode (unchanged from earlier draft)

Both static IP and DHCP are supported, chosen per host or per host group
(e.g. `network: {mode: static, address: ...}` vs `{mode: dhcp}`). The
domain controller should generally be static (it's also serving DNS),
but nothing in the design forces static addressing on every host.

## 15. Workflow

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

## 16. Testing / Validation

- `tofu validate` and `tflint` for the provisioning code.
- `ansible-lint` for playbooks/roles.
- Molecule role testing is a stretch goal, not v1.

## 17. Open Questions

None blocking further scaffolding right now. Remaining items are
implementation-level and can be decided as each milestone is built:

1. Per-host-group **NetBIOS derivation** default (first label of
   `domain_name`, uppercased) — flag if a different default is wanted.
2. Whether `promote-to-template.sh` (§12) is a manually-run step (you
   decide when a lab VM is "good enough" to become a template) or should
   ever be triggered automatically — current design assumes manual,
   since "ready to template" isn't a well-defined automatic condition.
3. **Reachability method (§14.4)**: route-via-host or NAT port-forwarding
   as the standard way you and the Ansible control machine reach into an
   environment — depends on where the control machine sits relative to
   the hypervisor host and what routing you're able to add, which isn't
   something the design can decide for you. Both are documented; pick
   one as the default when this is built, or use route-via-host as the
   default with port-forwarding as a documented fallback if that's fine.
4. **Concurrent environments**: is running more than one environment at
   once an actual near-term need? If so, the manual per-environment
   subnet assignment in §14.2 needs a documented convention (e.g. a
   simple counter/registry file) sooner rather than as a stretch goal.

## 18. Proposed Milestones

- **M1** — libvirt backend, small profile, Linux-only VMs, NAT'd
  per-environment network (`libvirt_network`), base Ansible config,
  direct-ISO-boot path only. End-to-end smoke test on one backend.
- **M2** — Hyper-V backend reaching parity with M1 for Linux VMs,
  including the Internal-switch + `New-NetNat` provisioning step (§14.1).
- **M3** — Windows VM support on both backends, WinRM bootstrap,
  `windows_common` role.
- **M4** — Domain controller role: AD DS promotion, configurable domain
  name, Windows domain join (server + workstation), Linux realm join
  (`realmd`/`sssd`). Validate the flexible-role model (DC as
  single-purpose vs. dual-role host) and DC-provided DNS with upstream
  forwarding (§14.3).
- **M5** — Workstation host type + medium profile, validated with
  domain join across all host types. Implement the chosen reachability
  method (§14.4/§17.3).
- **M6** — Packer base images for the full OS matrix in §5, set as the
  default image source; `promote-to-template.sh` for turning an
  ISO-built lab VM into a reusable template; software manifest system
  finalized.
- **M7** — Secrets/vault integration, CI validation (`tflint`,
  `ansible-lint`), docs polish.
