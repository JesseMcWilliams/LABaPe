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
12. [Certificates](#12-certificates)
13. [Help](#13-help)
14. [Environment templates](#14-environment-templates)

## 1. Signing in and your roles

Open the address your admin gave you and choose the sign-in button (usually **Authentik**). LABaPe gives you roles from your Authentik groups:

| Role | Default Authentik group | What it adds |
|---|---|---|
| viewer | `labape-viewers` | Sign in and browse; sees the environments shared with them (see *Sharing* in [§4](#4-working-with-an-environment)) and nothing else |
| deployer | `labape-deployers` | Create, deploy, redeploy and destroy your own environments; see their credentials |
| template_editor | `labape-template-editors` | Everything a deployer can do, plus build, refresh, promote and edit VM templates |
| file_manager | `labape-file-managers` | Upload, download from a URL, edit and delete files |
| console | `labape-console` | Open text sessions and run commands on VMs through the console API and the MCP server ([§11](#11-api-tokens)); the web interface's own pages need no more than your other roles |
| auditor | `labape-auditors` | Read the audit log, the syslog messages and the remote-session log, and play session recordings; change nothing |
| ca_requester | `labape-ca-requesters` | Request certificates from the platform CA ([§12](#12-certificates)) |
| ca_auditor | `labape-ca-auditors` | See every certificate and the CA's details; change nothing |
| ca_admin | `labape-ca-admins` | Manage the platform CA: see every certificate, revoke, edit its settings |
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

The top bar stays in view while you scroll, and **Help** in it opens these guides inside LABaPe ([§13](#13-help)).

Admins and auditors also see:
- **All stacks**, when this LABaPe is linked with others: totals across every linked stack (a machine two stacks both use is counted once) and a card per stack with its version, hosts, VMs, environments, jobs and open alerts. A stack that stopped answering shows its last data, marked "as of" when it last answered. A card opens that stack's own dashboard in a new tab (with its own sign-in). The card of the stack you are connected to is marked **you are here** and has a highlighted border.
- **Alerts**: problems the monitoring found (a host running hot or nearly full, a stopped container, a missing backup, a linked stack not answering), with when each started, and the ones recently cleared. Admins can **Ack** an alert (someone is on it) or **Silence** it for a while with a reason.

A VM's free space and "responding" state come from the QEMU guest agent, which LABaPe installs on every new VM. VMs built before the agent was added show "unknown" and the size of their disk file instead.

## 3. Creating an environment

**Environments** lists the environments you can see, filtered by name, status, KVM host, OS, who created it and when, with **Export** as CSV.

Choose **Environments → New environment** (deployers and template editors). The form:

| Field | Notes |
|---|---|
| Name | 3-40 lower-case letters, digits and hyphens. It's also the environment's OpenTofu workspace. |
| KVM host | Only enabled hosts are offered. The bars below show the host's load **with this environment added**. With more than one host, **Suggest best host** ranks them for your host groups (memory and CPU in use, running jobs, whether the templates are already there), picks the best and shows why. |
| Time zone | The VMs' time zone (by default your browser's, or UTC). It's shown on the environment's page and can't be changed later. |
| Host groups | One or more groups of identical VMs. **Add host group** adds another. |

The form checks what you type as you type it: a group name used twice (also when the names differ only in case), a disk below the OS's minimum, a template for another OS. The **Role** box follows the OS: choose a Windows OS and only Windows roles are offered.

Each host group sets:

| Field | Notes |
|---|---|
| Group name | 1-12 letters and digits, starting with a letter. Upper and lower case may mix (`Web` gives `Web1`), but two groups of one environment can't differ only in case. |
| KVM host | Shown when there's more than one host. **Same as the environment** keeps the group with the rest; another host puts this group's VMs there (one environment spread over hosts). The group's networks and templates then come from that host. The hosts must carry the same lab network, so the VMs reach each other and the domain controller. Each VM keeps its host: power, snapshots, details and consoles go to it. |
| Count | 1-20 VMs |
| OS and Role | One role per host group: `domain_controller`, `windows_server`, `windows_workstation`, `linux_server` or `linux_workstation` |
| Domain | Shown when the environment has a domain controller. **Member** (the default) joins the group's VMs to the domain; **Standalone** leaves them out of it. A domain controller is always part of its domain. |
| Image source | **Build from template** clones a template (minutes). **Build from ISO** installs from the OS's ISO (up to an hour or more for Windows). |
| Template | The templates on that host for that OS, with their description. An info icon shows the template's notes (quirks, after-tasks). Templates held by a **linked stack** are listed too, marked "on the linked stack …: copied here at deploy"; the deploy copies the one you chose into this stack's library first (the first copy of a big template takes a while; see [§7](#7-files-and-templates)). |
| vCPUs, Memory, Disk | An amber warning appears when you go below the template's minimums. A disk smaller than the template's is raised to the template's size. The form refuses a disk below the OS's own minimum (Windows 11: 64 GB). |
| Network, Addressing | Only the networks attached to the host that you may use, and only the modes that network allows. **Static** addresses come from the network's pool automatically; **DHCP** addresses are found after the VM boots. Domain controllers are always static. |
| Services, Applications, Packages | Catalog items added on top of the template (see [§8](#8-the-catalog-services-applications-and-packages)). Only published items with something for the group's OS are offered, packages included; changing the OS drops the choices that no longer fit. A package with members for both Windows and Linux installs only those for the group's OS. |
| Host names | One box per VM. Leave a box blank for `<group><n>` (for example `web1`). Names must be unique across every environment's VMs on the same KVM host, and Windows allows at most 15 characters. |

**Load limits.** The form warns above 4 vCPUs per CPU, above 90% of the host's RAM committed, or below 15% free VM storage. It refuses an environment that would commit more than 100% of the host's RAM, and **Create and deploy** stays disabled.

**Quotas.** Your admin may limit how many environments you have and how many vCPUs, how much memory and VM disk they use together. The Environments page shows "Your quota: N of M …". An environment that would go over it is refused, with the numbers; destroy an environment, make this one smaller, or ask an admin. Destroyed environments don't count.

**Create and deploy** asks you to confirm, saves the environment and starts a deploy job at once. You land on the environment's page.

**Removing a host group:** each group has a **Remove host group** button, and setting its **Count** to 0 asks whether to remove it (Cancel puts the count back to 1). The only group can't be removed.

**Start from a template** (at the top of the form) fills the form from a saved template: see [§14](#14-environment-templates).

**Save as draft** keeps what you typed without deploying. A draft takes no quota, no addresses and no VM names until it is deployed, and only you (and admins) see it. Its page shows **Edit draft**, which opens the form again with your choices (**Create and deploy** then saves and deploys it), and **Deploy**, which checks everything a new environment is checked for. **Duplicate**, on any environment's page, opens the form with a copy of its host groups under the name `<name>-copy`: the VM names are cleared and no accounts or passwords are copied.

## 4. Working with an environment

The environment's page shows its state, its host groups with each VM's address, the hosts-file entries, and its jobs. Owners see these actions:

| Action | What it does |
|---|---|
| **Deploy** / **Redeploy** | Runs OpenTofu and Ansible. A redeploy creates missing VMs and reconfigures existing ones. It reinstalls an existing VM only if what it was installed from changed (see *What a redeploy keeps*, below). |
| **Preview redeploy** | Shows what a redeploy would do to each VM (reinstall, create, remove, update or keep) and why, without applying anything. It's a snapshot: a later change to Settings or a LABaPe update can change the next redeploy. From the command line: `deploy.sh ... --plan-only <file>` ([Command-Line.md §1](Command-Line.md#1-deploying-and-destroying)). |
| **Sharing** (panel) | Owners and admins share the environment with a user (someone who has signed in) or a group: they see it and its details, while their roles still limit what they can do. **Hand over** makes another account the owner, for example when the owner has left; the previous owners keep access unless you untick it. Sharing and removing it are audited. |
| **Add host** | On a deployed or failed environment: more VMs in a group, or a new group like an existing one (its OS, size, network, role, template and software, under a new name). It is checked like a new environment (quota, host capacity, names), then starts a redeploy that creates only the new VMs. |
| **Destroy** | Deletes every VM, its disks and the OpenTofu workspace, and releases its addresses. It can't be undone. |
| **Delete** | On a destroyed (or never deployed) environment: removes the record, its stored secrets and rotation settings, and frees the name for a new environment. Jobs and the audit log keep their rows. Nothing on a KVM host changes. |
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
**What a redeploy keeps.** A redeploy doesn't wipe an existing VM: things you changed inside it stay, files, installed programs and data included. Two things can still change them:
- **LABaPe manages the setting.** The software roles, accounts, desktop sessions and other configuration of the environment are applied again, so a managed setting you changed by hand is set back to what LABaPe has (a package you installed or a file LABaPe doesn't manage is left alone).
- **The VM is reinstalled.** A VM is built once from an install recipe, and it is thrown away and built again (everything on it is lost) when that recipe changes: its operating system image or template, its CPU, memory or disk size, its network, host or storage folder, or its install answers: name, administrator user, addressing (IP address, gateway, DNS), the management source and syslog address from Settings, a Windows edition, and the answer files that ship with LABaPe (an update that changes them reinstalls VMs on their next redeploy). Rotating the VM key or the Windows bootstrap password, comments in the Windows answer files, and the VM's MAC address do not.
If you aren't sure, choose **Preview redeploy** first, or turn on automatic snapshots before a redeploy (Settings) or snapshot the VMs first. This is how the KVM deployment behaves; Hyper-V's was not checked.

- **RDP** opens a Remote Desktop session to a Windows VM in the browser, **.rdp** downloads a file for your own client (it asks for the password), and **SSH** opens a terminal to a Linux VM in the browser. Your admin may record these sessions (the session page says so) and closes idle ones after a while. **Copy and paste** works in both directions, as text: what you copy in the session lands on your computer's clipboard (click the page once if the browser holds it back), and what you copied on your computer goes to the session when you press Ctrl or use the **Paste** button; then paste there (RDP: Ctrl+V; SSH: Ctrl+Shift+V or a middle click). The browser may ask to allow reading the clipboard. Files and images aren't copied. Windows VMs deployed before Remote Desktop was turned on need a **Redeploy** first.
- **Save as template** keeps this environment's host groups as a template ([§14](#14-environment-templates)).
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

**Secrets.** Owners (and admins) see the environment's **Secrets** panel: LABaPe's own (the admin and account passwords, the credentials sheet, the inventory) and any the owners stored. Values stay masked until you choose **Show** or **Copy**, and each time is in the audit log. Search and the Group, VM and Store filters narrow the list; it pages like the other lists. **Sign in as** shows the user name to type for each account, with a copy button: for a Windows VM's local Administrator that's the VM's name in front (`L2WIN1\Administrator`, or `.\Administrator`), since a bare `Administrator` is completed with your own computer's domain; on a domain controller it's the domain's. The credentials sheet and the **.rdp** file name the VM the same way. An account has **Connect**: choose a VM and LABaPe opens RDP (Windows) or SSH (Linux) in the browser signed in as that account, without showing its password. A local account connects to its own VM; a domain account to the member servers and the Linux VMs, and domain admins to the domain controllers too.

**Mail server.** Add the catalog service **Mail server (Mailpit)** to a Linux host group to give the environment a mail server for testing: point applications at the SMTP address the Secrets panel shows (port 25, STARTTLS available), and read what arrived in its web UI (HTTPS on port 8025, user `mailpit`, password `vm/<vm>/mailpit` on the Secrets panel). Mail stays there unless your admin set up the lab mail relay for particular recipients.

**Desktop sessions (Linux).** A Linux host group can have **xrdp** (a desktop over RDP for every account, local or domain) and **VNC** (each local account gets a desktop of its own, signed in with its password). Choose them when creating the environment, or on its page under **Desktop sessions (Linux)**, then **Redeploy**; **Connect** then offers RDP and VNC for those VMs. **Store a secret** saves one of your own, for the whole environment or for one VM; **Replace** and **Delete** change your own. LABaPe's passwords change through rotation, not here. In OpenBao the environment's secrets are in the folder the panel shows, `env/<environment>/`. Where your admin has turned it on, the panel also links to **OpenBao's web UI**: sign in with **OIDC** (your LABaPe account), open the secrets engine it names and go to the folder. From a shell, set `BAO_ADDR` as shown and run `bao login -method=oidc`, then for example `bao kv get -mount=labape env/<environment>/accounts/windows_admin`. You read everything in your environment's folder and can store, change and delete under `user/` and `vm/<vm>/`; what you store there appears on the page within a few minutes. Access follows ownership: lose ownership and the next sign-in can't read the folder.

**Secrets on the VMs.** A VM can read its own secrets itself: on Linux `sudo labape-secret get NAME`, on Windows (elevated) `Get-LabapeSecret NAME`. It reads what you stored for that VM, and the shared secrets whose **VMs that read it themselves** list names it (set it when you store or replace a shared secret; replacing with an empty value changes only the list). The other VMs can't. A new or changed list reaches OpenBao within a few minutes; a VM deployed before gets the command at its next **Redeploy**.

**Accounts.** The environment's **Accounts** panel lists accounts beyond the built-in admins: **domain** users (on the environment's domain controller) and **local** users on the hosts of chosen roles. Owners choose **Edit** to add or remove them; **Redeploy** then creates new ones on the VMs. Each gets a generated password, shown on the credentials sheet under "Environment accounts" (a local account has a different one on each VM), and changed weekly. A local account is bound to roles (every VM with the role; the VM names are shown beside each role). **Only these VMs** under the roles narrows it to the VMs you tick; left blank, every VM of the roles gets it. **Sudo, no password** (Linux accounts) lets the account run `sudo` without a password, through one file LABaPe manages on each VM; unticking it, or the last Linux host, removes it at the next deploy. Removing an account from the list stops LABaPe managing it and forgets its password; it stays on the VMs (remove it from the VM's Accounts section).

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

**Download from URL.** The worker fetches the file as a job, with progress in the job log; a retried job resumes where it stopped. Addresses on the stack itself are refused: loopback, `localhost`, link-local, names without a dot (how the stack's own services are reached) and the stack's own container network. Your lab's other networks are fine.
- HTTPS only, unless an admin allows plain HTTP (Settings → Downloads).
- Leave the name empty to use the last part of the URL.
- The optional expected hash works as for uploads.

**ISOs.** Open an ISO to set which OS it installs (LABaPe guesses from the name) and whether it's the **default** for that OS. Template builds and ISO installs use the default ISO, or else the newest one. **A linked stack's ISO is copied for you.** A deploy or template build whose OS has no ISO on this stack takes one from a linked stack that has it (the one marked default there, else the newest by name), as the first step of the job; the job's log shows the copy. The same goes for the installers of the applications you chose. If no linked stack has it, the job says **"No ISO for ..."** or **"the install ISO ... isn't on this host"**: add one here with its OS set, then build or deploy again. Nothing is created before this is checked, so there is nothing to clean up. If it says the content is already stored under another name, open that file and set its operating system.

**Linked stacks.** When this stack is linked with others, the **Linked stacks** tab lists the templates and files each one offers. **Copy here** (file managers) copies one onto this stack as a job: a file lands in the same tab it came from, a template in the template library. A dropped connection resumes where it stopped when you copy it again, and a name that already exists is refused. A template can also be chosen straight in the New environment form and is copied when you deploy.

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
- Revoked and expired tokens are hidden; tick **Show revoked and expired** to see them. They are removed after the retention your admin set (Settings → History).
- A token acts as you, with the roles you chose, but never more than you have when it's used. If you lose a group, the token loses that role too (at your next sign-in). If your account is deactivated or removed in the sign-in provider, your personal tokens are revoked within about 15 minutes.
- Send it in a header:

  ```bash
  curl -H "Authorization: Bearer lpat_…" https://labape.example/api/environments
  ```

- Everything a token changes is in the audit log. A token can't make other tokens.
- **Revoke** stops it at once. The list shows each token's first characters, expiry and last use.

A token may also carry the CA roles (`ca_requester`, `ca_auditor`, `ca_admin`) to request, list and revoke certificates through the API.

A token with the **console** role can run commands and open text sessions on your environments' VMs, for scripts and AI agents (see [Web-Interface-Admin.md §20](Web-Interface-Admin.md#20-console-access-and-the-mcp-server-for-ais-and-scripts)).

Admins can also make **service tokens**, which aren't tied to a person (see [Web-Interface-Admin.md §18](Web-Interface-Admin.md#18-api-tokens-and-metrics)).

## 12. Certificates

**Certificates** (top bar) lists certificates signed by the platform CA: the ones you requested and those of environments you own. CA administrators, CA auditors and auditors can tick **Everyone's certificates**. Search by name, address, serial, requester or host, and filter by state (active, expiring, expired, revoked) and source (asked for here, a deploy, the stack's own); the filters apply as soon as you choose. Each row shows when it was **issued**, when it **expires** (and the days left), its state, who requested it and for which environment. Open one to see its details, including its **Uses** (what it may be used for), the PEM, and **Download**.

**Requesting a certificate** (the `ca_requester` role; admins hold it too). On the **Request** tab choose **LABaPe makes the key** (give a common name, more DNS names and addresses: LABaPe makes a key pair and shows the private key **once**, for you to download; it is not kept) or **I have a CSR** (paste your request; only the certificate comes back). You may ask for names under the stack's own names and the domains the CA administrator allowed, no wildcards, and for addresses in the allowed networks; the page says which. The lifetime is 30 days by default, at most 90. Tie the certificate to an environment and its owners see it too. Trust the CA's root (download it from the result or the dashboard) on the machines that use the certificate.

**Attributes.** With none chosen the CA gives its default: server and client authentication. A CA administrator can turn on extra attributes (**The CA** tab → **The CA applies extra attributes**, after a one-time command in the CA container; see [Web-Interface-Admin.md §21](Web-Interface-Admin.md#21-the-platform-ca-and-certificates)). Then you may also choose code signing, email protection (S/MIME), time stamping, OCSP signing, smart-card logon, and the key usages **Signing** and **Encryption**. A certificate that comes back without what you asked for is revoked and refused.

**Renew…** on a certificate opens a new request with the same names: a new key, a new certificate. **Revoke…** (CA administrators) marks a certificate revoked with a reason. Revocation is **passive**: the CA refuses to renew it and answers a revocation check, but a client that never checks keeps trusting it until it expires.

CA administrators (`ca_admin`) also see **The CA** tab: whether the CA answers, its address, root certificate and fingerprint, the register counts, the lists of allowed names and networks and the lifetimes, and **Add to the register** for a certificate the CA signed outside LABaPe. CA auditors (`ca_auditor`) see the same tab and every certificate but change nothing. In Authentik the groups `labape-ca-admins`, `labape-ca-requesters` and `labape-ca-auditors` give the three roles.

## 13. Help

**Help** in the top bar opens these guides inside LABaPe, so nobody needs the files: a section list on the left and the text on the right. The guide for admins shows only to admins and auditors, and the command-line guide only to admins. Every page also has a **Help for this page** link at its top right that opens the guide at the section for that page.

## 14. Environment templates

A template is an environment's host groups (how many, which OS, role, size, network, software, start order) **without** its name, KVM host, VM names, addresses, accounts or passwords, plus the few values you fill in when you start from it. **Environments → Templates** lists them.

- **Start from a template.** On the New environment form, open **Start from a template**, pick one, fill in its values (for example the number of Linux members) and press **Use this template**. The form is filled in and nothing is created until you press Create and deploy as usual, so every check still applies and you can change anything first. Below the button, notes say what the chosen KVM host or the catalog lacks: a catalog item that is retired or gone, an OS template the host doesn't have (when a linked stack has it, it is copied when the environment deploys), a network the host doesn't carry. When the host has exactly one template for a group's OS it is filled in; with several you pick.
- **Save as template.** On an environment's page, **Save as template** asks for a name and a description, whether to share it with everyone who can deploy (otherwise it is private), and which values can be changed when starting from it (per group: number of hosts, OS, network, CPUs, memory, disk).
- **Starters.** LABaPe ships three: *Domain with members*, *Three-tier Linux (standalone)* and *Kerberos lab*. They can't be changed or deleted.
- **Parts.** **New from parts** makes a template out of other templates: each part brings its host groups with a prefix (up to 6 letters) in front of their names, so the same part can be used twice. Its values are asked for with the part's prefix, and you can set values for a part's groups (hosts, OS, network, CPUs, memory, disk): a value replaces the part's own, and if the part lets it be changed it is still asked for, with yours as the starting value. **Edit** changes the name, description, sharing and the parts. A part can **follow the latest** version of its template or be **pinned** to one; each change to a template's groups, parameters or parts is a new version, and a pinned part says when a newer one exists. A template that is part of another can't be deleted until it is taken out.
- **History.** **History** on a template's row lists its versions (who saved each and when), compares any two in plain sentences (host groups added, removed or changed field by field, values asked for, parts and their pins) and **Restore**s an older one: its groups, values and parts become the template's new version, so nothing is lost and a restore can be undone the same way. The name, description and sharing are not part of a version. A starter's history can be read but not restored.
- **Move templates between stacks.** **Export** (per template, with the templates it is made of) and **Export all** download YAML; **Import** creates the templates in such a file on this stack (all or none; names that already exist are never overwritten; imported templates are private until you share them).
- Who sees what: private templates only their owner (and admins); shared ones everyone who can deploy; the owner or an admin edits or deletes.
