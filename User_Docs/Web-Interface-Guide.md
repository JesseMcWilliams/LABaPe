# Using the LABaPe web interface

This guide is for everyone who signs in to LABaPe to build lab environments: deployers, template editors, file managers and viewers. Admins should also read [Web-Interface-Admin.md](Web-Interface-Admin.md), which covers installing, settings, secrets and updates.

Your organization may have renamed the app (Settings → Branding); the top bar shows its name. This guide calls it LABaPe.

## Contents

1. [Signing in and your roles](#1-signing-in-and-your-roles)
2. [The dashboard](#2-the-dashboard)
3. [Creating an environment](#3-creating-an-environment)
4. [Working with an environment](#4-working-with-an-environment)
5. [Jobs](#5-jobs)
6. [Operating system notes](#6-operating-system-notes)
7. [Files and templates](#7-files-and-templates)
8. [The catalog: services, applications and packages](#8-the-catalog-services-applications-and-packages)
9. [Hosts and networks](#9-hosts-and-networks)
10. [Confirmations, tooltips and maintenance](#10-confirmations-tooltips-and-maintenance)
11. [API tokens](#11-api-tokens)

## 1. Signing in and your roles

Open the address your admin gave you and choose the sign-in button (usually **Authentik**). LABaPe gives you roles from your Authentik groups:

| Role | Default Authentik group | What it adds |
|---|---|---|
| viewer | `labape-viewers` | Sign in and browse; environments can't be shared with other users yet, so a viewer sees no environments |
| deployer | `labape-deployers` | Create, deploy, redeploy and destroy your own environments; see their credentials |
| template_editor | `labape-template-editors` | Everything a deployer can do, plus build, refresh, promote and edit VM templates |
| file_manager | `labape-file-managers` | Upload, download from a URL, edit and delete files |
| auditor | `labape-auditors` | Read the audit log, the syslog messages and the remote-session log, and play session recordings; change nothing |
| admin | `labape-admins` | Everything, including hosts, networks, the catalog, settings and secrets |

Everyone signed in can browse files, the catalog, and the hosts and networks they may use.

**Your account menu.** Click your name at the top right to see your roles, groups, sign-in provider, this and your previous sign-in, and when your session ends.

**Reload rights.** If someone adds you to a group, choose **Reload rights** in the account menu. LABaPe asks Authentik for your current groups without signing you out, then recalculates your roles. If Authentik needs you to sign in again first, LABaPe says so: sign out and back in.

**Lists: pages and exports.** Environments, Jobs, Files, the Catalog and Syslog show 50 rows per page. **Rows per page** above a list changes it for every list (25, 50, 100, 250, or **Other…** for your own number, 5-1000); LABaPe keeps your choice with your profile. **Export** (Environments and Files as CSV; Jobs as JSON or CSV, each job with its parameters and result, not its log) asks which rows: **this page**, **rows from - to** of the filtered list, or **all**; it uses the filters shown.

## 2. The dashboard

The dashboard is the landing page; clicking the app name at the top left always returns to it. It refreshes every minute.

- **KVM hosts** you can deploy to: CPU, RAM, free space and disk activity, each with a 24-hour trend.
- **Your environments**: how many VMs are online, and for each VM its CPU, RAM, free disk space and disk activity.
- **Download the root certificate**: the platform CA's root, for your browser (see [§4](#vm-certificates)).

Admins and auditors also see:
- **All stacks**, when this LABaPe is linked with others: totals across every linked stack (a machine two stacks both use is counted once) and a card per stack with its version, hosts, VMs, environments, jobs and open alerts. A stack that stopped answering shows its last data, marked "as of" when it last answered. A card opens that stack's own dashboard in a new tab (with its own sign-in).
- **Alerts**: problems the monitoring found (a host running hot or nearly full, a stopped container, a missing backup, a linked stack not answering), with when each started, and the ones recently cleared. Admins can **Ack** an alert (someone is on it) or **Silence** it for a while with a reason.

A VM's free space and "responding" state come from the QEMU guest agent, which LABaPe installs on every new VM. VMs built before the agent was added show "unknown" and the size of their disk file instead.

## 3. Creating an environment

**Environments** lists the environments you can see, filtered by name, status, KVM host, OS, who created it and when, with **Export** as CSV.

Choose **Environments → New environment** (deployers and template editors). The form:

| Field | Notes |
|---|---|
| Name | 3-40 lower-case letters, digits and hyphens. It's also the environment's OpenTofu workspace. |
| KVM host | Only enabled hosts are offered. The bars below show the host's load **with this environment added**. With more than one host, **Suggest best host** ranks them for your host groups (memory and CPU in use, running jobs, whether the templates are already there), picks the best and shows why. |
| Host groups | One or more groups of identical VMs. **Add host group** adds another. |

Each host group sets:

| Field | Notes |
|---|---|
| Group name | 1-12 lower-case letters and digits |
| KVM host | Shown when there's more than one host. **Same as the environment** keeps the group with the rest; another host puts this group's VMs there (one environment spread over hosts). The group's networks and templates then come from that host. The hosts must carry the same lab network, so the VMs reach each other and the domain controller. Each VM keeps its host: power, snapshots, details and consoles go to it. |
| Count | 1-20 VMs |
| OS and Roles | Roles: `domain_controller`, `windows_server`, `windows_workstation`, `linux_server`, `linux_workstation` |
| Image source | **Build from template** clones a template (minutes). **Build from ISO** installs from the OS's ISO (up to an hour or more for Windows). |
| Template | The templates on that host for that OS, with their description. An info icon shows the template's notes (quirks, after-tasks). |
| vCPUs, Memory, Disk | An amber warning appears when you go below the template's minimums. A disk smaller than the template's is raised to the template's size. |
| Network, Addressing | Only the networks attached to the host that you may use, and only the modes that network allows. **Static** addresses come from the network's pool automatically; **DHCP** addresses are found after the VM boots. Domain controllers are always static. |
| Services, Applications, Packages | Catalog items added on top of the template (see [§8](#8-the-catalog-services-applications-and-packages)). Only published items with something for this OS are offered. |
| Host names | One box per VM. Leave a box blank for `<group><n>` (for example `web1`). Names must be unique across every environment's VMs on the same KVM host, and Windows allows at most 15 characters. |

**Load limits.** The form warns above 4 vCPUs per CPU, above 90% of the host's RAM committed, or below 15% free VM storage. It refuses an environment that would commit more than 100% of the host's RAM, and **Create and deploy** stays disabled.

**Quotas.** Your admin may limit how many environments you have and how many vCPUs, how much memory and VM disk they use together. The Environments page shows "Your quota: N of M …". An environment that would go over it is refused, with the numbers; destroy an environment, make this one smaller, or ask an admin. Destroyed environments don't count.

**Create and deploy** asks you to confirm, saves the environment and starts a deploy job at once. You land on the environment's page.

## 4. Working with an environment

The environment's page shows its state, its host groups with each VM's address, the hosts-file entries, and its jobs. Owners see these actions:

| Action | What it does |
|---|---|
| **Deploy** / **Redeploy** | Runs OpenTofu and Ansible. A redeploy creates missing VMs and reconfigures existing ones; it doesn't reinstall them. |
| **Destroy** | Deletes every VM, its disks and the OpenTofu workspace, and releases its addresses. It can't be undone. |
| **Show credentials** | The tester accounts and passwords (see below). |
| **Refresh addresses** | Only for environments with DHCP VMs: looks up each DHCP VM's current address and rewrites the inventory and hosts entries. No VM is changed. |
| **Promote to template** | Template editors: turns a VM (not a domain controller) into a template. The VM is generalized (sysprep on Windows, cloud-init clean on Linux), shut down and copied into the template library. Destroy the environment when you're done with it. |

**Credentials.** Each password is masked. **Show** reveals it in place, **Show large** displays it in large type (for reading across a room or typing by hand) and **Copy** puts it on the clipboard. Every view and copy is recorded in the audit log.

**Password rotation.** A deployed environment with Windows hosts has a **Password rotation** panel:
- **Windows Administrator** (the local account on Windows hosts other than domain controllers) and **Domain Administrator** change every 7 days by default.
- Change the interval in days and choose **Save**, or clear **On** to stop scheduled changes for this environment.
- **Change now** changes the password at once. LABaPe sets the new password on the VMs, checks it signs in, and updates the credentials. A session already signed in with the old password keeps working until it ends.
- The **DSRM** password is never changed.
- If a change fails, every VM keeps (or gets back) its old password, the status shows the error, and LABaPe tries again an hour later. A VM that was off when its password changed is brought up to date at its next deploy or rotation.

Linux VMs have no admin password: Ansible signs in with an SSH key (the admin user name is an admin setting, Settings → VM accounts).

**Lease.** If your admin turned leases on, a new environment expires after a set time (30 days by default), and the environment's page says when. Before the end you get reminder emails. At the end its VMs are shut down; after a grace period (7 days by default) it's destroyed, unless the admin set leases to keep it shut down. Choose **+7 days** or **+30 days** to extend it, up to the longest lease allowed from today. Extending a lease that already ended starts it again; start the VMs yourself afterwards.

**VMs.** The **VMs** panel lists each VM's state and whether its guest agent answers. Owners can **Start**, **Shut down**, **Restart** and **Force off** one VM or all of them, as a job:
- A shutdown asks the guest to shut down cleanly and waits (5 minutes by default). Tick **Force off after the timeout** to power off a VM that doesn't. **Force off** is like pulling the plug.
- **Start order**: VMs start group by group, lowest number first (domain controllers are 1, everything else 2, unless you change it). A group can wait until each of its VMs' guest agents answers, and then a delay, before the next group starts. Shutdowns go in reverse order.
- **RDP** opens a Remote Desktop session to a Windows VM in the browser, **.rdp** downloads a file for your own client (it asks for the password), and **SSH** opens a terminal to a Linux VM in the browser. Your admin may record these sessions (the session page says so) and closes idle ones after a while. Windows VMs deployed before Remote Desktop was turned on need a **Redeploy** first.
- **Move host group** (with more than one KVM host): moves a group's VMs to another host while they run; memory and disks are copied, so a big disk takes a while. The VMs must be running and have no snapshots, and the other host must carry the group's network. A deploy follows on its own to record the new host. The VMs list shows each VM's KVM host when they're on more than one.

**A VM's page.** Click a VM's name for its own page, with sections on the left:
- **Dashboard**, **Disks and volumes**, **NICs**, **OS**: what the guest agent reports, and 24 hours of load.
- **Accounts** and **Groups** (owners): add, change, lock and delete local accounts and groups on the VM. Built-in accounts (Administrator, root, the admin user) are protected; a domain controller has no local accounts.
- **Console** (owners; needs the KVM host's SSH access set up by an admin): **VNC** for Windows and Linux desktops, **Serial** for Linux (press Ctrl+] to leave), and a `virt-viewer` command for your own client.
- **Snapshots**: see below.

**Snapshots.** On a VM's page → **Snapshots**, owners can **Take snapshot** (a name and a description), **Revert** to one and **Delete** one; each runs as a short job.
- A snapshot keeps the VM's disks and, if it's running, its memory: reverting brings it back running exactly where it was. Everything since the snapshot is lost.
- Snapshots live inside the VM's disk file, which grows by what changes afterwards. Delete the ones you no longer need.
- VMs that boot with UEFI can't have snapshots (the page says so).
- Your admin may have every VM snapshotted automatically before a redeploy (`auto-before-job-<n>`, the newest few kept).

**Secrets.** Owners (and admins) see the environment's **Secrets** panel: LABaPe's own (the admin and account passwords, the credentials sheet, the inventory) and any the owners stored. Values stay masked until you choose **Show** or **Copy**, and each time is in the audit log. Search and the Group, VM and Store filters narrow the list; it pages like the other lists. **Store a secret** saves one of your own, for the whole environment or for one VM; **Replace** and **Delete** change your own. LABaPe's passwords change through rotation, not here. In OpenBao the environment's secrets are in the folder the panel shows, `env/<environment>/`. Where your admin has turned it on, the panel also links to **OpenBao's web UI**: sign in with **OIDC** (your LABaPe account), open the secrets engine it names and go to the folder. From a shell, set `BAO_ADDR` as shown and run `bao login -method=oidc`, then for example `bao kv get -mount=labape env/<environment>/accounts/windows_admin`. You read everything in your environment's folder and can store, change and delete under `user/` and `vm/<vm>/`; what you store there appears on the page within a few minutes. Access follows ownership: lose ownership and the next sign-in can't read the folder.

**Accounts.** The environment's **Accounts** panel lists accounts beyond the built-in admins: **domain** users (on the environment's domain controller) and **local** users on the hosts of chosen roles. Owners choose **Edit** to add or remove them; **Redeploy** then creates new ones on the VMs. Each gets a generated password, shown on the credentials sheet under "Environment accounts" (a local account has a different one on each VM), and changed weekly. Removing an account from the list stops LABaPe managing it and forgets its password; it stays on the VMs (remove it from the VM's Accounts section).

<a id="vm-certificates"></a>**VM certificates.** When the platform CA is set up, every VM deployed from the web interface gets a certificate for its short name, its full name and its address, renewed on the VM before it expires. Each VM also trusts the LABaPe root. To trust services on those VMs from your own browser, download the root certificate from the dashboard and import it.

## 5. Jobs

Deploys, destroys, template builds, downloads and backups run as jobs. **Jobs** lists them, newest first, a page at a time (filter by environment, type, state, who and when; **Export** downloads them as JSON or CSV); each job's page shows its live log (**Follow** keeps the newest lines in view).
- **Cancel** stops the job's tools. Work already done stays: VMs created so far are kept, and you can retry the deploy or destroy the environment.
- **Retry** starts a new job with the same settings. OpenTofu picks up from its saved state.
- One job runs per environment at a time; a second one waits.
- If your admin turned on job emails, you get an email when your job finishes.

## 6. Operating system notes

| OS | Notes |
|---|---|
| Windows Server 2019, 2022, 2025 | Installs with the Desktop Experience by default. For Server Core, tick **Server Core** (ISO installs) or use a template built as Core. Older .NET 4.x is upgraded to 4.8 automatically (Server 2019). Firefox can't be installed on Server 2019 Core. |
| Windows 10, Windows 11 (`windows_10`, `windows_11`) | Install unattended and join the domain. Windows 11 needs a disk of at least 64 GB, or the deploy fails. |
| Windows on KVM | VMs use legacy BIOS, so Secure Boot and TPM scenarios aren't supported. |
| Linux workstations (`ubuntu_lts`, `ubuntu_26`, `debian_latest`) | Install unattended and join the domain as Linux workstations. |
| Rocky Linux 9 (`rocky9`) | The usual Linux server OS. |

## 7. Files and templates

**Files** has a tab for each location: **ISOs**, **Installers**, **Other files**, and the **Templates** library. Everyone can browse them. Admins and file managers can change them. Each tab says what its files are for and can be filtered (name, **OS**, size, hash, description, date added; templates by name, OS, host and description). The OS filter offers the OSes that tab's files are for: an ISO's OS, an installer's or other file's OS targets. Filters stay in the address, so a filtered list can be bookmarked.

**Installer targets.** An installer says which systems it's for: any, Windows or Linux, a family (Windows Server, Windows client, RHEL family, Debian family) or exact OS versions. LABaPe sets a first guess from the file type (MSI and EXE: Windows; DEB: Debian family; RPM: RHEL family). An application whose installer doesn't target a host group's OS is refused for that group, naming the installer.

**Uploading.** Say first which OS the file is for, then choose the upload button and pick a file; LABaPe asks you to confirm.
- **The file's OS**: an ISO, the OS it installs (or leave it to the guess from the file name); an installer, the OSes it targets (required: the upload button waits for it); another file, its OS targets if it has any. **Download from URL** asks the same.
- Uploads resume after a dropped connection.
- A file with the same content as one already stored is refused as a duplicate, and a name already in use is refused before the upload starts.
- **Verify against a hash**: paste the vendor's value (SHA-256, SHA-1, SHA-512 or MD5) or a checksum-file line such as a `SHA256SUMS` entry. If the file doesn't match, it's deleted.

**Download from URL.** The worker fetches the file as a job, with progress in the job log; a retried job resumes where it stopped.
- HTTPS only, unless an admin allows plain HTTP (Settings → Downloads).
- Leave the name empty to use the last part of the URL.
- The optional expected hash works as for uploads.

**ISOs.** Open an ISO to set which OS it installs (LABaPe guesses from the name) and whether it's the **default** for that OS. Template builds and ISO installs use the default ISO, or else the newest one.

**Templates** (template editors):
- **Build a template** installs an OS from its ISO with Packer and saves it under a new name. Linux takes about 15 minutes, Windows up to an hour or more. For Windows Server, tick **Server Core (no desktop)** for a Core template, and **Datacenter** for the Datacenter edition (Standard otherwise). A template's page shows its edition.
- **Refresh** clones a template, applies every available OS update (on Windows, every update, not only security fixes) and saves the result under a new name. The original is left as it is. Your admin may run a refresh of every template monthly.
- **Versions.** A dated name (`<name>-YYYY.MM`, such as `rocky9-base-2026.10`) makes each refresh a new version of the same template; LABaPe keeps the number of versions your admin chose.
- **Delete** on a template's page removes it, unless an environment or a VM still uses it.
- **Promote to template** turns a VM from an environment into a template (see [§4](#4-working-with-an-environment)).
- Open a template to edit its description, notes, edition and minimum vCPUs and memory. The deploy form uses them for its warnings.
- Templates are never overwritten: give each new one a new, dated name, such as `rocky9-base-2026.10`.

## 8. The catalog: services, applications and packages

**Catalog** lists what host groups can add on top of their template (each tab filters by name, platform, description and, for admins, status):
- **Services**: Windows roles and features, or Linux packages and systemd units (for example IIS, DNS server, nginx, Cockpit).
- **Applications**: software installed through Chocolatey, apt or dnf, or from an installer in the Files tab (for example Firefox, VS Code, 7-Zip).
- **Packages**: named groups of services, applications and other packages.

Applications that need a desktop (such as Firefox) are skipped on Windows Server Core hosts instead of failing the deploy. The **Syslog collector** service gives a Linux VM a syslog server: UDP and TCP 514, TLS 6514, and mutual TLS 6515 with a client certificate from the platform CA; messages land in `/var/log/remote/`.

If what you need isn't there, open the **Requests** tab and choose **New request**. An admin accepts, declines or closes it with a note, which you see on the same tab.

## 9. Hosts and networks

The new-environment form shows the KVM hosts and networks you may use. **Networks** lists the networks you may deploy on, filtered by name, address (network, gateway, pools, DNS), addressing and VLAN. Admins manage both under **Admin** ([Web-Interface-Admin.md](Web-Interface-Admin.md)).

## 10. Confirmations, tooltips and maintenance

- Every button shows a short tooltip. Buttons that change something ask you to confirm first, in a dialog that explains what will happen; their tooltip ends with "(asks to confirm)".
- **Maintenance mode**: while an admin or an update has LABaPe in maintenance, a banner on every page shows why, who started it and when it should end. Changes are refused and their buttons are disabled ("Unavailable during maintenance"). You can still look at everything, follow job logs, cancel a job and sign out. Running jobs finish; new ones wait until maintenance ends.
- A banner also warns ahead of a scheduled maintenance or the weekly maintenance window.

## 11. API tokens

Scripts and CI can call LABaPe's API with a token instead of signing in. Open your account menu (your name at the top right) → **API tokens**:
- **New token**: a name, the roles it may use (none chosen: all of yours) and when it expires (at most a year). The token is shown once: copy it then.
- A token acts as you, with the roles you chose, but never more than you have when it's used. If you lose a group, the token loses that role too (at your next sign-in).
- Send it in a header:

  ```bash
  curl -H "Authorization: Bearer lpat_…" https://labape.example/api/environments
  ```

- Everything a token changes is in the audit log. A token can't make other tokens.
- **Revoke** stops it at once. The list shows each token's first characters, expiry and last use.

Admins can also make **service tokens**, which aren't tied to a person (see [Web-Interface-Admin.md §18](Web-Interface-Admin.md#18-api-tokens-and-metrics)).

