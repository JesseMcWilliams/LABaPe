# Changelog

LABaPe's releases. The web interface shows the installed release on the
admin dashboard and says when a newer one is published on GitHub.
Versions follow `vMAJOR.MINOR.PATCH`; the image is stamped with
`git describe --tags`.

## [Unreleased]

## [0.5.0] - 2026-10-15

The stack now runs its own mail server, Mailpit, with an Admin -> Mail page and forwarding to real inboxes; alerts default to its inbox. The project is licensed AGPL-3.0-only, the Python dependencies are locked with hashes, and the software bill of materials is regenerated on every release. Updates now recreate Caddy when its bind-mounted Caddyfile changed.

### Added
- A stack's monitoring alerts go to a Mailpit inbox (`alerts@<host name>`) until real recipients are set, and Settings → Email and Monitoring link to the Mailpit page.
- Every stack runs its own mail server, Mailpit, in the base install. Settings →
  Email is set to it the first time the stack starts (unless you had set up
  Email yourself), so alerts and job emails are caught and can be read with
  nothing configured. **Admin → Mail** shows it, opens its web page (admins
  only, at `/mail/`), sends a test email, and sets up forwarding to real inboxes
  through an SMTP server you give it, for the addresses and domains you list.
- The Python packages in the image are locked: `container/requirements.txt`
  pins every one (LABaPe's dependencies, Ansible and pywinrm) with hashes,
  for amd64 and arm64, and the image build installs from it, checking the
  hashes. Rebuilds now repeat, and the Debian copies of `requests`,
  `urllib3`, `certifi` and `idna` (old) are replaced by the locked ones.
  `tools/helpers/lock-python.sh` refreshes the lock.
- LABaPe is licensed under the GNU Affero General Public License v3
  (`LICENSE`); the web interface has a footer linking to the source, as the
  license asks of a network service.
- The release script (`tools/helpers/release.sh`) also takes the lab image's inventory, regenerates the software bill of materials for the new version
  and attaches the CycloneDX file to the GitHub release.
- A software bill of materials: `Claude_Docs/Reference_SBOM.md` lists every
  Python and npm package, container image, engine tool, Ansible collection,
  provider and plugin LABaPe uses, with versions, licenses and what each is
  for, and `sbom/labape.cdx.json` is the same in CycloneDX form for scanners.
  `tools/helpers/sbom.py` regenerates both, and a test fails when they're
  out of date.
- VMs read their own secrets: a VM signs in to OpenBao with its platform
  certificate and reads the secrets its owners stored for it, and the
  shared ones whose VM list names it (`sudo labape-secret get NAME` on
  Linux, `Get-LabapeSecret NAME` on Windows). Each shared secret's VM list
  is set on the environment's Secrets panel; other VMs can't read it.

## [0.4.0] - 2026-10-15

LABaPe 0.4.0 spreads environments over several KVM hosts and moves running VMs between them, signs people in across linked stacks, and makes secrets something people and tests can use: every secret is checked and recovered where it can be, Windows VMs get a password of their own per account, environment secrets are on their page and in OpenBao (with its own sign-in), and the browser connects as any of the environment's accounts, RDP, SSH, xrdp or VNC. Environments can have a lab mail server (Mailpit); Catalog → Updates downloads new .NET, Chrome, KeePassXC and PuTTY versions weekly for offline installs; Claude Code is in the catalog; each KVM host keeps its files beside its VMs; and every list pages, filters and exports.

### Fixed
- A web update that recreated OpenBao left it sealed: the API answered 503 to its login, the worker restarted forever, the health check failed and the rollback failed the same way. The update agent now runs `stores.sh unseal` after it brings the stack up (and after a rollback does).
- An install made by `install-labape.py` could never update from the web interface: the update agent called its checkout dirty because of the installer's own `container/compose.override.yaml` (and `.env` backups). Those files are now git-ignored and the agent looks at tracked changes only.
- A changed Caddyfile reaches the running Caddy: the file is bind-mounted, so
  replacing it (git, or `deploy-web.sh`) left the container on the old one.
  The update agent and `deploy-web.sh` now recreate Caddy when they differ.
- The image build retries the Packer plugin installs (a dropped connection
  made Packer crash and the build fail).
- OpenBao's sign-in page offers **OIDC** (your LABaPe account), not only a
  token.
- OpenBao's web UI no longer asks for a client certificate (a smart card
  prompt on Windows), and its tile in Authentik opens OpenBao's sign-in at
  the stack's host name instead of `localhost`.
