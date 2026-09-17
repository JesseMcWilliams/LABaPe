# Base Images: Packer Templates vs. Direct ISO Boot

Both hypervisor backends (Hyper-V, libvirt) need a starting point for
each VM's disk. LABaPe supports two ways to get there, chosen per
host/profile, not globally.

## 1. The two paths

| | Packer-built template | Direct ISO boot |
|---|---|---|
| When the OS install runs | Once, ahead of time | Every `tofu apply` |
| Per-VM provisioning time | Minutes (clone + boot) | As long as the OS installer takes |
| Extra pipeline/state to maintain | Yes — template library | No |
| Best for | OS versions you rebuild often (the bulk of day-to-day use) | One-off or rarely used OS versions; evaluating a new release before deciding to template it |

Both paths use **the same answer files** (`autounattend.xml` for
Windows, kickstart for the RHEL family, cloud-init autoinstall for the
Debian family) stored once under `iso/answer-files/`. Packer just wraps
that same unattended install into a repeatable pipeline and snapshots
the result; direct ISO boot hands the identical answer file to the
hypervisor provider on every apply. They never drift from each other
because there's only one copy of each answer file.

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
    rocky/ rhel/ almalinux/ ubuntu/ debian/ mint/
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
    debian-family/
      user-data-ubuntu-lts.yaml
      user-data-debian.yaml
      user-data-mint.yaml
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

### RHEL family (Rocky, RHEL, AlmaLinux)

- **Builder**: `qemu` or `hyperv-iso`.
- **Boot**: ISO + kickstart file served over Packer HTTP — partitioning,
  user creation, enabling `sshd`, installing `cloud-init`.
- **RHEL specifically**: needs `subscription-manager register` with a
  Red Hat subscription (or the free Red Hat Developer subscription)
  during the build to pull packages/updates from Red Hat's CDN. Rocky
  and AlmaLinux don't need this, being unencumbered rebuilds — this is
  the main practical difference in an otherwise identical pipeline.
- **Finalize**: `dnf clean all`, remove `/etc/machine-id` and SSH host
  keys, `cloud-init clean` — otherwise every clone would share the same
  machine identity and SSH host keys.
- **Output**: qcow2 (libvirt) or VHDX (Hyper-V).

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

For a host that shouldn't use a pre-baked template:

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
  `tofu apply` for that host.

## 5. Choosing per host

Selection happens per host/profile, e.g.:

```yaml
windows_server:
  image_source: packer_template   # win2022-base-2026.09
linux_workstation:
  image_source: iso_direct        # testing Mint 22 once, not templating it yet
```

This lets a fast Packer template be used for OSes rebuilt often while a
rare or newly-evaluated OS version boots straight from ISO without first
building a pipeline for it.

## 6. Versioning & rebuild cadence

- Name templates with a build date, e.g. `win2022-base-2026.09`,
  `rocky9-base-2026.09`.
- Rebuild templates periodically (Windows: Patch Tuesday cadence; Linux:
  monthly) instead of patching live VMs — every fresh environment then
  starts from a current, consistent baseline.
- Don't delete a template immediately after rebuilding it — keep it
  until nothing references it, so in-flight environments aren't broken
  out from under them.
