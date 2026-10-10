# Base Images: Packer Templates, Direct ISO Boot, Promotion, and Refresh

Both hypervisor backends (Hyper-V, libvirt) need a starting point for
each VM's disk. LABaPe supports four related workflows, chosen per
host/host-group, not globally: build a template with Packer up front,
boot straight from ISO, boot from ISO now and promote the result into a
template later, or refresh an *existing* template once it needs an
update.

## 1. The four workflows

| | Packer-built template | Direct ISO boot | ISO boot, then promote | Refresh an existing template |
|---|---|---|---|---|
| When the OS install runs | Once, ahead of time | Every `tofu apply` | Once, during initial lab build | Never, for the recommended incremental path (§6 Option B) — starts from the template, not ISO. A full rebuild (§6 Option A) still runs it, same as the first column. |
| Per-VM provisioning time after this | Minutes (clone + boot) | As long as the OS installer takes, every time | Minutes, after the one-time promotion | Minutes, same as any template clone |
| Extra pipeline/state to maintain | Yes — template library | No | Template library, but built lazily | Template library (updates it) |
| Best for | OS versions already known to be reused often | One-off/rarely used OS versions; evaluating a new release | **Recommended default day-one workflow** — stand a lab up fast from ISO, then convert the VMs worth keeping into templates instead of reinstalling next time | **A software package or OS patch needs to land in a template you already have** — see §6 |

`image_source_default: packer_template` (Claude_Docs/Design_System-Overview.md §10) is the default
for new host groups, but nothing stops a host group from starting on
`iso_direct` and moving to a promoted template once it's proven out —
that transition is exactly what §5 below covers. §6 covers keeping an
already-promoted (or already Packer-built) template current.

The first three workflows all use **the same answer files**
(`autounattend.xml` for Windows, kickstart for the RHEL family,
cloud-init autoinstall for the Debian family, AutoYaST for
openSUSE/SLES) stored once under `iso/answer-files/`. Packer wraps the
unattended install into a repeatable pipeline; direct ISO boot hands the
identical answer file to the hypervisor provider on every apply;
promotion reuses the same generalize/finalize steps a Packer build would
run, just against an already-installed VM instead of inside an isolated
build. Refresh (§6) only touches answer files if it takes Option A (a
full rebuild) — Option B, the recommended path for a small change,
clones an existing template instead and never touches ISO or answer
files at all. Nothing diverges across whichever of these actually run,
because there's only one copy of each answer file and one set of
finalize steps.

## 2. Repository layout

```
packer/
  windows/windows.pkr.hcl        # implemented — every Windows version (Server 2019/2022/2025, 10, 11)
  linux/
    rocky/rocky.pkr.hcl          # implemented — RHEL-family kickstart (rocky9)
    ubuntu/ubuntu.pkr.hcl        # implemented — subiquity autoinstall (ubuntu_lts, ubuntu_26)
    debian/debian.pkr.hcl        # implemented — preseed (debian_latest)
    rhel/ almalinux/ fedora/ opensuse/ oraclelinux/ mint/   # not yet
iso/
  answer-files/
    windows/
      autounattend-windows-server.xml.tpl   # implemented — shared: 2019/2022/2025
      autounattend-windows-client.xml.tpl   # implemented — shared: 11 (10 once it lands)
    rhel-family/
      ks-rocky9.cfg.tpl                     # implemented
      ks-rhel9.cfg.tpl                      # not yet — same pattern once needed
      ks-almalinux9.cfg.tpl
      ks-fedora.cfg.tpl
      ks-oraclelinux9.cfg.tpl
    debian-family/
      user-data-ubuntu-lts.yaml.tpl         # implemented
      meta-data.yaml.tpl                    # implemented — shared, all Debian-family
      user-data-debian.yaml.tpl             # not yet
      user-data-mint.yaml.tpl
    opensuse/
      autoyast-leap.xml.tpl                 # not yet
    cloud-init/
      user-data-clone.yaml.tpl              # implemented — first boot of a cloned Linux VM
      meta-data-clone.yaml.tpl              # implemented
      network-config.yaml.tpl               # implemented
    windows/
      autounattend-windows-clone.xml.tpl    # implemented — first boot of a cloned Windows VM
scripts/
  promote-to-template.sh   # implemented — generalize + copy a live VM into the template library (§5)
ansible/roles/template_finalize/   # implemented — the generalize steps promote runs
```

