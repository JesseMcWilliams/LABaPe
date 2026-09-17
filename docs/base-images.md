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

`image_source_default: packer_template` (DESIGN.md §10) is the default
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
  windows/
    2019/ 2022/ 2025/
      build.pkr.hcl
      variables.pkr.hcl
      scripts/
        provision.ps1
  linux/
    rocky/ rhel/ almalinux/ fedora/ opensuse/ oraclelinux/ ubuntu/ debian/ mint/
      <version>/
        build.pkr.hcl
        variables.pkr.hcl
        scripts/
          provision.sh
iso/
  answer-files/
    windows/
      autounattend-2019.xml
      autounattend-2022.xml
      autounattend-2025.xml
      autounattend-win10.xml
      autounattend-win11.xml
    rhel-family/
      ks-rocky9.cfg
      ks-rhel9.cfg
      ks-almalinux9.cfg
      ks-fedora.cfg
      ks-oraclelinux9.cfg
    debian-family/
      user-data-ubuntu-lts.yaml
      user-data-debian.yaml
      user-data-mint.yaml
    opensuse/
      autoyast-leap.xml
scripts/
  promote-to-template.sh   # generalize + export a live ISO-built VM into a template
```

Packer's `build.pkr.hcl` for each OS points at the shared answer file
under `iso/answer-files/` rather than keeping its own copy.

## 3. Per-OS-family build process

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

## 5. Promoting an ISO-built VM to a Template

This is the intended on-ramp: build the first environment entirely from
ISO (fast to get started, no template pipeline needed yet), then convert
the VMs worth reusing into templates instead of reinstalling from ISO
every rebuild.

`scripts/promote-to-template.sh <backend> <vm-name> <template-name>`
does, against the already-installed VM:

1. Runs the **same finalize steps** §3 lists for that OS family over the
   VM's existing WinRM/SSH connection — `sysprep` for Windows,
   `cloud-init clean` + machine-id/SSH-host-key removal for the RHEL,
   Debian, and Fedora families, AutoYaST-equivalent cleanup + `zypper
   clean` for openSUSE/SLES.
2. Shuts the VM down.
3. Exports/converts its disk into the template library using the same
   naming convention Packer output uses (§8) — a Hyper-V export or a
   libvirt qcow2 conversion, depending on backend.
4. Registers the result so any host group can reference it going forward
   via `image_source: packer_template` (the name is kept for consistency
   even though this template didn't come from a Packer build — the
   consuming side, OpenTofu, doesn't care how a template was produced).

This is a **manually-triggered** step (DESIGN.md §17.2) — you decide
when a lab VM is done enough to become a reusable template, rather than
the tooling guessing.

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

For the case you described — one package needs a newer version, nothing
else about the image is changing — rebuilding the OS from ISO is wasted
work. Instead, `scripts/refresh-template.sh <backend> <template-name>
<new-template-name>` does:

1. Boots a **throwaway VM** from the existing template (`<template-name>`)
   using the same OpenTofu `vm` module every other VM uses —
   `image_source: packer_template` — not from ISO.
2. Runs the **same Ansible** that would configure a normal lab host
   against it: either the regular software manifest (§11 in DESIGN.md)
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
