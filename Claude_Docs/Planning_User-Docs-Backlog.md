# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Windows 10 and Windows 11 workstations (`windows_10`, `windows_11`) install unattended and domain-join; Windows 11 host groups must set `disk_gb` >= 64 or the plan fails.
- `ubuntu_lts`, `ubuntu_26` and `debian_latest` all install unattended and join the domain as Linux workstations.
- Host-group names must be unique across environments sharing one libvirt host; a create refuses another workspace's VM.
- Rotating the Windows bootstrap password: change it on running hosts first, then the vault (Claude_Docs/Reference_Credentials.md §9); VMs aren't reinstalled.
- Windows VMs on the libvirt backend use legacy BIOS, so Secure Boot/TPM scenarios aren't supported.
- `deploy.sh`/`destroy.sh` options: `--test`, `--no-ansible`, `--env-file`, `--directory-manifest` (throwaway test environments next to a long-lived one).
- Templates: `scripts/promote-to-template.sh` and host-group `image_source = "packer_template"` + `template = "<name>"`; `template_storage_path` in environment.yml.
- `scripts/build-template.sh <os-key> <template-name>`: build a template with Packer (installed on the libvirt host in ~/.local/bin, plugins via `packer init`).
- Windows hosts get .NET Framework 4.8 automatically where an older 4.x is installed (Server 2019); Firefox can't be installed on Server 2019 Core.
- Windows Server installs the Desktop Experience by default; host group `windows_core = true` (ISO) or `build-template.sh --core` (template) for Server Core.
- Web interface (phase 10a): installing the container stack, Authentik setup, break-glass access, registering a KVM host, deploying and destroying from the browser (container/README.md covers the admin side; no end-user guide yet).
- `destroy.sh --yes --delete-workspace` for unattended teardown; a template clone's disk is raised to at least the template's size.
- Networks: `environment.yml` `networks:` catalog (CIDR, gateway, DNS, bridge, static/DHCP, static pools, DHCP scope, reserved); host groups set `network` and `addressing`; profiles can set `static_ip_offsets` per network; plans breaking the catalog are refused. Web UI: Networks page, Hosts → Networks, network/addressing per host group. `refresh-template.sh --network/--addressing`.
- Installer: `sudo deploy/install-labape.py` (steps, `--resume`, `--step`, `--dry-run`, answers file); `labape bootstrap FILE.json` in the container registers hosts and networks; Refresh addresses button / `scripts/refresh-addresses.sh` for DHCP environments.
- Web UI batch A: host names per host group (unique across the KVM host); template dropdown by OS with minimum warnings; clear field errors (and the admin Errors page); user menu with Reload rights; masked credentials (Show / Show large / Copy); audit JSON export.
- KVM host resources and load: shown in the deploy form (with the new environment projected) and on Hosts; warnings at 4 vCPUs per CPU, 90% RAM committed, 15% disk free; environments over 100% RAM committed are refused.
- Files tab: upload ISOs, installers and other files (resumable, checksummed, duplicates refused); set each ISO's OS and the default per OS (used for builds and ISO installs); template library with edit, refresh and build; Promote to template from an environment.
- Catalog: browse services, applications and packages, request new ones; admins create, publish, retire, import and export YAML; host groups add services/applications/packages on top of the template.
- Admin Settings page: file locations, daily database backups (Back up now, download, restore with pg_restore), history retention, syslog forwarding of the audit trail, SMTP and job-finished emails.
- Group search: Settings > Directory with a read-only Authentik token (view groups only); group fields suggest as you type and still accept typed names.
- Dashboard (landing page): KVM host and environment load with 24-hour trends; admins see sign-in, secrets, SMTP/syslog health and the installed version against the latest release. VMs need qemu-guest-agent (installed automatically on new VMs) for guest free space and the responding state.
- Lists open each entry's own page (hosts, networks, catalog items, files, templates); Add is at the top right. Non-admins see Hosts and Networks read-only. Settings > Branding renames the app.
- Files: Download from URL (HTTPS; HTTP and a proxy in Settings > Downloads) and expected-hash checks (SHA-256, SHA-1, SHA-512, MD5) on downloads and uploads.
- Remote KVM hosts (qemu+ssh) and installer profiles (--profile kvm on the KVM host, --profile web for the web interface); authorize the worker key shown on the host page.
- Platform CA: VM certificates and renewal, trusting the LABaPe root in browsers (dashboard download), Caddy on the platform CA (LABAPE_CADDYFILE), backing up secrets/ca_password with the labape-ca volume.
- Tooltips on every action; confirmation dialogs for every change (what each one says).
- Maintenance mode (Settings): what pauses and what keeps working, scheduling it, the weekly maintenance window and its notice banner.