Every real file above ends in `.tpl` and is rendered via OpenTofu's
`templatefile()` (tofu/modules/vm/libvirt/main.tf), not consumed
directly — the non-`.tpl`, per-version names further down (`ks-rhel9.cfg`,
`autoyast-leap.xml`, …) are this section's original aspirational
listing for OS families not implemented yet, kept for the shape of what
a future entry should look like, not a literal filename to expect.
Windows and Debian-family are both **one shared template per family**
(`os_variant`/the rendered hostname etc. are the only per-instance
difference), not one file per version — confirmed for Windows Server
2019/2022/2025 via `wiminfo` against each real ISO; the RHEL family is
the one place that's genuinely per-`os_key` (`ks-<os_key>.cfg.tpl`),
since each RHEL-family distro's package set/repo config differs enough
to warrant its own file once added.

Packer's `build.pkr.hcl` for each OS points at the shared answer file
under `iso/answer-files/` rather than keeping its own copy.

## 3. Per-OS-family build process

### What's implemented (libvirt, M6 phase B)

```
scripts/build-template.sh rocky9 rocky9-base-2026.10
scripts/build-template.sh windows_server_2022 win2022-base-2026.10
```

`scripts/build-template.sh <os-key> <template-name>` takes the ISO from
environment.yml's `os_iso_paths`, runs the matching Packer build with
Packer's QEMU builder on the libvirt host, and moves the result into the
template library (mode 0444, never overwriting an existing name), the
same library `promote-to-template.sh` (§5) fills. Each build:

- renders **the same answer-file template** the iso_direct path uses
  (Packer's `templatefile()` has OpenTofu's syntax) with DHCP addressing,
  since the build VM sits on QEMU's user-mode network, and an empty
  `management_source`; delivers it the installer's usual way (kickstart
  and preseed over Packer's HTTP server via the boot command, Ubuntu's
  seed and Windows' `Autounattend.xml` on a CD);
