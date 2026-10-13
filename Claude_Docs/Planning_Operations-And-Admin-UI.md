# Plan: VM operations, admin area, filters, host details and remote access

> **Stage: Planning.** The user's feature list of 2026-10-12, turned into batches with options and
> recommendations. Questions 107-120 in [Planning_Questions.md](Planning_Questions.md) are answered
> (§0); building goes batch by batch, O1 first. Once a batch ships, record what was built in
> [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) (a new section per batch),
> as for earlier rounds.

## 0. Decisions (questions 107-120, answered 2026-10-12)

| Q | Decision | Changes the plan |
|---|---|---|
| 107 | Audit, Errors and Secrets move under Admin too; Hosts and Networks are admin pages | O1 |
| 108 | Clean shutdown by default; Force off offered as an option | O3 |
| 109 | Start order: anything that provides a service to others (a DC) starts before its clients | O3 |
| 110 | Wait for the guest agent plus an optional delay; staggering also spares the KVM host | O3 |
| 111 | Owners and admins may start, stop and restart | O3 |
| 112 | Installer targets both generic (Windows, Linux, Any) and fine-grained (OS family, distribution, specific OS keys) | O2: `targets` is a list, not one value |
| 113 | The KVM host account LABaPe uses is separate from the tool's own service account, e.g. `localadmin01` | O4 |
| 114 | An admin types credentials in once; they're used only to create that access account (key-based), which LABaPe uses from then on | O4 |
| 115 | Metrics every 5 minutes, kept 7 days; SMART daily | O4 |
| 116 | guacd with LABaPe in front: RDP/VNC for hosts with a GUI, SSH for everything that supports it, plus Download .rdp | O5 |
| 117 | Recording optional (admin setting, off by default) | O5 |
| 118 | Owners and admins open sessions; a new **auditor** role sees session logs and recordings | O5: new role |
| 119 | Guacamole VNC console for GUI, a serial console for everything that supports it, virt-viewer file optional | O6 |
| 120 | Environment owners and admins manage accounts and groups on VMs; admins on KVM hosts | O4, O6: not read-only |

Future item from 113: adding more local and domain accounts to an environment (Planning_Backlog.md).

## 1. The request, item by item

| # | Request | Batch |
|---|---|---|
| R1 | Start, stop and restart VMs, one VM or the whole environment | O3 |
| R2 | Set a boot order; a whole-environment restart follows it | O3 |
| R3 | An **Admin** tab with the admin pages nested in it, listed vertically on the left: Networks, Settings, Hosts | O1 |
| R4 | Jobs page: filters; users see only jobs of environments they can access; environments shown by name, not ID | O1 |
| R5 | Files: filters on every sub-tab (name, size, SHA, description, date added) | O2 |
| R6 | Installers target OS types (Windows, Linux, …) | O2 |
| R7 | Each Files sub-tab starts with a description of what its files are for | O2 |
| R8 | Catalog → Requests is missing a **New** button | O1 |
| R9 | Registering a KVM host takes SSH details, used to gather information and show status and metrics; an option to start the install | O4 |
| R10 | Errors gets filters like Audit's | O1 |
| R11 | KVM host details: disks (`lsblk`, SMART), volumes, NICs, temperatures, and whatever else an administrator wants; sections listed on the left: Dashboard, Drives and Volumes, NICs, OS, Accounts, Groups | O4 |
| R12 | Open an SSH or RDP session from the browser (suggest options; Guacamole for RDP) | O5 |
| R13 | VMs in an environment get a details page like the KVM host's, with a link to a console (suggest options) | O6 |

## 2. What exists today

- **Navigation** (`web/src/App.tsx`): one top bar. Everyone sees Environments, Jobs, Files,
  Catalog, Hosts and Networks; admins also see Audit, Errors, Settings and Secrets.