- Rotating a Windows Administrator password no longer depends on a
  detached change finishing in its first second: a detached process lost
  its access to the account database once WinRM closed the shell that
  started it. The change now runs in the same shell.
- Destroying an environment now also clears its own accounts' passwords
  and any value a rotation was trying, not only its Administrator
  passwords, credentials sheet and inventory.
- Preparing a host that had no NetworkManager (e.g. a Debian cloud image)
  no longer leaves it without DNS servers.
- A template copied to a host that had it once before no longer fails
  with a duplicate-key error.
- KVM and stack hosts never sleep. A desktop install's login screen
  suspended kvm1 15 minutes after boot, stopping its VMs and the web stack.
  The installer's new `Prereq.NoSleep` step and a host's access-account
  setup mask the sleep targets (the installer also sets logind to ignore
  idle, the suspend key and the lid), and a host's page warns when it can
  still sleep.

### Changed
- Drive space alerts: **Free space below (%)** in Settings → Monitoring
  (default 20%), for every host and every filesystem, and a host can set its
  own. It replaces "Disk full at (%)", which only covered stack hosts (KVM
  hosts warned at a fixed 90% full); a saved value isn't carried over.
- `stores/stores.sh setup` makes the stack's OpenBao the default store for
  new environments' secrets when it first connects LABaPe to it (a later
  choice in Settings → Secret stores stays).
- Settings is in sections with a list on the left: Sign-in, Stack, Updates
  and backups, Environments, Files, Secrets, Remote access.
- Releases are cut with `tools/helpers/release.sh`: the CHANGELOG section,
  the version in `app/pyproject.toml` and `web/package*.json` (both had
  stayed at 0.2.0), the docs' "the release after" wording, optionally the
  `.docx` guides, the signed tag and the GitHub release.
- Authentik is served over HTTPS on port 9445 by the stack's Caddy, with
  the web interface's certificate, and LABaPe signs in through it (it used
  plain HTTP on 9000). Existing stacks: see the admin guide.
- The user and admin guides cover everything since v0.2.0: monitoring,
  backups and rebuilding a stack, linked stacks, quotas, leases, snapshots,
  accounts, remote access, the syslog collector, tokens and metrics.

### Added
- New catalog applications: .NET 8 and 10 (runtime, hosting bundle, SDK),
  Google Chrome, KeePassXC, PuTTY and Claude Code. On Windows they install
  from the newest copy Catalog → Updates downloaded (offline), or from
  Chocolatey until there is one; on Linux from the vendors' repositories.
  Claude Code installs through npm for every user and updates itself
  weekly. Catalog applications can also be EXE installers and npm packages.
- Catalog → **Updates**: download sites checked weekly (and on demand);
  new versions land in Files → Installers with the vendor's hash checked,
  and only the newest few of each are kept. Sources can be Microsoft's .NET
  feed, a GitHub release, a fixed link, a page that links the newest
  version, or a Chocolatey package; admins add their own. .NET 8 and 10
  (runtime, hosting bundle, SDK), Chrome, KeePassXC and PuTTY are there to
  start.
- A mail server per environment: the catalog service **Mail server
  (Mailpit)** on a Linux VM catches what the environment's VMs send to it
  (port 25) and shows it in a web UI (port 8025, HTTPS, user `mailpit`, its
  password on the Secrets panel). Nothing leaves unless an admin sets
  Settings → **Lab mail relay**, and then only to the recipients listed.
- Desktop sessions on Linux VMs: a host group can have **xrdp** (a desktop
  over RDP for every account, local or domain) and **VNC** (a desktop of
  its own for each local account), set when creating the environment or on
  its page (then Redeploy). The Secrets panel's **Connect** offers them.