- builds on the virtual hardware clones will use: virtio disk/NIC for
  Linux; q35 + SATA (IDE on q35) + e1000e for Windows; legacy BIOS;
  `-cpu host` (EL9 needs x86-64-v2, which QEMU's default CPU lacks);
- generalizes with the **same `template_finalize` role** as promotion,
  via Packer's Ansible provisioner with `finalize_shutdown=false`; Packer's
  `shutdown_command` then powers the VM off (Linux) or starts the role's
  sysprep task (Windows).

**Windows Server edition:** the Desktop Experience (image index 2,
"SERVERSTANDARD") unless Server Core is asked for, in which case index 1
("SERVERSTANDARDCORE"); same layout on the 2019/2022/2025 evaluation
ISOs (checked with `wiminfo`). ISO installs ask with `windows_core = true`
on the host group; Packer builds with `build-template.sh ... --core`
(name such templates `...-core-...`). A cloned VM gets its template's
edition. Client Windows always has the desktop.

A Packer-built template and a promoted one are interchangeable from the
cloning side. Built and clone-tested: Rocky 9, Ubuntu 24.04 and 26.04,
Debian 13, Windows Server 2019/2022/2025, Windows 10 and 11. Build times are roughly an ISO install plus a few
minutes when run one at a time; six in parallel saturated the host's
disks and took over an hour.

The per-family notes below are the original design, which also covers
Hyper-V (`hyperv-iso`), not built yet.

### Windows (Server 2019/2022/2025, Windows 10/11)

- **Builder**: `hyperv-iso` on the Hyper-V backend, `qemu` on the
  libvirt backend.
- **Boot**: vendor ISO + `autounattend.xml` served over Packer's
  built-in HTTP server. The answer file handles edition selection, disk
  partitioning, local administrator password, initial network config,
  and enabling a WinRM listener (needed for Packer's `winrm`
  communicator, and later for Ansible).
- **Provisioners**: install Windows updates (e.g. via a `PSWindowsUpdate`
  script), install **Cloudbase-Init** (the Windows equivalent of
  cloud-init — lets a cloned VM pick up hostname/network config from the
  hypervisor at first boot).
- **Finalize**: `sysprep /generalize /oobe /shutdown` so every VM cloned
  from the template gets a unique SID — this is what actually makes it
  a *template* rather than a one-off VM.
- **Output**: exported Hyper-V VM (VHDX + export folder) copied into the
  template library, or a libvirt qcow2 base image.

### RHEL family (Rocky, RHEL, AlmaLinux, Oracle Linux)

- **Builder**: `qemu` or `hyperv-iso`.
- **Boot**: ISO + kickstart file served over Packer HTTP — partitioning,
  user creation, enabling `sshd`, installing `cloud-init`.
- **RHEL and Oracle Linux** need registration during the build to pull
  packages/updates: RHEL via `subscription-manager register` (a Red Hat
  subscription, or the free Red Hat Developer subscription, works),
  Oracle Linux via its free ULN/yum-repo registration. Rocky and
  AlmaLinux don't need this, being unencumbered rebuilds — the main
  practical difference in an otherwise identical pipeline.
- **Finalize**: `dnf clean all`, remove `/etc/machine-id` and SSH host
  keys, `cloud-init clean` — otherwise every clone would share the same
  machine identity and SSH host keys.
- **Output**: qcow2 (libvirt) or VHDX (Hyper-V).

### Fedora

- Same pipeline as the RHEL family (kickstart, `dnf`, `cloud-init`) —
  no registration needed. Fedora's faster release cadence means these
  templates are worth rebuilding more often than the RHEL-family ones.

### openSUSE/SLES

- **Builder**: `qemu` or `hyperv-iso`.
- **Boot**: ISO + **AutoYaST** profile (its unattended-install format,
  distinct from kickstart/preseed but the same role in the pipeline).
- **Finalize**: `zypper clean`, clear machine-id and SSH host keys,
  `cloud-init clean` if cloud-init is installed.
- SLES additionally needs SCC registration, similar in spirit to RHEL's
  subscription-manager step.

### Debian family (Ubuntu, Debian, Linux Mint)

- **Builder**: `qemu` or `hyperv-iso`.
- **Boot**: ISO + cloud-init `user-data`/`meta-data` (Ubuntu 20.04+,
  Debian 12+, and Mint 21+ all support autoinstall this way); fall back
  to classic preseed only if an older release is ever needed.
- **Provisioners**: ensure `cloud-init` is present and `ssh` is enabled.
- **Finalize**: `cloud-init clean`, clear machine-id and SSH host keys.
- **Linux Mint note**: Mint is Ubuntu-based (Mint Debian Edition is
  Debian-based) — it reuses the Ubuntu/Debian pipeline with Mint's ISO
  and minor answer-file tweaks, rather than needing a separate one.

## 4. Direct ISO-boot path (no Packer)

For a host that shouldn't use a pre-baked template — including the very
first lab build before any templates exist yet:

- The OpenTofu VM module points the hypervisor provider straight at the
  vendor ISO (mounted as a virtual DVD) plus the same answer file from
  `iso/answer-files/`, mounted as a secondary virtual floppy/CD, or
  served over a temporary HTTP endpoint the provider spins up during
  install — the same mechanism Packer uses, just driven by OpenTofu
  instead.
- Both providers support this without extra tooling: `taliesins/hyperv`
  via a DVD drive resource, `dmacvicar/libvirt` via `cdrom`/`cloudinit`
  disk resources.
- Trade-off is time, not capability: the full OS installer runs on every
  `tofu apply` for that host — until it's promoted (§5).

**What the libvirt backend's `iso_direct` path actually does** (each
family needs a genuinely different unattended-install delivery
mechanism, not just a different answer-file format):
  - **RHEL family**: `virt-install --location <iso> --initrd-inject
    <rendered kickstart> --extra-args "inst.ks=file:/<basename>
    console=ttyS0"` — the kickstart file is injected straight into the
    boot initrd and referenced by a kernel argument.
  - **Windows**: no kernel-argument hook exists for Windows Setup: it
    auto-detects an `autounattend.xml` at the root of any attached
    optical/floppy media instead, so a small ISO containing just that
    file is built and attached as a second CD-ROM alongside the vendor
    ISO (`--disk ...,device=cdrom` + `--cdrom <iso>`). Every Windows
    version boots legacy BIOS: `windows_11` is cataloged with
    `os_variant = "win10"` because `win11` makes virt-install pick
    UEFI + TPM, whose "press any key to boot from CD" prompt can't be
    answered unattended. Windows 11's hardware checks are skipped with
    `LabConfig` registry keys in the answer file instead. Windows 11 also
    needs `disk_gb` >= 64 (`min_disk_gb`, enforced at plan time).
  - **Debian family**: subiquity/cloud-init's NoCloud datasource
    expects a labeled `CIDATA` volume containing exact-named
    `user-data`/`meta-data` files at its root — closer to Windows'
    second-CD-ROM pattern than to RHEL's single injected file. A small
    `CIDATA`-labeled ISO is built from both rendered files and attached
    as a second CD-ROM, booted via `--location <iso> --extra-args
    "autoinstall ds=nocloud;s=file:///cdrom/ console=ttyS0"` (still
    `--location`, same as RHEL, since both extract the installer's own
    kernel/initrd the same way — only the seed-delivery mechanism
    differs).
  - See `tofu/modules/vm/libvirt/scripts/create-iso-direct.sh` for the
    exact, current implementation of all three.

