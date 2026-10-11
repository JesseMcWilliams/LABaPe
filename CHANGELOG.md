# Changelog

LABaPe's releases. The web interface shows the installed release on the
admin dashboard and says when a newer one is published on GitHub.
Versions follow `vMAJOR.MINOR.PATCH`; the image is stamped with
`git describe --tags`.

## [Unreleased]

### Changed
- Every action shows a short tooltip, and every action that changes
  something asks first in a dialog with a fuller note (Continue or
  Cancel); the browser's confirm boxes are gone.
- Hosts, Networks, catalog items, files and templates follow the
  Environments pattern: a list with Add at the top right, and each name
  opening the entry's own page, where editing happens. Non-admins see
  Hosts and Networks read-only, limited to what they can use.

### Added
- Maintenance mode: changes pause (503 with the reason) while reads,
  status and live logs keep working; the worker starts no new jobs and
  pauses scheduled work; banner on every page; turn on now or schedule
  it; a weekly maintenance window with advance notice.
- Database migrations (Alembic): existing databases are stamped at the
  baseline on first start; `labape db-status`; the dashboard shows the
  database revision. A database newer than the code stops startup with
  an explanation instead of failing later.
- Platform CA (step-ca) in the stack: web-interface deploys give every
  VM a certificate (key made on the VM, CSR signed through the API with
  a per-job token, renewed daily by a timer or scheduled task) and the
  LABaPe root in its trust store. The web interface can take its own
  certificate from the CA over ACME (`LABAPE_CADDYFILE=Caddyfile.labape-ca`).
  Root download and CA health on the dashboard.
- Remote KVM hosts over SSH (`qemu+ssh://user@host/system`): jobs run
  on the host with its own toolchain (state through an SSH tunnel), host
  stats and the template library come over SSH, and the host page shows
  the worker's key and a connection check. `labape worker-key`.
- Installer profiles: `install-labape.py --profile full|kvm|web`.
- Files: download a file from a URL as a worker job (resumable, HTTPS
  only unless allowed, link-local addresses refused), and check
  downloads and uploads against a vendor's SHA-256, SHA-1, SHA-512 or
  MD5. Settings > Downloads (allow HTTP, proxy).
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
