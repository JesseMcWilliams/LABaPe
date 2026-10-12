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

## 1. Signing in and your roles

Open the address your admin gave you and choose the sign-in button (usually **Authentik**). LABaPe gives you roles from your Authentik groups:

| Role | Default Authentik group | What it adds |
|---|---|---|
| viewer | `labape-viewers` | Sign in and browse; environments can't be shared with other users yet, so a viewer sees no environments |
| deployer | `labape-deployers` | Create, deploy, redeploy and destroy your own environments; see their credentials |
| template_editor | `labape-template-editors` | Everything a deployer can do, plus build, refresh, promote and edit VM templates |
| file_manager | `labape-file-managers` | Upload, download from a URL, edit and delete files |
| admin | `labape-admins` | Everything, including hosts, networks, the catalog, settings and secrets |

Everyone signed in can browse files, the catalog, and the hosts and networks they may use.

**Your account menu.** Click your name at the top right to see your roles, groups, sign-in provider, this and your previous sign-in, and when your session ends.

**Reload rights.** If someone adds you to a group, choose **Reload rights** in the account menu. LABaPe asks Authentik for your current groups without signing you out, then recalculates your roles. If Authentik needs you to sign in again first, LABaPe says so: sign out and back in.

## 2. The dashboard

The dashboard is the landing page; clicking the app name at the top left always returns to it. It refreshes every minute.

- **KVM hosts** you can deploy to: CPU, RAM, free space and disk activity, each with a 24-hour trend.
- **Your environments**: how many VMs are online, and for each VM its CPU, RAM, free disk space and disk activity.
- **Download the root certificate**: the platform CA's root, for your browser (see [§4](#vm-certificates)).

A VM's free space and "responding" state come from the QEMU guest agent, which LABaPe installs on every new VM. VMs built before the agent was added show "unknown" and the size of their disk file instead.

## 3. Creating an environment

Choose **Environments → New environment** (deployers and template editors). The form:

| Field | Notes |
|---|---|
| Name | 3-40 lower-case letters, digits and hyphens. It's also the environment's OpenTofu workspace. |
| KVM host | Only enabled hosts are offered. The bars below show the host's load **with this environment added**. |
| Host groups | One or more groups of identical VMs. **Add host group** adds another. |

Each host group sets:

| Field | Notes |
|---|---|
| Group name | 1-12 lower-case letters and digits |
| Count | 1-20 VMs |
| OS and Roles | Roles: `domain_controller`, `windows_server`, `windows_workstation`, `linux_server`, `linux_workstation` |
| Image source | **Build from template** clones a template (minutes). **Build from ISO** installs from the OS's ISO (up to an hour or more for Windows). |
| Template | The templates on that host for that OS, with their description. An info icon shows the template's notes (quirks, after-tasks). |
| vCPUs, Memory, Disk | An amber warning appears when you go below the template's minimums. A disk smaller than the template's is raised to the template's size. |
| Network, Addressing | Only the networks attached to the host that you may use, and only the modes that network allows. **Static** addresses come from the network's pool automatically; **DHCP** addresses are found after the VM boots. Domain controllers are always static. |
| Services, Applications, Packages | Catalog items added on top of the template (see [§8](#8-the-catalog-services-applications-and-packages)). Only published items with something for this OS are offered. |
| Host names | One box per VM. Leave a box blank for `<group><n>` (for example `web1`). Names must be unique across every environment on the same KVM host, and Windows allows at most 15 characters. |

**Load limits.** The form warns above 4 vCPUs per CPU, above 90% of the host's RAM committed, or below 15% free VM storage. It refuses an environment that would commit more than 100% of the host's RAM, and **Create and deploy** stays disabled.

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

<a id="vm-certificates"></a>**VM certificates.** When the platform CA is set up, every VM deployed from the web interface gets a certificate for its short name, its full name and its address, renewed on the VM before it expires. Each VM also trusts the LABaPe root. To trust services on those VMs from your own browser, download the root certificate from the dashboard and import it.

## 5. Jobs

Deploys, destroys, template builds, downloads and backups run as jobs. **Jobs** lists them; each job's page shows its live log (**Follow** keeps the newest lines in view).
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

**Files** has a tab for each location: **ISOs**, **Installers**, **Other files**, and the **Templates** library. Everyone can browse them. Admins and file managers can change them.

**Uploading.** Choose the upload button on a tab and pick a file; LABaPe asks you to confirm.
- Uploads resume after a dropped connection.
- A file with the same content as one already stored is refused as a duplicate, and a name already in use is refused before the upload starts.
- **Verify against a hash**: paste the vendor's value (SHA-256, SHA-1, SHA-512 or MD5) or a checksum-file line such as a `SHA256SUMS` entry. If the file doesn't match, it's deleted.

**Download from URL.** The worker fetches the file as a job, with progress in the job log; a retried job resumes where it stopped.
- HTTPS only, unless an admin allows plain HTTP (Settings → Downloads).
- Leave the name empty to use the last part of the URL.
- The optional expected hash works as for uploads.

**ISOs.** Open an ISO to set which OS it installs (LABaPe guesses from the name) and whether it's the **default** for that OS. Template builds and ISO installs use the default ISO, or else the newest one.

**Templates** (template editors):
- **Build a template** installs an OS from its ISO with Packer and saves it under a new name. Linux takes about 15 minutes, Windows up to an hour or more. For Windows Server, tick **Server Core (no desktop)** for a Core template.
- **Refresh** clones a template, applies every OS update and saves the result under a new name. The original is left as it is.
- **Promote to template** turns a VM from an environment into a template (see [§4](#4-working-with-an-environment)).
- Open a template to edit its description, notes, edition and minimum vCPUs and memory. The deploy form uses them for its warnings.
- Templates are never overwritten: give each new one a new, dated name, such as `rocky9-base-2026.10`.

## 8. The catalog: services, applications and packages

**Catalog** lists what host groups can add on top of their template:
- **Services**: Windows roles and features, or Linux packages and systemd units (for example IIS, DNS server, nginx, Cockpit).
- **Applications**: software installed through Chocolatey, apt or dnf, or from an installer in the Files tab (for example Firefox, VS Code, 7-Zip).
- **Packages**: named groups of services, applications and other packages.

If what you need isn't there, open the **Requests** tab and send a request. An admin accepts, declines or closes it with a note, which you see on the same tab.

## 9. Hosts and networks

**Hosts** lists the KVM hosts you may use, with their load. **Networks** lists the networks you may deploy on. Non-admins see both read-only; admins manage them ([Web-Interface-Admin.md](Web-Interface-Admin.md)).

## 10. Confirmations, tooltips and maintenance

- Every button shows a short tooltip. Buttons that change something ask you to confirm first, in a dialog that explains what will happen; their tooltip ends with "(asks to confirm)".
- **Maintenance mode**: while an admin or an update has LABaPe in maintenance, a banner on every page shows why, who started it and when it should end. Changes are refused and their buttons are disabled ("Unavailable during maintenance"). You can still look at everything, follow job logs, cancel a job and sign out. Running jobs finish; new ones wait until maintenance ends.
- A banner also warns ahead of a scheduled maintenance or the weekly maintenance window.
