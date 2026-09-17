# Base Images: Packer Templates, Direct ISO Boot, and Promotion

Both hypervisor backends (Hyper-V, libvirt) need a starting point for
each VM's disk. LABaPe supports three related workflows, chosen per
host/host-group, not globally: build a template with Packer up front,
boot straight from ISO, or boot from ISO now and promote the result into
a template later.

## 1. The three workflows

| | Packer-built template | Direct ISO boot | ISO boot, then promote |
|---|---|---|---|
| When the OS install runs | Once, ahead of time | Every `tofu apply` | Once, during initial lab build |
| Per-VM provisioning time after this | Minutes (clone + boot) | As long as the OS installer takes, every time | Minutes, after the one-time promotion |
| Extra pipeline/state to maintain | Yes — template library | No | Template library, but built lazily |
| Best for | OS versions already known to be reused often | One-off/rarely used OS versions; evaluating a new release | **Recommended default day-one workflow** — stand a lab up fast from ISO, then convert the VMs worth keeping into templates instead of reinstalling next time |

`image_source_default: packer_template` (DESIGN.md §10) is the default
for new host groups, but nothing stops a host group from starting on
`iso_direct` and moving to a promoted template once it's proven out —
that transition is exactly what §5 below covers.

All three workflows use **the same answer files** (`autounattend.xml`
for Windows, kickstart for the RHEL family, cloud-init autoinstall for
the Debian family, AutoYaST for openSUSE/SLES) stored once under
`iso/answer-files/`. Packer wraps the unattended install into a
repeatable pipeline; direct ISO boot hands the identical answer file to
the hypervisor provider on every apply; promotion reuses the same
generalize/finalize steps a Packer build would run, just against an
already-installed VM instead of inside an isolated build. Nothing
diverges because there's only one copy of each answer file and one set
of finalize steps.

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
   naming convention Packer output uses (§6) — a Hyper-V export or a
   libvirt qcow2 conversion, depending on backend.
4. Registers the result so any host group can reference it going forward
   via `image_source: packer_template` (the name is kept for consistency
   even though this template didn't come from a Packer build — the
   consuming side, OpenTofu, doesn't care how a template was produced).

This is a **manually-triggered** step (DESIGN.md §17.2) — you decide
when a lab VM is done enough to become a reusable template, rather than
the tooling guessing.

## 6. Choosing per host

Selection happens per host group, e.g.:

```yaml
host_groups:
  - name: winsrv
    image_source: packer_template   # win2022-base-2026.09
  - name: linws
    image_source: iso_direct        # testing Mint 22 once, not templating it yet
```

## 7. Versioning & rebuild cadence

- Name templates with a build date, e.g. `win2022-base-2026.09`,
  `rocky9-base-2026.09` — the same convention whether the template came
  from a Packer build or a promotion (§5).
- Rebuild/re-promote templates periodically (Windows: Patch Tuesday
  cadence; most Linux: monthly; Fedora: more often given its release
  pace) instead of patching live VMs — every fresh environment then
  starts from a current, consistent baseline.
- Don't delete a template immediately after rebuilding it — keep it
  until nothing references it, so in-flight environments aren't broken
  out from under them.
