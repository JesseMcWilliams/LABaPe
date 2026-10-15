# Administering the LABaPe web interface

This guide is for LABaPe admins (the `admin` role, `labape-admins` by default). It covers getting the web interface running, then everything under **Admin** (the button at the top: a list on the left with KVM hosts, Networks, Quotas, Settings, Secrets, Audit log, User errors, Remote sessions and Linked stacks) and the Catalog, plus monitoring, backups, updates and the audit trail. Lists such as Jobs, the audit log and user errors have filters that stay in the address, so a filtered view can be bookmarked. For what every user sees, read [Web-Interface-Guide.md](Web-Interface-Guide.md) first.

## Contents

1. [Installing](#1-installing)
2. [First sign-in and break-glass access](#2-first-sign-in-and-break-glass-access)
3. [KVM hosts](#3-kvm-hosts)
4. [Networks](#4-networks)
5. [The catalog](#5-the-catalog)
6. [Settings](#6-settings)
7. [Maintenance mode](#7-maintenance-mode)
8. [Secrets and rotation](#8-secrets-and-rotation) ([external stores](#81-external-secret-stores))
9. [Updating LABaPe](#9-updating-labape)
10. [Audit log and user errors](#10-audit-log-and-user-errors)
11. [Commands inside the containers](#11-commands-inside-the-containers)
12. [Monitoring and alerts](#12-monitoring-and-alerts)
13. [Backups and rebuilding a stack](#13-backups-and-rebuilding-a-stack)
14. [Linked stacks](#14-linked-stacks)
15. [Quotas, leases and placement](#15-quotas-leases-and-placement)
16. [Remote access and auditors](#16-remote-access-and-auditors)
17. [The syslog collector](#17-the-syslog-collector)
18. [API tokens and metrics](#18-api-tokens-and-metrics)

## 1. Installing

LABaPe installs on a Debian KVM host with `sudo deploy/install-labape.py`, run from a checkout of the repository. It asks only for what the chosen steps need, saves the answers (never a secret) for later runs, and can `--resume` after a failure or show everything with `--dry-run`. The full reference is [deploy/README.md](../deploy/README.md).

| Profile | Installs |
|---|---|
| `--profile full` (default) | The KVM host and the web interface on one machine |
| `--profile kvm` | The KVM host only, for a web interface elsewhere. Pass the web interface's worker key with `--set WorkerPublicKey='ssh-ed25519 ...'`. |
| `--profile web` | The web interface only, driving a remote KVM host over SSH (`--set RemoteKvmUri=qemu+ssh://user@host/system`) |

After the stack is up, `labape bootstrap` (run by the installer) registers the host and its networks.

**Sign-in.** LABaPe signs people in through OpenID Connect, Authentik by default. The installer either takes an existing provider (issuer, client ID, and the client secret, which it prompts for) or starts a throwaway Authentik for testing ([tools/authentik-test/](../tools/authentik-test/README.md)). In Authentik, the redirect URI is `https://<web host>/api/auth/oidc/callback`, and roles come from groups; see [container/README.md § Sign-in](../container/README.md#sign-in-containerenv) for the mapping and how to change it.

**Running the stack by hand** (Docker or Podman, Quadlets, HTTPS options): [container/README.md](../container/README.md).

**Platform CA and the browser.** The stack runs its own certificate authority. Web-interface deploys give every VM a certificate from it. To serve the web interface with a certificate from the same CA, set `LABAPE_CADDYFILE=Caddyfile.labape-ca` in `container/.env` and recreate Caddy. Then have users import the root (dashboard → **Download the root certificate**). Back up `container/secrets/ca_password` together with the `labape-ca` volume: losing either means re-issuing every certificate.

## 2. First sign-in and break-glass access

There's no standing local admin password. For the first admin sign-in, or when every sign-in provider is down, someone with control of the container runtime runs:

```bash
docker compose exec labape-api labape breakglass enable [--minutes 5] [--local-only]
```

It prints a `/breakglass` URL and a one-time credential, valid for 5 minutes, which opens a 60-minute admin session. `--local-only` makes it work only from the host itself (`https://localhost:8443`, or an SSH tunnel). A banner marks a break-glass session, and everything in it is audited. `labape breakglass revoke` cancels unused credentials. Details: [container/README.md § Break-glass access](../container/README.md#break-glass-access).

## 3. KVM hosts

**Hosts → Add** registers a host:

| Field | Notes |
|---|---|
| Name | How the host appears in forms |
| libvirt URI | `qemu:///system` for the host the stack runs on, or `qemu+ssh://user@host/system` for a remote one |
| VM storage path, Template storage path | Where VM disks and the template library live on that host |
| Fallback bridge | Used when a network has no bridge on this host |
| Concurrent jobs | How many jobs run at once on this host |

**Remote hosts.** Install the remote host with `--profile kvm`. The host's page shows the worker's SSH key in a **Remote access over SSH** panel, with Copy; add it to that user's `~/.ssh/authorized_keys` there. Then use **Check connection**: it reports the libvirt version, the tools jobs need, and whether the storage paths are writable. Jobs for a remote host run on that host over SSH. One environment stays on one host.

**Networks on a host** (the host's page → Networks): attach each catalog network the host carries, with the host's bridge for it, an optional slice of the network's static pool for this host only (so hosts sharing a VLAN never collide), and which network is the default. A network still used by an environment on the host can't be detached.

**Roles.** Each host is a **KVM** host (runs VMs), a **stack** host (runs a LABaPe stack only, such as a Raspberry Pi), or **both**. A stack host has a **Stack address** (its web address) and a **Stack** section instead of VMs; Prepare isn't offered for stack-only hosts.

**A host's page** has sections on the left: **Dashboard** (warnings such as failed disks, heat, nearly full filesystems, failed services, pending reboots; key facts; 24-hour trends), **Drives and volumes** (loop devices hidden unless you tick **Show loop devices**), **NICs**, **OS**, **Accounts**, **Groups**, **VMs** or **Stack**, **SSH access** and **Settings**.
- **SSH access → Set up access**: give an existing admin login on the host once. LABaPe signs in with it, creates its own access account there (default `localadmin01`: key only, its password locked, passwordless sudo), checks it, and from then on reads facts hourly, metrics every 5 minutes (kept 7 days) and disk health daily. The password you typed is used once and never stored. A host whose SSH allows only keys refuses a password; turn password sign-in on for the setup, or add the key by hand.
- **Accounts** and **Groups**: add accounts (with groups and an optional SSH key), set passwords, lock, unlock and delete; add groups and members. Root and the access account are protected. Every change is audited.
- **Prepare this host** (Debian): installs the KVM-host profile over the access account (packages, libvirt, Ansible, OpenTofu, Packer, the bridges for its networks, the worker's key), then the host passes **Check**.
- **Logs to the syslog collector**: the host sends its own logs to the stack's collector over TLS ([§17](#17-the-syslog-collector)).
- **Open SSH**: a terminal on the host in the browser, as the access account.

**Primary file host.** One host holds every file LABaPe uses (ISOs, installers, other files, the template library): the **primary** (a chip in the host list; **Primary file host** in a host's Settings moves it). A job on another host gets the ISOs, installers and templates it needs copied there first, host to host through the worker; a file already there isn't copied again. **Files from a share** on a host says its admin mounted the primary's file locations at the same paths, so they're read there instead. Settings → File distribution sets how long a job waits for an unreachable primary (default 30 minutes), and can copy every template to every host daily ahead of need.

**Load.** Every enabled host is sampled each minute. The deploy form warns above 4 vCPUs per CPU, above 90% of RAM committed (every defined VM's memory, running or not) or below 15% free VM storage, and refuses an environment that would commit more than 100% of RAM.

## 4. Networks

**Networks** is the catalog of everything VMs may use; nothing outside it can be used.

| Field | Notes |
|---|---|
| Name, Description | For example `lab-vlan48` |
| CIDR, Gateway, DNS | The gateway defaults to the network's .1, DNS to the gateway. Domain members use their environment's domain controllers for DNS. |
| VLAN ID | A label only; LABaPe doesn't tag traffic |
| Addressing | Static, DHCP or both |
| Static pools | Ranges LABaPe assigns from. Required when static is allowed. |
| DHCP ranges | The DHCP server's scope: never assigned statically, and where LABaPe looks for DHCP VMs |
| Reserved | Never assigned |
| Limit to roles / Or limit to groups | Who may deploy on it. With neither, anyone who can deploy may. Group names are suggested as you type once Settings → Directory is set up. |
| Enabled | A disabled network keeps its environments but takes no new ones |

LABaPe refuses ranges outside the CIDR, pools that overlap the DHCP scope, the reserved ranges or the gateway, and networks that overlap each other. Static addresses are allocated when a deploy is queued, kept on redeploy and released by a successful destroy.

## 5. The catalog

Admins create, edit, publish and retire **services**, **applications** and **packages** (Catalog → the tab → Add). A new item starts as a draft.
- **Publish** makes it available in deploy forms. A package can be published only when everything in it is.
- **Retire** stops offering it. Environments already using it keep it on redeploy.
- **Export** downloads the selected items, or everything, as `labape_catalog` YAML, including what packages need. **Import** reads such a file as drafts or published, skipping or overwriting existing names.
- **Requests**: users ask for new items here. Accept, decline or close each one with a note; the requester sees it.

The definitions use the shapes in [Planning_Web-Interface-Design.md §21.4](../Claude_Docs/Planning_Web-Interface-Design.md#214-batch-d-built-services-applications-and-packages): an application is a Chocolatey, apt or dnf name and/or an installer from the Installers location; a service is Windows features and services and/or Linux packages, systemd units and an optional vetted configuration role (`linux.role`, see [§27](../Claude_Docs/Planning_Web-Interface-Design.md#27-syslog-collector-service-service-roles-built-2026-10-12)). The starter catalog includes a Linux **Syslog collector** service.

## 6. Settings

Settings is in sections, listed on the left: **Sign-in** (directory group search, VM accounts), **Stack** (maintenance, monitoring, branding, email, the audit trail's syslog, the syslog collector, history), **Updates and backups** (full stack and database backups), **Environments** (leases, VM snapshots, templates), **Files** (file locations and distribution, downloads), **Secrets** (the default store, OpenBao, Conjur) and **Remote access**. Each card saves on its own and asks you to confirm.

| Section | What it sets |
|---|---|
| Branding | The app name (up to 40 characters): top bar, browser title, sign-in page and emails. A logo (PNG, JPEG or WebP, at most 256 KB): top bar, sign-in page and browser tab. |
| File locations | The folder for ISOs, installers and other files, as paths inside the containers |
| Full stack backups | The whole stack in one encrypted archive, daily ([§13](#13-backups-and-rebuilding-a-stack)) |
| Database backups | A daily dump of LABaPe's database only (also taken before every update), at a set time (UTC), the folder and how many days to keep. **Back up now** runs one as a job; the list downloads any backup. Restore with `labape restore FILE --yes` (see [§11](#11-commands-inside-the-containers)). |
| Monitoring | Who gets alert emails, whether alerts go to syslog, and the thresholds ([§12](#12-monitoring-and-alerts)) |
| Environment leases, VM snapshots | [§15](#15-quotas-leases-and-placement); automatic VM snapshots before a deploy changes an environment (off by default; how many to keep per VM) |
| File distribution | How long jobs wait for an unreachable primary file host; copying every template to every host daily ([§3](#3-kvm-hosts)) |
| Templates | How many versions of a template to keep, and a monthly refresh of every template (on or off, day, time) |
| Remote access, Syslog collector | [§16](#16-remote-access-and-auditors), [§17](#17-the-syslog-collector) |
| History | Days to keep job logs, user errors and the audit trail (0 keeps the audit trail forever, the default) |
| Syslog | Sends every audit event to a syslog server over UDP, TCP or TLS (RFC 5424, JSON body). **Send test message** checks it. |
| Email (SMTP) | Server, port, security (none, STARTTLS, SSL/TLS), optional sign-in, from address. **Email people when their jobs finish** turns on job emails. **Send test email** checks it. A stack starts with this pointed at its own mail server (Mailpit, [§6.1](#61-the-stacks-mail-server)). |
| Directory (group search) | An Authentik API token that can only view groups (`authentik_core.view_group`), and the Authentik URL (blank uses the sign-in issuer's address). Group fields then search Authentik. **Test search** shows what the token can see. |
| Downloads | Allow plain HTTP for Download from URL (off by default), and an HTTP(S) proxy |
| VM accounts | The Linux admin user for new environments (default `labape`). Existing environments keep theirs. |
| Secret stores | The default store for new environment secrets, and an OpenBao/Vault and a Conjur section, each with **Test connection**. See [§8.1](#81-external-secret-stores). |
| Maintenance mode, Maintenance window | See [§7](#7-maintenance-mode) |

Passwords and tokens entered here are kept encrypted in LABaPe's built-in secret store, unless you move them to an external store ([§8.1](#81-external-secret-stores)), and never shown again.

### 6.1 The stack's mail server

Every stack runs its own mail server, Mailpit, and Settings → Email is set to it the first time the stack starts (if you hadn't set Email up yourself). So alerts and job emails work out of the box: LABaPe sends to Mailpit, which keeps every message. **Admin → Mail** shows whether it's running, how many messages it holds, and opens its web page (**Open Mailpit**, admins only).

- **Reading mail.** Everything LABaPe sent is in Mailpit's page, whether or not it was forwarded.
- **Forwarding to real inboxes.** On the Mail page, give Mailpit an SMTP server to forward through (server, port, security, sign-in, and the password, which is kept encrypted), then say which recipients it forwards: only the addresses and `@domain`s you list, or every recipient. Anything else stays in Mailpit. Forwarding off (the default) sends nothing outside. A server that only accepts its own sender needs **Replace the From address**. Saving restarts Mailpit within seconds; its messages are kept.
- **Alerts** go to the addresses in Settings → Monitoring ("Email alerts to"); with none set nothing is emailed. Put your address there and list it (or its domain) for forwarding.
- **Test.** **Send test** emails an address the way LABaPe sends its own.
- **If Email points elsewhere** the page offers **Use this server** to point it back.
- **Retention.** Mailpit keeps 5000 messages or 30 days by default; both are on the page.

The mail servers environments can have (catalog service Mail server) are separate, with their own forwarding account in Settings.

## 7. Maintenance mode

**Settings → Maintenance mode** turns it on now (a reason is required, with an optional expected end) or schedules it. While it's on:
- every change is refused with the reason, and change buttons are disabled for everyone;
- sign-in and sign-out, turning maintenance off, the update and rollback controls and cancelling a job still work;
- reads, the dashboard and job logs keep working;
- the worker starts no new jobs (running ones finish) and pauses backups, purges, the release check and rotations.

A banner on every page shows the reason, who started it, the expected end and the jobs still running. Turning it off is always explicit; it doesn't end on its own at the expected end.

**Maintenance window.** A weekly slot (day, start time in UTC, length) with a heads-up banner a set number of hours before. Secret rotations that restart services run only inside it.

Updates, rollbacks, restores and those rotations turn maintenance on and off themselves.

## 8. Secrets and rotation

**Secrets** has three tabs: **Stack** (the stack's own secrets), **KVM hosts** (the worker's key and each host's LABaPe access account) and **Environments** (each environment's accounts and passwords, its credentials sheet and inventory, filtered by environment, store, VM, local or domain, password or key, and when it last changed). Each tab lists what it is, which store holds it, its class, rotation interval, last and next rotation, and status. Open one to change its interval, turn scheduled rotation off, choose an elevated account or add notes; **Rotate now** rotates it at once. Every rotation and every change is audited.

**Health and recovery.** LABaPe checks that each stored secret still works (a sign-in, an API call, a key that opens what it locks): every quarter of its rotation period, at least daily; not at all for one that rotates every day (each rotation proves it); weekly for one LABaPe doesn't rotate. **Health** shows the result (ok, recovered, failed, not in use) with the last and next check; **Check now** checks at once. **Recoverable** says what happens when a check fails:
- **Yes**: LABaPe puts a working value back by itself, with an earlier value it kept or a new one set with another credential it holds (the rotation, the update agent, a host's access account), then checks again. The tooltip says how.
- **No**: there's no way back for LABaPe if the value is lost (the data key, the master key, the CA password, the domain Administrator, the SMTP password). The secret shows **failed**, an alert is raised (dashboard, syslog, email), and the tooltip says what a person must do.
A check that can't reach what it tests (a VM that's off) doesn't count as a failure.

**Recovery pairs.** Each environment with Windows hosts gets a second admin next to Administrator, `labape-admin2`: a domain admin (Domain Admins) when it has a domain controller, and a local admin on each member server. LABaPe keeps its password, rotates it weekly half a week apart from Administrator's, and uses it only for recovery: when Administrator's stored password stops working, `labape-admin2` sets it again, and the other way round. Environments deployed before get it on their next **Redeploy**.

**A password per VM.** Each Windows member server's Administrator and `labape-admin2`, and each local account of the environment's own on each VM, has a password of its own, so one VM's password opens nothing else. A new VM starts with the environment's bootstrap password, and the deploy changes it to the VM's own; if a deploy stops part way, the next one finds the VM's new password and carries on. The credentials sheet lists them per VM, and they rotate and are checked per VM. Domain controllers use the domain's accounts. Environments deployed before get theirs on their next **Redeploy**.

| Secret | Rotated by | Default |
|---|---|---|
| Built-in store data key | the worker | daily |
| Worker SSH key for remote KVM hosts | the worker | daily |
| Authentik directory token | the worker, with an elevated account | weekly |
| OIDC client secret | the worker, with an elevated account | daily |
| Master key, PostgreSQL password, platform CA password | the update agent, with a restart | weekly, in the maintenance window |
| Engine vault password (and the command line's vault on the same host), VM bootstrap SSH key | the update agent, no restart | daily |
| SMTP password | you, on the mail server | reminder only |
| OpenBao/Vault AppRole secret ID, Conjur host API key (LABaPe's own logins to the stores) | the worker, no restart; listed once the store is set up | daily |

**How a rotation works.** LABaPe makes the new value, applies it, checks it works, and only then switches. If anything fails, the old secret stays, the status shows the error (also on the dashboard and in syslog) and it retries an hour later.

**Restart-needing secrets** (master key, PostgreSQL, CA) restart the API and worker. They run at most weekly, inside the maintenance window, with maintenance mode on. A master key rotation signs everyone out.

**Elevated accounts.** Some secrets can't rotate themselves: a read-only Authentik token can't replace its own key. **Add elevated account** stores the credential of an account that can, and the secret's page names it. Without one, the Authentik directory token and the OIDC client secret show as **reminder** and aren't rotated. In Authentik, give the elevated token:
- for the directory token: `add_token` and `view_token` on that token;
- for the OIDC client secret: `view_oauth2provider` and `change_oauth2provider` on the LABaPe provider.

**OIDC client secret.** After the first rotation, LABaPe keeps the current client secret in its built-in store and uses it from the next sign-in, with no restart. The secret file the stack was installed with (`container/secrets/oidc_client_secret`) becomes a fallback. If you paste a new secret into that file later, the file takes over again.

**VM bootstrap key.** The 7 previous keys are kept and still tried, so a VM that was off during a rotation is still reached. The agent also copies the current key into the service user's `~/.ssh/labape_bootstrap` for the command line. Environments on remote KVM hosts are skipped.

### 8.1 External secret stores

Besides its built-in store (encrypted in LABaPe's database), LABaPe can keep secrets in **OpenBao** or **HashiCorp Vault** and in **CyberArk Conjur**. Each secret lives in one store, and several stores can be in use at once.

**Connecting a store** (Settings → Secret stores):

| Store | What LABaPe needs |
|---|---|
| OpenBao / Vault | The `https://` address, the KV version 2 mount (default `labape`) and an optional path prefix, the AppRole mount (default `approle`), role name and role ID, and the **secret ID**. Vault Enterprise: the namespace. |
| Conjur | The `https://` address, the account, the login (e.g. `host/labape/app`), the authenticator (default `authn`), the policy branch the host owns (default `labape/secrets`), and the host's **API key**. |

Certificates are checked against the LABaPe platform CA unless you paste another CA bundle; you can turn checking off, but don't outside a lab. **Test connection** signs in and reads something harmless; the dashboard's **Secrets and certificates** panel shows the same check. A store is offered for secrets only once it's turned on and its test passes.

Give LABaPe's identity rights to its own area and nothing else:
- **OpenBao / Vault:** read, write, list and delete under its KV mount, `read` on `auth/token/lookup-self`, and `update` on `auth/approle/role/<role>/secret-id` and `.../secret-id/destroy` so LABaPe can rotate its own secret ID. The full policy is in [Reference_Secrets-Backend-Requirements.md §7](../Claude_Docs/Reference_Secrets-Backend-Requirements.md#7-least-privilege-in-the-store).
- **Conjur:** a host that owns its policy branch. LABaPe declares a variable there the first time it writes one.

The secret ID and API key are kept in the built-in store, are never shown again, and rotate daily (the table above). After the first rotation, any other copy no longer works, but it isn't removed: LABaPe can't reach files outside its own containers. So after connecting a store:

1. Save the store's settings and check that **Test connection** passes.
2. On **Secrets**, open the store's login (*OpenBao / Vault: LABaPe's AppRole secret ID* or *CyberArk Conjur: LABaPe's host API key*) and choose **Rotate now**. From then on, only LABaPe holds a working login.
3. Delete every copy you made while setting it up: the file or note you pasted it from, and shell history. They no longer work, but they shouldn't be left lying around.

With the stores in the stack (`container/stores/`), LABaPe owns their logins, so there are no outside copies to clean up.

**Where secrets live.** **Secrets → Stored secrets** lists every stored secret: its name, its store, the rotation that changes it (with a link to its page) and the elevated account that rotation uses. Values are never shown.
- **Move**: tick secrets, choose **Move to** and confirm. LABaPe copies each one, reads it back, switches to the new copy, then deletes the old one. If any step fails, that secret stays where it was and the result says why.
- A secret's page has a **Store** field too: saving a new store moves that secret.
- **Files on KVM hosts.** Each KVM host's settings show its **Files path**: where it keeps copies of the files its jobs need (`ISOs`, `Installers`, `Other`), by default beside its VM folder (`/data/VMs/LABaPe` -> `/data/Files`). Change it there and LABaPe creates the new folders and moves the files (a job). The primary file host keeps the stack's file locations instead.
- **Catalog → Updates.** Download sites LABaPe checks weekly (day and hour on that tab; **Check all now** any time). A new version goes into Files → Installers, its hash checked when the site publishes one, and only the newest few of each are kept (per source). Add a source for anything else: Microsoft's .NET feed, a GitHub release (asset name pattern), a fixed link, a page that links the newest version (a pattern with the link and version), or a Chocolatey package.
- **Applications from Catalog → Updates.** A catalog application's Windows installer can name a Catalog → Updates source (`source: update`, `update_source: <name>`): deploys use the newest copy downloaded, and fall back to the application's Chocolatey package until one is there. An application can also be an npm package (`npm: <package>`), installed for every user with Node.js and updated weekly (Claude Code is one).
- **Lab mail relay.** Settings → Lab mail relay lets the environments' mail servers (catalog service Mail server, Mailpit) forward chosen recipients (addresses or `@domain`) through an SMTP account you give it; off, they keep everything. Use an account of its own: owners have root on their VMs and can read what's there. Redeploy environments with a mail server after a change.
- **Free-space alert.** Settings → Monitoring → **Free space below (%)** (default 20) raises an alert when any host's filesystem has less free space; a host's settings can set its own.
- **Settings → Secret stores → Default store** (the stack's OpenBao once `stores.sh setup` has connected it) decides where an environment's first secrets go (inventories, credential sheets, rotated passwords); later ones join the store that environment's secrets are in, so move an environment's secrets together. Existing ones stay until you move them.
- **People in OpenBao.** With `LABAPE_OPENBAO_PEOPLE=yes` and `LABAPE_OPENBAO_BIND=0.0.0.0` in `container/.env`, `stores/stores.sh setup` turns on OpenBao's web UI at `https://<LABAPE_HOSTNAME>:8200/ui` with sign-in through the stack's Authentik (it makes the client there), and owners use their environment's folder themselves (UI or `bao login -method=oidc`); admins read every environment's. Every 5 minutes LABaPe brings OpenBao's owners in line with its own (or **Sync now**: `POST /api/admin/openbao-people/sync`); new or removed environments make the update agent rewrite the policies from `stores.sh`'s template. Only environments whose secrets are in OpenBao take part, so move an environment's secrets there first. Without the Authentik installed with LABaPe, make an OIDC client yourself and set `LABAPE_OPENBAO_OIDC_CLIENT_ID` (its secret in `stores/secrets/openbao_oidc_secret`, and `LABAPE_OPENBAO_OIDC_ISSUER` if it's another issuer).
- **VMs in OpenBao.** With people access on, OpenBao also listens on port 8210 for VMs, which sign in with their platform certificate (`auth/cert`); each VM's policy (its own folder and the shared secrets that list it) and sign-in role are written by `stores.sh vm-policies` when the update agent asks. Keep 8210 reachable from the lab networks.
- **In OpenBao** each environment has a folder: `env/<environment>/accounts/` (what LABaPe generates), `env/<environment>/user/` and `env/<environment>/vm/<vm>/` (what owners store on the environment's page). Secrets stored before this layout move there the next time they're read.
- The stores' own logins, elevated accounts and one-time job tokens always stay in the built-in store, since LABaPe needs them to reach the others.

Once a secret is in an external store, LABaPe reads it from there every time it's used. If the store is down, whatever needs that secret fails with the store's error, such as a deploy that needs the environment's inventory. LABaPe never falls back to an old copy.

**The stores in the stack.** Every install with the web interface runs its own **OpenBao** and **Conjur** (installer step `Stack.Stores`, or `container/stores/stores.sh setup`; `stores.sh status` shows their state). Settings → Secret stores comes filled in. The **platform store** (OpenBao by default; `LABAPE_PLATFORM_STORE=openbao|conjur` in `container/.env`, installer answer `PlatformStore`) holds LABaPe's own secrets: the engine's vault password, vault and VM key (the command line reads them from there too) and the stack's bootstrap secrets (master key, database password, CA password, OIDC client secret). To move them between OpenBao and Conjur, change the setting and run `stores.sh setup` again.
- At boot, a user service unseals OpenBao, writes the bootstrap secrets into a memory-only folder (`LABAPE_SECRETS_DIR`, under `/run/user/<uid>`) and starts the stack. Nothing is left on disk.
- **Secret zero** is `container/stores/secrets/`: what opens the stores (OpenBao's unseal key and root token, Conjur's data key, the agent's login). Back it up somewhere safe; it's in every full stack backup ([§13](#13-backups-and-rebuilding-a-stack)).
- After the move, the old `container/secrets/` and `container/engine-secrets/` folders (and the command line's `~/.labape-vault-pass` and `~/.ssh/labape_bootstrap`) are old copies: back them up and delete them. `labape engine-secrets status` and `check` confirm the store's copies work.
- To set a bootstrap secret by hand (such as a new OIDC client secret), use `container/stores/set-secret.sh NAME` with the value on stdin.

**Rotated by the store.** If OpenBao/Vault or Conjur rotates a secret itself, tick **The secret store rotates it** on the secret's page. LABaPe then never changes it and reads the current value each time. This only works for a secret that's in an external store.

The secrets the update agent keeps in files on the host (master key, PostgreSQL and CA passwords, engine vault password, VM bootstrap key), the data key and the worker key stay where they are for now.

Lab environments' Windows passwords rotate per environment, from each environment's page ([Web-Interface-Guide.md §4](Web-Interface-Guide.md#4-working-with-an-environment)).

## 9. Updating LABaPe

Admins see every host and environment on the dashboard, plus a platform panel:
- **Sign-in**: each provider's health and last successful sign-in, break-glass state and the directory token;
- **Secrets and certificates**: the built-in store's self-test, each connected external store's check, the engine vault, the platform CA's health and its root;
- **Notifications**: SMTP and syslog settings and their last result;
- the **installed version**, the database layout, and whether a newer release is published (checked daily).

**Check now** opens the update dialog:
- the newest **release**, and a newer **pre-release** (a preview) if there is one, each with a link to its notes;
- **Roll back to <previous version>**, while the previous version is on standby.

**Continue** hands the choice to the update agent on the host. The agent turns maintenance mode on, waits for running jobs, backs up the database, keeps the current version on standby, then checks out, builds and starts the new version and runs health checks for about 5 minutes. If they pass, maintenance turns off; if they fail, it rolls back on its own. The dialog shows its progress live.

- **Signed releases only.** The agent applies a release only if its tag is signed by a key in `~/.config/labape/allowed_signers` on the host (copied from `deploy/allowed_signers`). Until a key is listed there, every update is refused.
- **Rollback** returns to the previous version only, restoring the pre-update backup when the database layout changed. After a rollback nothing is on standby.
- **Further back**: on the host, `deploy/labape-rollback.sh --to vX.Y.Z [--restore-backup FILE]`.
- **No agent installed**: the dialog shows the commands to run by hand instead. The installer sets the agent up (step `Stack.UpdateAgent`); see [deploy/README.md § Updates](../deploy/README.md#updates).
- **Sign-in over HTTPS (from v0.4.0).** Authentik installed with LABaPe is now served by the stack's web server on port **9445**, with the web interface's certificate, and LABaPe signs in through it. A new install does this by itself. On a stack installed before, after the update, as the stack's service user in the `container/` folder of the LABaPe checkout: set `LABAPE_OIDC_ISSUER=https://<the web interface's name>:9445/application/o/labape/` in `.env`, then run `docker compose up -d caddy` and `docker compose up -d --force-recreate labape-api labape-worker`. Port 9445 must be open to browsers. Linked stacks sign in to each other there too.
- **Every name of the stack (same release).** With the platform CA, the stack answers on its web name, the host's short name, its FQDN and its address with one certificate carrying all of them. A new install does it (step `Stack.WebTls`); on an existing stack, as the service user in the checkout's `container/`: `bash caddy/web-tls.sh setup`. `LABAPE_ALT_NAMES` in `.env` lists the extra names; after changing it, `bash caddy/web-tls.sh renew`.
- **Sign-on between linked stacks (same release).** After the update, as the service user: reinstall the update agent (`bash deploy/update-agent/install.sh` in the checkout; an update doesn't replace the running agent), then in `tools/authentik-test/` run `bash sso.sh --bundle-only` and `docker compose up -d`. If `tools/authentik-test/.env` belongs to root (an install run as root), give it to the service user first (`sudo chown <service user>: .env`); otherwise the agent can't manage Authentik and full-stack backups can't read it.

## 10. Audit log and user errors

- **Audit** lists who did what and when: sign-ins, every change, every credential view and copy, every rotation. Filter by user and by action prefix (for example `environment.` or `auth.`). **Export JSON** downloads what the filter shows; the export is itself audited.
- **Errors** lists requests LABaPe refused and forms the browser blocked, with the rule that failed, so you can see what users are struggling with. Errors are kept 90 days by default.

## 11. Commands inside the containers

Run these with `docker compose exec labape-api labape <command>` in the `container/` folder (or `podman compose exec`):

| Command | What it does |
|---|---|
| `breakglass enable` / `revoke` | Break-glass access ([§2](#2-first-sign-in-and-break-glass-access)) |
| `auth status`, `auth disable-provider <name>`, `auth enable-provider <name>` | Sign-in providers' health; switch one off or on |
| `maintenance on` / `off` / `status` | Maintenance mode from the command line |
| `backup`, `restore FILE --yes` | Back up or restore the database |
| `health [--deep]` | The checks the update agent uses |
| `db-status` | The database's schema revision against the code's |
| `worker-key` | The worker's public SSH key for remote KVM hosts |
| `engine-secrets status` / `check` | Where the engine's vault password, vault and VM key are, and whether a job's copies from the store work |
| `backup-key`, `seal --key-file F [--open]` | A new backup key; encrypt (or decrypt) stdin to stdout with one, as the full stack backup does ([§13](#13-backups-and-rebuilding-a-stack)) |
| `ca-sign [--hours N] [--with-root]` | Signs a certificate request from stdin with the platform CA and prints the chain (default 30 days, at most 90), for services on the host itself. Audited. |

## 12. Monitoring and alerts

LABaPe watches its hosts and itself, and turns problems into **alerts**: on the dashboard (admins and auditors), to syslog, and by email. Each alert records when it started and cleared.

What's watched:
- every KVM and stack host with SSH access set up: failed disks and wear, temperature, nearly full filesystems, failed services, pending reboots;
- stack hosts, additionally: LABaPe's containers (stopped, unhealthy, restarted) and volumes;
- this stack: the newest backups, its certificates (web, CA, syslog collector) and its databases;
- linked stacks ([§14](#14-linked-stacks)) and stacks registered as stack hosts: whether they answer.

**Settings → Monitoring**: the email recipients (it uses Email (SMTP)), whether alerts go to syslog, and the thresholds (disk 85 %, temperature 80 °C, certificates with 7 days left, no backup for 26 hours, a peer missing 2 checks in a row). Each stack host's own record needs **Set up access** before its containers can be read.

**Handling alerts** (dashboard → Alerts):
- **Ack** marks an alert as seen; it changes nothing else and ends with the alert.
- **Silence** stops syslog and email for that condition for 1 hour to 30 days, with a reason; the alert is still shown. **End** on the silence list stops it early. If the condition is still there when the silence ends, it's announced then. The all-clear is always sent once the start was.
- In **maintenance mode** no new alerts are sent; they go out when it ends if they're still open.
- If alert emails can't be sent, that's an alert of its own (to syslog), cleared when the mail server works again.

## 13. Backups and rebuilding a stack

**Full stack backups** (Settings → Full stack backups) put the whole stack into one encrypted archive: every database (LABaPe, Authentik, the syslog collector, Conjur), OpenBao, the platform CA, the configuration and the bootstrap secrets. Session recordings and syslog messages are options. The host's update agent takes one daily (03:00 UTC by default) and on **Back up now**; nothing stops while it runs. Archives older than the retention (14 days) are removed; the newest always stays.

- **The backup key.** Archives are encrypted with a key kept on the stack host (`~/.config/labape/backup.key` of the service user), never in an archive. Without it no archive opens. Settings shows the command to print it until you record that a copy is kept elsewhere (**I saved it**): put it in your password manager.
- **The check.** Each archive is opened again after it's written, every file is compared with its manifest, and the LABaPe and Authentik databases are test-loaded. The list shows the result; a failed check is an alert.
- **Copies on linked stacks.** With **Copy to linked stacks** on, the newest archive is sent to every linked stack, which keeps it (still encrypted) on its primary file host. Settings lists what each link got and the copies kept here for others.
- **Database backups** (LABaPe's database only) still run daily and before every update.

**Rebuilding a stack** on a new machine, from an archive and its key:

```bash
sudo deploy/install-labape.py --from-backup /path/labape-stack-....tar.sealed --backup-key-file /path/backup.key
```

It installs with the answers saved in the archive, then puts back the configuration, secrets, volumes and databases, so the new machine is the old stack: same web address, CA, secrets, Authentik users and data. Use a checkout at least as new as the backup. Override answers that named the old machine, such as `--set SyslogBind=0.0.0.0`. Afterwards point DNS at the new machine and rebuild templates as needed.

**A restore drill** next to the real stack: add `--restore-drill`. The copy then stays in maintenance mode (no jobs or schedules against the real KVM hosts). Point the web address at the copy only on the copy (its `/etc/hosts`). On a host with a stack already installed, `sudo deploy/stack-restore.sh ARCHIVE --key-file KEY --yes` puts an archive back without reinstalling.

## 14. Linked stacks

Two or more LABaPe stacks can be **linked**: each reads the others' summary (hosts, environments, jobs, open alerts) every minute, read-only, for the dashboard's **All stacks** panel, and they keep each other's backup copies ([§13](#13-backups-and-rebuilding-a-stack)).

1. On one stack, Admin → **Linked stacks** → **Create link code**. The code works once, for an hour. Copy it and give it to the other stack's admin out of band.
2. On the other stack, **Link with a stack** and paste it. That stack checks the first one's platform CA against the code before it sends anything; then both are linked.

Each link shows whether it answers, when it last did and a one-line summary. **Check** reads it now; **Unlink** removes it on both sides (linking again takes a new code). A linked stack that stops answering is shown with its last data, marked stale after 3 minutes, and becomes an alert after Settings → Monitoring's missed checks. Each stack shows the overall view; actions are always taken on each stack itself.

**Sign-on between linked stacks.** In the **Sign-on** column, **Turn on** lets that stack's users sign in to this one with their own accounts: this stack's sign-in page then offers the other stack by name. They appear here as `<user>@<other stack>` and keep the role groups they have there, so an admin there is an admin here. Each side decides for itself: turning it on here doesn't let this stack's users into the other one. **Turn off** removes the button here and the other stack's client for it; users who came that way stay, but can't sign in that way any more. It needs Authentik installed with LABaPe and served over HTTPS on both stacks ([§9](#9-updating-labape)), and each stack's update agent, which makes the Authentik objects; the column shows "setting up", "on", or "failed" with the reason.

## 15. Quotas, leases and placement

**Quotas** (Admin → Quotas) limit what a user's environments may use together: vCPUs, memory, VM disk and the number of environments (empty: no limit). Set one for a user, a group or everyone (the default). A user's own quota wins; else the most generous of their groups' quotas, limit by limit; else the default. Admins have none. Only environments that aren't destroyed count. It's checked when an environment is created and when a destroyed one is deployed again.

**Environment leases** (Settings → Environment leases, off by default because the end of a lease can destroy VMs): new environments get the default lease (30 days); existing ones have none until someone sets one. Owners get reminder emails before the end (7 and 1 days by default). At the end the VMs are shut down; after the grace period (7 days) the environment is destroyed, or kept shut down if you choose that. Owners extend up to the longest lease allowed from today; on an environment's page admins can also set **No expiry**.

**Placement.** On the new-environment form, **Suggest best host** ranks the KVM hosts for the host groups entered (memory and CPU in use with the environment added, running jobs, templates already there) and shows why a host doesn't fit. Through the API, creating an environment without a host places it on the best one.

## 16. Remote access and auditors

LABaPe opens **SSH**, **RDP** and **VNC/serial consoles** in the browser through its own gateway: the credentials stay on the server. Owners and admins open sessions to an environment's VMs; admins open SSH to KVM hosts.

- **Settings → Remote access**: record every session's screen (off by default), close sessions after N idle minutes (default 30), keep recordings N days (default 90).
- **Admin → Remote sessions**: who opened what, when, and how it ended, with **Play** for recordings (each play is audited).
- The **auditor** role (group `labape-auditors`) reads the audit log, the syslog messages and the remote-session log, and plays recordings; it changes nothing and can't open sessions.
- Windows VMs allow Remote Desktop (with Network Level Authentication) from the deploy that added it on; redeploy older environments to turn it on.
- VM consoles go through the KVM host's access account, so the host needs SSH access set up.

## 17. The syslog collector

The stack runs a syslog collector: UDP and TCP 514 and TLS 6514 on the host (`LABAPE_SYSLOG_BIND` limits the address), storing every message in its own database.

- **Syslog** (top bar, admins and auditors): filters for host, sender address, facility, minimum severity, app, text and time; **Live** adds new messages as they come; **Older** pages back.
- **Settings → Syslog collector**: how long messages are kept (30 days by default), whether TLS senders need a client certificate from the platform CA, and up to 10 forwarding targets (to a SIEM: UDP, TCP or TLS, RFC 5424 or 3164, filtered by facility and severity).
- LABaPe's own audit trail goes to the collector over TLS with its client certificate (Settings → Syslog).
- KVM hosts send their logs with **Logs to the syslog collector** on their SSH access section. VMs and other machines send to the stack host's address; for TLS with client certificates, get one with `labape ca-sign`.

## 18. API tokens and metrics

**API tokens** (account menu → API tokens): every user makes **personal** tokens ([Web-Interface-Guide.md §11](Web-Interface-Guide.md#11-api-tokens)). Admins also make **service** tokens: exactly the roles given, acting as `svc:<name>`, not tied to anyone's groups. With **Everyone's, and service tokens** ticked, admins see and revoke every token. Only a token's hash is kept; every write made with a token is audited as `token.use`.

**Prometheus metrics** at `/api/metrics`, for auditors and admins: make a service token with the **auditor** role, then:

```yaml
scrape_configs:
  - job_name: labape
    scheme: https
    metrics_path: /api/metrics
    authorization:
      credentials_file: /etc/prometheus/labape.token
    static_configs:
      - targets: ["labape.example:443"]
```

The metrics: `labape_info{version}`, `labape_maintenance`; per KVM host (`host`, `role`) `labape_host_enabled`, `labape_host_up`, `_cpus`, `_cpu_percent`, `_memory_bytes`, `_memory_used_bytes`, `_vm_disk_bytes`, `_vm_disk_free_bytes`, `_vms_running`, `_vms_defined`, `_collected_timestamp_seconds`; `labape_environments{status}`, `labape_jobs{state}`, `labape_jobs_failed_24h`, `labape_alerts_open{level}`; per linked stack `labape_link_up` and `labape_link_last_ok_timestamp_seconds`; `labape_backup_last_timestamp_seconds{kind}` and `labape_backup_last_check_ok`. A scrape reads only what LABaPe already collects. If your Prometheus doesn't trust the platform CA, give it the root (dashboard → Download the root certificate).