- **Jobs** (`app/labape/api/jobs.py`): the list is already limited to jobs of environments the
  user can see (`visible_ids`), plus their own non-environment jobs. The page shows `#<id>` for the
  environment, and there are no filters.
- **Audit** has actor and action filters and date limits in the API (`since`, `until`) and an
  export. **Errors** filters only by user name in the API, and the page has no filter fields.
- **Files**: tabs per category (ISOs, installers, other) plus Templates. Installers have no OS
  target. There are no filters or tab descriptions.
- **KVM hosts**: name, libvirt URI (`qemu:///system` or `qemu+ssh://user@host/system`), storage
  paths, bridge, concurrency and enabled. Remote hosts are reached with the worker's SSH key
  (design §22.4), and **Check** runs `virsh version`. Host load (CPU, memory, VMs) comes from
  `virsh` (`engine/hoststats.py`). There's nothing about disks, NICs, sensors or accounts.
- **VMs**: deploy and destroy only; no power operations. `virsh domstats` and `guestinfo`
  (filesystems) are already read for host load. Linux VMs are reachable over SSH with the VM key,
  and Windows VMs over WinRM with the environment's passwords (`engine/vmaccess.py`).
- **Credentials** are visible to environment owners and admins only (decision 9).

## 3. Batches (recommended order)

### O1. Admin area, Jobs, Errors, Catalog requests (built 2026-10-12: Planning_Web-Interface-Design.md §28.1)

- **Admin tab** (R3). One **Admin** button in the top bar opens `/admin/*`, with a vertical list on
  the left (MUI `List` in a side panel) and the page on the right: Hosts, Networks, Settings,
  plus Audit, Errors and Secrets (question 107). The old URLs redirect to the new ones, so bookmarks
  and links in emails keep working.
- **Jobs** (R4). Filters: environment (by name), kind, status, started by, and a date range.
  They're held in the URL query, so a filtered view can be bookmarked. Environments and users are
  shown by name; a destroyed environment shows its name with "(destroyed)". The API gains the
  same filters and returns `environment_name`. Visibility stays as built, with a test proving a
  user can't see another user's environment jobs by filtering or by ID.
- **Errors** (R10). The same filter bar as Audit: user, kind/path text, message text, date range,
  plus the export.
- **Catalog → Requests** (R8). A **New request** button on the Requests tab opens the existing
  request form, with a kind selector (service, application or package).

### O2. Files: filters, descriptions, installer OS targets (built 2026-10-13: Planning_Web-Interface-Design.md §28.2)

- **Filters** (R5) on every Files tab: name (contains), size range, SHA-256 (prefix match),
  description (contains), and date added (range). Filtering runs in the browser for these list
  sizes, and the values stay in the URL.
- **Descriptions** (R7): a short paragraph under each tab title. For example, Installers: "MSI,
  EXE, DEB and RPM files that catalog applications install from (source: local). Each is used
  only on the OS it targets."
- **OS targets** (R6): a new `target_os` on files, `windows`, `linux` or `any`, set on upload or
  edit and shown as a chip (question 112). A catalog application's local installer must target
  that platform, and the application form lists only matching installers. Migration `0007` sets
  existing installers from their extension (`.msi`/`.exe` → windows, `.deb`/`.rpm` → linux,
  otherwise `any`).

### O3. VM power and boot order (built 2026-10-13: Planning_Web-Interface-Design.md §28.3)

- **Operations** (R1): start, shut down, restart and force off, for one VM (environment page, per
  VM) or the whole environment. Each is a job (`vm.power`), so it's audited, shows a log, and
  works for remote KVM hosts the same way deploys do (the job runs `virsh` on the host).
  - **Shut down** is a guest shutdown (ACPI, or the guest agent when present), with a timeout
  (default 5 minutes), then the job fails and offers **Force off** (question 108).
  - **Restart** is shut down, then start.