## 5. Promoting an ISO-built VM to a Template

This is the intended on-ramp: build the first environment entirely from
ISO (fast to get started, no template pipeline needed yet), then convert
the VMs worth reusing into templates instead of reinstalling from ISO
every rebuild. **Implemented on the libvirt backend (M6 phase A).**

```
scripts/deploy.sh libvirt test-tpl test-tpl --test --no-ansible     # source VMs from ISO
scripts/promote-to-template.sh libvirt test-tpl rocky1 rocky9-base-2026.10
scripts/destroy.sh libvirt test-tpl test-tpl --test
```

`promote-to-template.sh <backend> <environment-instance> <vm-name> <template-name>`:

1. Checks the VM is tagged for that environment and that no template of
   that name exists yet (templates are never overwritten, §8).
2. Runs `ansible/playbooks/finalize-template.yml` (role
   `template_finalize`) against the VM over its existing connection, and
   refuses a domain-joined VM (its machine account would be cloned):
   - **Linux:** installs cloud-init (+ growpart) if missing (Rocky's
     minimal install and Debian's preseed don't have it), removes
     installer-written cloud-init overrides (Ubuntu's disable networking)
     and the install-time network config (NetworkManager keyfiles,
     netplan, ifupdown), runs `cloud-init clean`, empties
     `/etc/machine-id`, deletes the SSH host keys, shuts down.
   - **Windows:** from a one-shot SYSTEM scheduled task (removing the
     WinRM listener ends the Ansible session, and WinRM kills processes
     started from a session when it closes): removes the WinRM HTTPS
     listener, its self-signed certificate and firewall rule, deletes
     the cached `C:\Windows\Panther\unattend.xml` (it holds the
     install-time Administrator password in plain text), resets the NIC
     to DHCP (a static address survives generalize), then
     `sysprep /generalize /oobe /shutdown /mode:vm`.
3. Waits for the VM to shut itself down.
4. Copies its disk into the template library with libvirt
   (`virsh vol-create-from` into the `labape-templates` storage pool, a
   full, flattened copy, mode 0444). The pool is created on first use
   at `template_storage_path` (environment.yml; default
   `<vm_storage_path>/templates`).

The source VM is left shut off and generalized; tear its environment
down afterwards. This is a **manually-triggered** step
(Claude_Docs/Design_System-Overview.md §17.2).

### How a VM is cloned from a template (libvirt)

A host group with `image_source = "packer_template"` and
`template = "<name>"` gets, per VM (`tofu/modules/vm/libvirt`,
`scripts/create-from-template.sh`):

- a qcow2 **overlay** disk whose read-only backing file is the template
  (`virt-install --import`, `backing_store=`), sized `disk_gb` — creating
  a VM takes seconds, and the template is never written to;
- a small CD with the VM's first-boot identity:
  - **Linux:** a cloud-init NoCloud seed (`CIDATA`) from
    `iso/answer-files/cloud-init/`: hostname, the `labape` user with the
    SSH key and passwordless sudo, static network matched by the VM's
    MAC (fixed per environment + VM name, so every cloud-init renderer
    can match it), and root-filesystem growth, including an LVM root
    (Rocky);
  - **Windows:** `unattend.xml` from
    `iso/answer-files/windows/autounattend-windows-clone.xml.tpl` (the
    specialize/oobeSystem half of the install answer file: computer
    name, Administrator password, AutoLogon once, FirstLogonCommands for
    WinRM, and extending C: into a bigger disk) plus `firstboot.ps1`,
    which sets the static IP by interface index once the NIC is up and
    logs to `C:\Windows\Temp\labape-firstboot.log`. The answer file
    must be named `unattend.xml`: Setup only reads `Autounattend.xml`
    from removable media for the windowsPE pass.

Disk bus and NIC model match what the template was installed with
(virtio for Linux; SATA + e1000e for Windows), and Windows stays on
legacy BIOS (§4). From Ansible's side a clone is indistinguishable from
an iso_direct VM: same inventory, same `site.yml`. Destroying a clone
deletes only its overlay and seed CD (`safe_undefine`).

## 6. Refreshing an Existing Template

Promotion (§5) answers "how does a template get created." It doesn't
answer a different, equally common question: **a template already
exists, and a package in it needs a version bump** (or an OS patch, or a
config change) — the image itself needs to move forward, not just the
next lab build.

Two ways to get there, and neither is "edit the template in place" —
templates stay disposable/rebuildable, never hand-patched:

### Option A — Full Packer rebuild

Re-run `packer build` for that OS from ISO + kickstart/answer-file +
provisioners (§3), producing a new dated template
(`win2022-base-2026.10`) from scratch. Fully reproducible — the template
is always exactly what its build recipe says it is, nothing more. Best
when the change is significant enough to want a clean rebuild anyway
(a new OS point release, a provisioner script change), but it re-runs
the entire OS install every time, which is slow for "just bump one
package."

### Option B — Incremental refresh (recommended for small changes)

**Implemented on the libvirt backend (M6 phase C):**

```
scripts/refresh-template.sh libvirt rocky9-base-2026.10 rocky9-base-2026.10.1 --os rocky9
scripts/refresh-template.sh libvirt win2022-desktop-2026.10 win2022-desktop-2026.11 --os windows_server_2022
```

It clones the template into a throwaway `test-refresh-<id>` environment
(`deploy.sh --test --no-ansible`, the clone's disk sized from the
template's own virtual size), runs `ansible/playbooks/refresh-template.yml`
(all package upgrades on Linux, Windows security/critical updates and
rollups, rebooting as often as needed) or the playbook given with
`--playbook`, promotes the result with `promote-to-template.sh`
(flattening the overlay into a self-contained copy), and destroys the
environment. `--os` is required because a template name doesn't reliably
say which OS it is; `--ip-offset` picks the throwaway VM's address (the
pre-flight check still guards it). On failure the environment is left for
inspection and the script prints the teardown command.

For the case you described — one package needs a newer version, nothing
else about the image is changing — rebuilding the OS from ISO is wasted
work. Instead, `scripts/refresh-template.sh <backend> <template-name>
<new-template-name>` does:

1. Boots a **throwaway VM** from the existing template (`<template-name>`)
   using the same OpenTofu `vm` module every other VM uses —
   `image_source: packer_template` — not from ISO.
2. Runs the **same Ansible** that would configure a normal lab host
   against it: either the regular software manifest (§11 in Claude_Docs/Design_System-Overview.md)
   with the updated package version, or a dedicated OS-patch playbook
   (`apt/dnf/zypper upgrade`, Windows Update) — whichever is actually
   driving the change. This is deliberately the same playbook path
   as normal configuration, not a separate update mechanism to maintain.
3. Runs `promote-to-template.sh` (§5) against that same throwaway VM —
   the finalize/generalize steps are identical whether the VM came from
   ISO or from an existing template.
4. Destroys the throwaway VM.

The result is a **new, separately named template**
(`win2022-base-2026.10.1`, or whatever scheme distinguishes it from the
original `2026.10` build) — the old template is never overwritten.
Host groups keep referencing the template name they were already
pinned to until you deliberately move them to the new one, and the old
template stays available until nothing references it (§8's retirement
policy already covers this — refresh just adds another reason a new
version gets created).

This is why `promote-to-template.sh` was designed as a standalone,
manually-triggered step in the first place (§5): it doesn't care whether
its source VM came from ISO or from cloning an existing template, so
refresh didn't need a second finalize/export implementation — just a
different way of getting to "a VM ready to be finalized."

## 7. Choosing per host

Selection happens per host group, e.g.:

```yaml
host_groups:
  - name: winsrv
    image_source: packer_template   # win2022-base-2026.09
  - name: linws
    image_source: iso_direct        # testing Mint 22 once, not templating it yet
```

## 8. Versioning & rebuild cadence

- Name templates with a build date, e.g. `win2022-base-2026.09`,
  `rocky9-base-2026.09` — the same convention whether the template came
  from a Packer build, a promotion (§5), or a refresh (§6).
- Rebuild/re-promote/refresh templates periodically (Windows: Patch
  Tuesday cadence; most Linux: monthly; Fedora: more often given its
  release pace) instead of patching live VMs — every fresh environment
  then starts from a current, consistent baseline.
- Don't delete a template immediately after rebuilding or refreshing it
  — keep it until nothing references it, so in-flight environments
  aren't broken out from under them.