- **Connect as an account**: on the environment's Secrets panel, an
  account's **Connect** opens RDP or SSH in the browser signed in as that
  account (a local account on its VM; a domain account on the member
  servers, the domain controllers for domain admins, and the Linux VMs),
  its password filled in by LABaPe. Linux VMs accept password sign-in for
  accounts (LABaPe's admin user stays key-only) after their next redeploy.
- Each KVM host has a **Files path** on its settings (by default beside its
  VM folder: `/data/VMs/LABaPe` -> `/data/Files`, with `ISOs`, `Installers`
  and `Other`): the copies jobs need are kept there instead of a hidden
  cache, and changing it moves the files.
- The environment's Secrets panel pages like the other lists and has a
  search box and filters (group, VM, store).
- Every host LABaPe sets up gets `smartmontools`, `lm-sensors` (its chips
  detected once, automatically) and `ethtool`: the installer on every
  profile, and a host's access setup for hosts installed before.
- People can use OpenBao itself: its web UI and `bao login -method=oidc`,
  signing in with their LABaPe account (`LABAPE_OPENBAO_PEOPLE=yes`,
  `stores/stores.sh setup`). Owners read their environment's folder and
  change `user/` and `vm/`; admins read every environment's. LABaPe keeps
  the owners in OpenBao in step; the policies come from a fixed template
  that `stores.sh` writes when the update agent asks, so LABaPe can't grant
  itself anything new.
- An environment's secrets on its page: owners and admins see every
  secret of the environment, show or copy any (each audited), and store,
  replace or delete their own, for the whole environment or one VM. In
  OpenBao each environment's secrets sit in a folder of their own,
  `env/<environment>/` (`accounts/` for LABaPe's, `user/` and `vm/<vm>/`
  for the owners'); existing ones move there the next time they're read.
  A new secret joins its environment's store.
- A password of its own per VM: each Windows member server's
  Administrator and `labape-admin2`, and each of the environment's own
  local accounts on each VM, get a unique password, stored per VM. A
  redeploy gives deployed environments theirs; the credentials sheet lists
  them per VM, and rotation and the secret checks work per VM. Domain
  controllers keep the domain's.
- Recovery pairs: environments with Windows hosts get a second admin,
  `labape-admin2` (in the domain and on each member server), whose
  password LABaPe keeps and rotates apart from Administrator's; each of a
  pair sets the other's password again when it's lost, so the domain
  Administrator is now recoverable.
- Secrets in tabs: Stack, KVM hosts (with each host's access account) and
  Environments, whose secrets filter by environment, store, VM, local or
  domain, password or key, and last change. Cleared values (spent job
  tokens, destroyed environments) are hidden unless asked for.
- Secret health: each secret is checked (every quarter of its rotation
  period, at least daily; weekly when LABaPe doesn't rotate it) and shows
  **Recoverable** yes/no. A failed check on a recoverable secret puts a
  working value back (an earlier value, or a new one set with another
  credential); otherwise it raises an alert. **Check now** on Secrets.
- A stack answers on its web name, host name, FQDN and address with one
  certificate from the platform CA carrying all of them as SANs, on the
  web interface and Authentik, renewed daily when due
  (`container/caddy/web-tls.sh`, installer step `Stack.WebTls`).
- A KVM host's Drives and volumes hide loop devices unless **Show loop
  devices** is ticked. Host setup and the installer install lm-sensors
  (temperatures), and a host's page says when it's missing.
- Paged lists (Environments, Jobs, Files, Catalog, Syslog): 50 rows by
  default, each user's own page size kept in their profile. Exports ask
  for this page, rows from-to or all: Environments and Files as CSV, Jobs
  as JSON or CSV. Environments have filters.
- Files: an OS filter on every tab (the OSes that tab's files are for);
  a file's OS is given when it's uploaded or downloaded (required for
  installers), and other files can carry OS targets too. Catalog and
  Networks lists have filters.
- Sign-on between linked stacks (Admin → Linked stacks → Sign-on): an
  admin lets a linked stack's users sign in here with their own accounts,
  per link and per side. They appear as `<user>@<stack>` and keep their
  role groups. Each Authentik trusts the other's platform CA.
- Move a host group to another KVM host while its VMs run (environment
  page → VMs → Move host group): live migration with the disks copied,
  then a deploy records the new host.
- One environment over several KVM hosts: each host group can name its
  own KVM host on the new-environment form. Each host's part runs on that
  host in its own OpenTofu workspace, then Ansible runs once over all the
  VMs. Power, snapshots, VM details and consoles go to each VM's own host.
- A logo (Settings → Branding): PNG, JPEG or WebP, in the top bar, on the
  sign-in page and as the tab icon.
- Best-fit placement: Suggest best host on the new-environment form ranks
  the KVM hosts (memory, CPU, running jobs, templates there) with reasons;
  the API places an environment created without a host on the best one.
  Optionally, every template is copied to every KVM host daily.
- An environment's own accounts: domain users and local users on chosen
  roles' hosts, created at deploy with generated passwords, listed on the
  credentials sheet and rotated weekly (environment page → Accounts).
- Quotas (Admin → Quotas): limits on vCPUs, memory, VM disk and environments
  per user, group or everyone, checked when an environment is created or
  deployed again; users see theirs on the Environments page.
- Environment leases (off until an admin turns them on): reminder emails
  before the end, the VMs shut down at the end, destroyed (or kept) after a
  grace period; owners extend, admins set any end or none.
- VM snapshots: take, revert and delete on the VM page (internal libvirt
  snapshots with memory, BIOS VMs), and optionally a snapshot of every VM
  before a deploy changes an environment (Settings → VM snapshots).

### Fixed
- Destroying an environment whose VMs had snapshots failed: the teardown
  now drops libvirt's snapshot records with the VM.

## [0.3.0] - 2026-10-14

Everything since v0.2.0: the update agent with signed releases and
rollback, secret rotation, OpenBao and Conjur in the stack, the admin area
and VM operations, browser SSH/RDP and consoles, the syslog collector,
remote and ARM stack hosts, monitoring with alerts, full-stack backups with
install from a backup, linked stacks with an overall dashboard and backup
copies, API tokens and Prometheus metrics.

### Added
- Prometheus metrics at `/api/metrics` (hosts, environments, jobs, alerts,
  linked stacks, backup ages), read with an auditor service token.
- API tokens: personal tokens (act as you, never with more than your current
  roles) and admins' service tokens, with an expiry of at most a year, for
  scripts and CI (`Authorization: Bearer`); writes made with a token are
  audited. User menu → API tokens.
- Full backups to linked stacks: each stack sends its newest checked
  archive to every linked stack, which keeps it (still sealed) on its
  primary file host with its own retention; Settings lists what was sent
  and the copies kept for others.
- All stacks on the dashboard: totals across every linked stack (a machine
  two stacks share counted once) and a card per stack that opens its own
  dashboard; stacks that stopped answering show their last data.
- Linked stacks: Admin → Linked stacks links two LABaPe stacks with a
  one-time code (the other stack's CA is checked against it); each reads
  the other's summary every minute (hosts, environments, jobs, alerts),
  keeps the last one when it stops answering, and raises an alert.
- Install from a backup: `install-labape.py --from-backup ARCHIVE
  --backup-key-file KEY` makes a new machine the stack that was backed up
  (same address, CA, secrets, OpenBao, Authentik users, data);
  `deploy/stack-restore.sh` puts a backup back onto an installed stack.
  `--restore-drill` keeps the copy in maintenance mode.
- Full-stack backups: the update agent writes the whole stack (every
  database, OpenBao, the platform CA, configuration and bootstrap secrets)
  into one archive encrypted with a backup key kept apart from it, checks
  it (hashes, test-loads the LABaPe and Authentik databases), daily and on
  demand, listed and downloadable in Settings, watched by monitoring.
- Alert handling: acknowledge an alert, silence its condition for a while
  (with a reason), no alert announcements during maintenance mode (they go
  out when it ends if still open), and an alert when alert emails can't be
  sent, cleared when the mail server works again.

### Changed
- Audit events go to syslog over one kept-open TCP/TLS connection instead
  of one connection per event.

### Removed
- The update agent's file-based rotation of the engine vault password and
  VM key (phase R2): the worker rotates them in the secret store. An install
  that still has them as files is told to run `stores.sh setup` first.
- `tools/secrets-test/` (throwaway OpenBao and Conjur used while building
  the store providers): the stack's own stores replaced it. The store
  live-test scripts now check the stack's stores.

### Changed
- New installs get the web interface's certificate from LABaPe's platform CA
  (installer answer `Tls=labape-ca`, the default), so browsers need to
  trust one root (Admin dashboard → download the root), the same one the
  VMs' certificates chain to. `internal` (Caddy's own CA) and ACME remain.
- Pages use the full width of the browser window (no 1,200 px cap), so
  wide tables and remote sessions get the room.
- The dashboard leaves out disabled KVM hosts.
- Preparing a KVM host copies only the installer and the few examples its
  steps read (jobs bring their own files), and a re-run removes what older,
  fuller copies left in `/opt/labape-kvm` (its state, logs and generated
  configuration stay).
- KVM hosts get `smartmontools` (disk health) from the installer and from
  Set up access.

### Fixed
- The stores' setup could fail on a fresh OpenBao ("cannot write to
  readonly storage"): it now waits for the node to become active after the
  unseal.
- A service the installer left out (e.g. Conjur) could still be started by
  its own compose profile: the override now replaces the profiles.
- Changing the web interface's host name (e.g. from an address to its DNS
  name) works by re-running the installer's steps: the platform CA adds the
  new name, the stack's Authentik updates its redirect URI, and Stack.Setup
  no longer copies engine secrets back onto disk once they're in the store.
- Destroying a VM on a KVM host LABaPe prepared no longer fails on the
  disk cleanup (it needed sudo the service user doesn't have).
- A new install always has a primary file host (the Files page could
  otherwise keep files on the stack host).
- Set up access joins the account to sshd's `AllowGroups` group when there
  is one (Raspberry Pi OS).
- A download from a URL served compressed no longer shows its progress
  against the compressed size.
- Redeploying an environment whose VMs were off (after a host reboot)
  starts them, domain controllers first, instead of waiting 45 minutes for
  DHCP addresses that couldn't come.
- VM guest details: an empty value from the guest agent (a Windows loopback's
  MAC address) no longer swallows the next line.
- The installer's `Storage.Dirs` gives the VM and template folders to the
  service user, so jobs on a freshly installed host can write there.
- Long jobs the worker runs itself (VM power, host install) keep their
  heartbeat, so they're no longer marked failed after two minutes.
- Audit syslog over TLS ends the connection with a TLS close_notify, and
  trusts the platform root as well as the system roots, so a collector with
  a platform certificate (the Syslog collector service) verifies without
  turning verification off. TLS 1.2 is the minimum.

### Added
- Monitoring for the hosts that run a LABaPe stack, and the stacks
  watching each other: a role per host (KVM, stack or both) with matching
  sections, containers, volumes, backups, certificates and database sizes,
  peer stacks probed every minute, and alerts that start and clear on the
  dashboard, to syslog and by email (Settings → Monitoring).
- The web stack runs on ARM (arm64, e.g. a Raspberry Pi 5): the image
  builds there, Conjur is left out (no arm64 image), Debian 12 and an
  existing Docker CE install are handled.
- The web stack can run on a machine that isn't the primary file host:
  the Files page then keeps every file on the primary over SSH (uploads
  and downloads stream there; nothing is staged on the stack host), and
  templates built on other hosts are copied to the primary.
- A primary file host: one KVM host holds every file (ISOs, installers,
  template images); a job on another host gets copies of the ones it needs
  (checked, kept for next time), waiting a configurable time when the
  primary is unreachable. Templates on the primary can be used on every
  host. A host can read the files from a share instead. Applications with
  local installers now work on remote KVM hosts.
- KVM hosts on Rocky and Alma Linux 9 or later (the installer's kvm
  profile, so Prepare works there too): dnf packages, libvirt's modular
  daemons, SELinux labels for the storage folders, Ansible on Python 3.12.
- The installer reuses its saved answers on every run (an update asks only
  new questions; `--fresh` asks again), shows every answer for a yes before
  it starts when anything was asked, and offers each optional service:
  Conjur (OpenBao is always installed), the syslog collector, remote access,
  the update agent and Authentik. Not chosen means parked in
  `container/compose.override.yaml`. With Authentik installed it's the
  sign-in provider; otherwise a throwaway Authentik for the first sign-in
  is the default.
- Preparing a KVM host from the web interface takes its networks from
  Admin → Networks (bridge, NIC and address per network) and attaches them
  to the host afterwards; the host gets no command-line setup unless asked
  (jobs bring their own files), and templates aren't asked.
- A syslog collector in the stack: rsyslog on UDP/TCP 514 and TLS 6514
  (client certificates from the platform CA required by default) with its
  own PostgreSQL, a **Syslog** tab for admins and auditors (filters, paging,
  live view), retention (30 days by default) and forwarding to SIEMs over
  UDP, TCP or TLS with facility and severity filters (Admin → Settings →
  Syslog collector). The audit trail goes there by default on new installs
  and can present LABaPe's client certificate. KVM hosts can send their own
  logs to it over TLS (Prepare does it by default; a switch on the host's
  SSH access section).
- A details page for each VM of an environment: Dashboard (state, size,
  24-hour load), Disks and volumes, NICs and OS from the QEMU guest agent
  (no login), Accounts and Groups read and changed live by the
  environment's owners and admins (SSH on Linux, WinRM on Windows;
  audited, LABaPe's own accounts protected), and a Console section: the
  VM's VNC console (Windows and Linux workstations) and serial console
  (Linux) in the browser through the KVM host's access account, plus a
  copyable virt-viewer command.
- SSH and RDP in the browser through guacd (in the stack, behind LABaPe's own
  tunnel; credentials never reach the browser): RDP to Windows VMs, SSH to
  Linux VMs (owners and admins) and to KVM hosts (admins), plus a .rdp file
  for native clients. Optional session recording, idle timeout, a session
  log, and a new **auditor** role that reads the audit log and the session
  log and plays recordings.
- Windows hosts get Remote Desktop on (Network Level Authentication
  required, firewall rules on) through the new `windows_rdp` role.
- KVM host administration over SSH: a dedicated access account (default
  `localadmin01`, key-only, passwordless sudo) set up once from an admin
  login typed in and never stored; a details page with Dashboard (warnings,
  24-hour trends), Drives and volumes (with SMART), NICs, OS, Accounts,
  Groups and VMs; metrics every 5 minutes kept 7 days; admins manage the
  host's accounts and groups; **Prepare this host** runs the installer's kvm
  profile on a new Debian host as a job and points LABaPe at it.
- VM power: start, shut down (clean, with an optional force off after a
  timeout), restart and force off, for one VM or a whole environment
  (owners and admins). A whole-environment start follows each host group's
  start order (domain controllers first by default), waiting for each
  group's guest agents and an optional delay; shutdowns go in reverse.
- Files: every tab says what its files are for and has filters (name, size,
  SHA-256, description, date added; Templates by name, OS, host). Installers
  list the OSes they target, generic (Any, Windows, Linux), by family or
  exact OS; the catalog and deploy forms refuse an installer on an OS it
  doesn't target. Existing installers get targets from their extension
  (migration 0007).
- The maintainer's release signing key (Ed25519) in `deploy/allowed_signers`,
  so the update agent can accept signed release tags. `install.sh` now also
  replaces a host's comments-only signer list (the placeholder) with the
  repository's.
- **Admin** area: KVM hosts, Networks, Settings, Secrets, Audit log and User
  errors in one place, listed on the left (admins only); old links redirect.
- Jobs page filters (environment, type, state, requested by, dates), kept in
  the URL; environments are shown by name. Users still see only their own
  jobs and those of environments they can access.
- User errors get the audit log's kind of filters (user, where, message,
  source, status, dates) and a JSON export; the audit log gets a date range.
- Catalog → Requests has a **New request** button.
- `container/stores/set-secret.sh NAME`: sets one of the stack's bootstrap
  secrets where the stack reads it (and in the platform store once they've
  moved there). The installer and the test Authentik use it for the OIDC
  client secret.
- **Syslog collector** catalog service (Linux only): rsyslog receiving UDP and
  TCP on 514, TLS on 6514 and mutual TLS on 6515 with the host's platform
  certificate (TLS 1.2/1.3). Remote messages go to `/var/log/remote/`. Existing
  catalogs get it through migration 0006. Reverse DNS lookup of senders is a
  role option, `syslog_collector_dns_lookup`, off by default.
- Catalog services can name a vetted Ansible role (`linux.role`) for
  configuration that packages and units can't express; it runs after the
  platform certificates.

### Changed
- Template refreshes install every available Windows update (all
  categories), not only security, critical and rollup updates.
- An environment's rotated Windows passwords are two secrets,
  `environment/<id>/windows_admin` and `.../domain_admin`, instead of one
  JSON value; existing ones are converted automatically.
- `tools/helpers/ship.sh` merges with `--delete-branch` and prunes.
- Rotating the engine's vault password also gives the command line's vault
  on the same host (`secrets.vault.yml` and `~/.labape-vault-pass`) the
  new password, so both keep working. `deploy/update-agent/install.sh
  --cli-vault FILE|none [--vault-pass-file FILE]` says which vault.
- The credentials sheet lists the domain Administrator in its own
  section, and a domain controller's inventory entry uses
  `domain_admin_password`.
- One job runs per environment at a time.
- A Linux VM's reinstall check uses the SSH key it was created with, so
  rotating the VM key doesn't reinstall running VMs.
- Every action shows a short tooltip, and every action that changes
  something asks first in a dialog with a fuller note (Continue or
  Cancel); the browser's confirm boxes are gone.
- Hosts, Networks, catalog items, files and templates follow the
  Environments pattern: a list with Add at the top right, and each name
  opening the entry's own page, where editing happens. Non-admins see
  Hosts and Networks read-only, limited to what they can use.

### Fixed
- The admin dashboard's Platform CA row showed the last secret store's
  check instead of the CA's.
- Linux VMs cloned from a template resolve their own name: the template's
  `127.0.1.1 template` line in `/etc/hosts` is rewritten to the VM's name at
  every deploy (no reinstall). `sudo` no longer warns "unable to resolve
  host" and `hostname -f` works.
- Firefox installs on Debian (as `firefox-esr`); the deploy of a Debian VM
  with the default software manifest no longer fails. Catalog entries can
  name a package per distribution (`apt: {package: ..., distributions:
  {Debian: ...}}`), and a migration updates the web catalog's seeded entry.
- Seven shell scripts (the update agent's installer and script, the
  rollback script, the platform CA scripts and two libvirt helpers) are
  executable in the repository again; `install.sh` and
  `labape-rollback.sh` failed with "Permission denied" when run directly.

### Added
- Which store holds LABaPe's own secrets is configurable: `LABAPE_PLATFORM_STORE`
  (`openbao`, the default, or `conjur`) in `container/.env`, installer
  answer `PlatformStore`. Re-running `container/stores/stores.sh setup`
  moves the engine and bootstrap secrets and the command line's login to
  the chosen store and deletes the old copies. Conjur gains hosts
  `labape/agent` (owns `labape/host`) and `labape/cli` (read permits).
- The master key, database password and connection strings, CA password
  and OIDC client secret move into OpenBao (`labape-host/bootstrap/`,
  only the update agent's login reads it). The stack reads a memory-only
  copy (`LABAPE_SECRETS_DIR`), refilled at boot by `labape-stores.service`
  before it starts the stack; the agent's rotations write OpenBao too
  (`container/stores/hostsecrets.py push|pull|same`).
- The engine's vault password, vault and VM SSH key (with its previous keys)
  live in OpenBao: `stores.sh` imports them (`labape engine-secrets
  import|status|check`), every job reads them from there, and the worker
  rotates them instead of the update agent. The command line reads them
  with its own read-only login (`~/.config/labape/store.env`,
  `scripts/lib/store_get.py`, `scripts/lib/cli-secrets.sh`) and re-encrypts
  its own vault when the password was rotated.
- OpenBao and Conjur Open Source run in the LABaPe stack (`stores` compose
  profile), set up by the installer's new `Stack.Stores` step
  (`container/stores/stores.sh`, also for existing installs): certificates
  from the platform CA, OpenBao unsealed at boot by a user unit, LABaPe's
  store settings and logins filled in. `labape stores-configure` sets them.
- Dated template versions (`<name>-YYYY.MM`): a monthly refresh of each
  template's newest version, scheduled by the worker, and retention that
  keeps the newest 3 versions by default and deletes older ones once no
  environment or VM uses them (Settings → Templates).
- Delete on a template's page: a `template.delete` job that refuses while
  an environment uses the template or any VM on the host is built on it.
- Windows Server Datacenter as an option (Standard stays the default): the
  Build a template form and `build-template.sh --datacenter`, and
  `windows_datacenter` on a direct-ISO host group. Templates record their
  `sku`; migration `0005` adds the column.
- `requires_gui: true` on a catalog application marks software that needs
  a desktop. Firefox has it, so Windows Server Core hosts skip it instead
  of failing the deploy.
- External secret stores, step 1 (design §25): Settings → Secret stores
  connects OpenBao or HashiCorp Vault (KV v2, AppRole) and CyberArk Conjur
  (host and API key), with Test connection and a check on the admin
  dashboard. Their logins are kept in the built-in store.
- External secret stores, step 2: every stored secret names the store it
  lives in and links the rotation policy that changes it. Secrets → Stored
  secrets lists them and moves them between stores (copied, read back,
  switched, then the old copy deleted); changing a secret's store on its
  page moves it too. Settings → Secret stores sets the default store for
  new environment secrets. Store logins, elevated accounts and job tokens
  stay built-in.
- External secret stores, step 3: LABaPe rotates its own logins to the
  stores every 24 hours (OpenBao/Vault AppRole secret ID, Conjur host API
  key), once the store is set up. A secret can be marked as rotated by its
  external store only when it lives in one.
- The admin guide has the steps for cleaning up outside copies of LABaPe's
  store logins once LABaPe rotates them.
- `labape ca-sign`: signs a certificate request with the platform CA from the
  host's command line, for services on the LABaPe host itself.
- `Claude_Docs/Reference_Secrets-Backend-Requirements.md`: what a new secrets
  backend and its provider must offer.
- User guides: `User_Docs/Web-Interface-Guide.md` (everyone who signs
  in), `User_Docs/Web-Interface-Admin.md` (admins) and
  `User_Docs/Command-Line.md`, linked from the README.
- Secret rotation: the OIDC client secret rotates daily through the Authentik
  API with an elevated Authentik token, is checked at the token endpoint
  (put back if it fails), and takes effect at the next sign-in with no
  restart. Secrets that need an elevated account (this one and the
  directory token) are reminders until one is chosen, instead of failing
  every hour.
- Secret rotation (phase R2): the engine's Ansible vault password and the
  VM bootstrap SSH key rotate daily through the host agent, with no
  restart and no maintenance mode. The new key is added and checked on
  the deployed Linux VMs first, then swapped in for the web stack and the
  command line (`~/.ssh`); the 7 previous keys are kept and still tried.
  New CLI command: `labape push-vm-key`.
- Lab password rotation (phase R4): each deployed environment's Windows
  Administrator and domain Administrator passwords rotate weekly by
  default, changeable or off on the environment's page (DSRM never). The
  change is checked by signing in, all hosts or none; the inventory,
  credentials sheet and later deploys follow.
- Settings → VM accounts: the admin account made on new Linux VMs
  (default `labape`), recorded per environment.
- Secret rotation (phase R3): the master key, the PostgreSQL password and
  the CA password rotate through the host's update agent (weekly, in the
  maintenance window, or with Rotate now), with health checks that
  include a CA signing probe and full rollback on failure.
- Updates from the web interface: Check now offers the newest release,
  a newer pre-release, or a rollback to the previous version, each with
  its notes. A host-side agent (user systemd unit) applies only signed
  tags, with maintenance mode, a database backup, the previous image on
  standby, health checks and automatic rollback.
  `deploy/labape-rollback.sh` goes back further by hand. New CLI commands:
  `labape maintenance`, `backup`, `restore`, `health`.
- Secret rotation (phase R1): a Secrets page listing every internal
  secret with its store, class, interval and status; scheduled rotation
  as jobs with retries and audit; elevated accounts for secrets that
  can't rotate themselves; the built-in store now uses data keys
  (envelope encryption) that rotate daily without a restart; the worker's
  SSH key for remote hosts and the Authentik directory token rotate too.
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