- **Boot order** (R2): each host group gets a **Start order** number (default: domain controllers
  1, everything else 2) and a **Wait** rule for when the next group may start (question 110). A
  whole-environment start or restart goes group by group in that order; a whole-environment shut
  down goes in reverse (members first, domain controllers last). VMs in the same group start
  together.
  - "Boot order" could also mean the firmware boot device order (disk, CD-ROM, network); see
  question 109.
- **Who** (question 111): environment owners, deployers on their own environments, and admins.
- **State**: the environment page shows each VM's power state from `virsh domstate` (cached ~15 s,
  like host load).

### O4. KVM host registration over SSH, host details and metrics

- **Register** (R9): the host form gets an **SSH** section: host name or address, port, user, and
  how to authenticate (question 113). **Test** checks SSH, `sudo -n` for the allowed commands, and
  libvirt. The libvirt URI is filled in from it (`qemu+ssh://user@host/system`) for remote hosts.
- **Install** (R9): **Prepare this host** runs `install-labape.py --profile kvm` on the new host as
  a job, over SSH, and streams its log (question 114).
- **The remote host's command line** (handoff follow-up, folded in here on 2026-10-12): a remote
  KVM host's own command line has no login to the platform store, because `store_get.py` reads
  `~/.config/labape/store.env` and the stores listen only on the stack host's loopback unless
  `LABAPE_STORES_BIND` is set. Preparing a host would also:
  - publish the stores on an address the host can reach, with TLS from the platform CA;
  - issue that host its own read-only store login (one AppRole or Conjur host per KVM host, so
  one can be revoked alone);
  - write its `store.env` over SSH.

  The template delete's "is this disk in use" check also needs the SSH account to read VM disks
  on that host (the libvirt group, or the sudoers whitelist).
- **Collector**: one read-only script (`engine/hostfacts.sh`, standard tools only) run over SSH. It
  returns JSON from:
  - `lsblk -J -O`, `smartctl -j -a` per disk, `df`, LVM (`pvs`/`vgs`/`lvs --reportformat json`), ZFS or mdraid when present;
  - `ip -j addr`/`ip -j link`, `ethtool` speed/duplex, bond and bridge members;
  - `sensors -j` (lm-sensors), and `/sys/class/thermal` otherwise;
  - `/etc/os-release`, kernel, uptime, load, pending updates count, reboot-required, time sync (`timedatectl`), failed systemd units;
  - accounts (`getent passwd`, UID ≥ 1000 plus root, shell, last login, sudo rights) and groups (`getent group` with members).

  `smartctl` and `sensors` need root on most systems. A sudoers drop-in lets the LABaPe account
  run only those exact commands, without a password.
- **Metrics** (question 115): CPU, memory, disk use, temperatures and NIC counters every 5
  minutes, kept 7 days in PostgreSQL. SMART is read daily, and on demand. The host's Dashboard
  section shows sparklines (the existing `Sparkline` component) and warnings: a SMART failure or
  pre-fail attribute, high temperature, a filesystem over 90 % full, failed units, reboot
  required.
- **Details page** (R11): sections on the left, each a sub-route (`/admin/hosts/<id>/<section>`):
  - **Dashboard**: status, load, temperatures, warnings;
  - **Drives and volumes**: disks, SMART, partitions, LVM, filesystems;
  - **NICs**: links, speed, addresses, bridges and bonds, counters;
  - **OS**: release, kernel, uptime, updates, time sync, services;
  - **Accounts**; **Groups**;
  - **VMs**: the host's domains and their environments;
  - **Storage pools**: libvirt pools and free space;
  - **Settings**: the current form.

  Accounts and Groups are read-only (question 120).

### O5. SSH and RDP from the browser

Options (question 116):

