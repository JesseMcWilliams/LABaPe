# Changelog

LABaPe's releases. The web interface shows the installed release on the
admin dashboard and says when a newer one is published on GitHub.
Versions follow `vMAJOR.MINOR.PATCH`; the image is stamped with
`git describe --tags`.

## [Unreleased]

### Changed
- Hosts, Networks, catalog items, files and templates follow the
  Environments pattern: a list with Add at the top right, and each name
  opening the entry's own page, where editing happens. Non-admins see
  Hosts and Networks read-only, limited to what they can use.

### Added
- Settings > Branding: rename the app (top bar, browser title, sign-in
  page, emails). The app name links to the dashboard.

## [0.2.0] - 2026-10-11

This is the first tagged release. It covers everything built so far.

### Command line (OpenTofu, Ansible, Packer)
- Environments on libvirt/KVM (recommended) and Hyper-V. Windows Server
  2019/2022/2025, Windows 10/11, Rocky 9, Ubuntu 24.04/26.04 and
  Debian 13 install unattended from an ISO or clone from a template.
- Domain services: AD DS promotion, Windows and Linux domain join,
  directory objects (OUs, groups, users), software from a catalog.
- Base images: Packer builds, promote-to-template and template refresh.
- Network catalog with static pools, DHCP discovery and address refresh.

### Web interface (container stack)
- Authentik (OIDC) sign-in, roles from groups, break-glass access, audit
  log, KVM host registration, deploy and destroy with live job logs.
- Networks and IP address management.
- Files: resumable uploads, ISOs, installers, the template library, and
  build, refresh and promote jobs.
- Catalog of services, applications and packages, with requests and
  YAML import/export.
- Settings: file locations, daily database backups, history retention,
  syslog forwarding of the audit trail, SMTP job emails, and Authentik
  group search.
- **Dashboard** (new landing page): KVM host CPU, RAM, free space and
  disk activity, and environments with each VM's state, CPU, RAM, free
  disk and disk activity, all with 24-hour trends. Admins also see every
  host and environment, sign-in methods, secrets back ends, SMTP and
  syslog health, and the installed version against the latest release.

### Installer
- `deploy/install-labape.py`: a from-scratch install of the KVM host and
  the web stack, step by step, with an answers file.

### Changed
- Every VM created by virt-install gets a qemu-guest-agent channel, and
  Ansible installs the agent: `qemu-guest-agent` on Linux, the virtio-win
  guest tools on Windows. This lets the dashboard show free space inside
  the guest and whether the agent responds.