| Option | How | For | Against |
|---|---|---|---|
| **a. Apache Guacamole, full web app** | `guacamole/guacd` + `guacamole/guacamole` (Tomcat) containers in the stack; sign-in through Authentik (OIDC); LABaPe creates connections through Guacamole's REST API and links to them | RDP, SSH, VNC, telnet, Kubernetes; session recording; mature | A second web app with its own user and permission model to keep in sync; the user sees a different UI |
| **b. guacd + LABaPe as the tunnel** *(recommended)* | Only `guacd` in the stack. The browser runs `guacamole-common-js` inside LABaPe's page; a WebSocket endpoint in the API checks the user's rights, then relays the Guacamole protocol to guacd with the connection's parameters (host, port, user, password or key) filled in server-side | One UI and one permission model (LABaPe's); credentials never reach the browser; every session audited; RDP, SSH and VNC (for consoles, O6) through one gateway | LABaPe implements the tunnel (a small, well-documented protocol; `guacamole-lite` is a Node reference) |
| c. Native clients | Download a `.rdp` file or open an `ssh://` link | No infrastructure | The browser's machine must reach the VM network, and the user types passwords themselves; no audit |
| d. SSH only, in-house | `xterm.js` in the page; the API relays to SSH (`asyncssh`) | Light, no extra container | SSH only; RDP still needs one of the others |

Recommendation: **b**, with **c** as an extra "Download .rdp" link for people on the lab network.

- Sessions run as the environment's accounts (Windows Administrator or the domain Administrator
  over RDP; the Linux admin user with the VM key over SSH) or, for KVM hosts, the registered
  SSH account.
- Who may open one (question 118): the people who may see the credentials, that is owners and
  admins. Each session start and end is audited.
- Recording (question 117) is a Guacamole feature (`recording-path`): off by default, an admin
  setting.

### O6. VM details page and console

- **Details** (R13): like O4's page, with sections Dashboard, Disks and volumes, NICs, OS, Accounts,
  Groups and Console. Most of it comes from the QEMU guest agent through libvirt, with no login
  to the VM: `virsh guestinfo` (OS, hostname, users logged in, filesystems, disks, interfaces) and
  `domifaddr --source agent`. Accounts and groups need a login: SSH on Linux, WinRM on Windows,
  read-only. Metrics come from `virsh domstats`, which the worker already reads.
- **Console** options (question 119):

| Option | How | Notes |
|---|---|---|
| **a. Guacamole VNC to the VM's libvirt display** *(recommended with O5 b)* | The VM's graphics are VNC on the KVM host's loopback; guacd reaches it through an SSH tunnel to the host (remote hosts) or directly (stack on the host) | Works before the OS has networking (boot, BIOS, installer, blue screens); same gateway and audit as O5 |
| b. noVNC | `websockify` relays the VNC port; noVNC in the page | Simple and well known; a second relay to secure and maintain |
| c. Serial console | `virsh console` through a WebSocket to `xterm.js` | Text only; good for Linux (needs `console=ttyS0`), not Windows |
| d. `virt-viewer` file | Download a `.vv` file with the SPICE/VNC address | Needs `virt-viewer` and network reach on the user's machine |

- The VM's VNC display gets a password per VM, kept in the store, so a reached port alone isn't
  enough.

## 4. Security notes

- Remote access and consoles put the lab VMs and KVM hosts one click away from the browser. The
  gateway listens only inside the stack network, the API checks rights on every session, and
  sessions time out when idle (default 30 minutes).
- The KVM host account for O4 is read-only apart from the sudoers whitelist. Installing (O4) is a
  separate, explicit, audited job.
- New secrets (VNC passwords, host SSH credentials) go into the platform store and get rotators
  where it makes sense, as for the existing ones (§24).

## 5. Tests

- pytest: API filters and visibility for jobs, errors and files; power-job ordering (start order,
  reverse shutdown, wait rules) with a fake `virsh`; host-facts parsing from captured command
  output; tunnel authorization (no session without rights, credentials never in responses).
- Live, on the lab: start, stop and restart `syslog1`, then `lab2` in order; register a second
  KVM host over SSH and read its facts; RDP to `l2win1`, SSH to `l2lin1`, a console on both.
