# Administering the LABaPe web interface

This guide is for LABaPe admins (the `admin` role, `labape-admins` by default). It covers getting the web interface running, then everything under Hosts, Networks, Catalog, Settings and Secrets, plus updates, maintenance and the audit trail. For what every user sees, read [Web-Interface-Guide.md](Web-Interface-Guide.md) first.

## Contents

1. [Installing](#1-installing)
2. [First sign-in and break-glass access](#2-first-sign-in-and-break-glass-access)
3. [KVM hosts](#3-kvm-hosts)
4. [Networks](#4-networks)
5. [The catalog](#5-the-catalog)
6. [Settings](#6-settings)
7. [Maintenance mode](#7-maintenance-mode)
8. [Secrets and rotation](#8-secrets-and-rotation)
9. [Updating LABaPe](#9-updating-labape)
10. [Audit log and user errors](#10-audit-log-and-user-errors)
11. [Commands inside the containers](#11-commands-inside-the-containers)

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

The definitions use the shapes in [Planning_Web-Interface-Design.md §21.4](../Claude_Docs/Planning_Web-Interface-Design.md#214-batch-d-built-services-applications-and-packages): an application is a Chocolatey, apt or dnf name and/or an installer from the Installers location; a service is Windows features and services and/or Linux packages and systemd units.

## 6. Settings

Each section saves on its own and asks you to confirm.

| Section | What it sets |
|---|---|
| Branding | The app name (up to 40 characters): top bar, browser title, sign-in page and emails |
| File locations | The folder for ISOs, installers and other files, as paths inside the containers |
| Backups | A daily database backup at a set time (UTC), the folder and how many days to keep. **Back up now** runs one as a job; the list downloads any backup. Restore with `labape restore FILE --yes` (see [§11](#11-commands-inside-the-containers)). |
| History | Days to keep job logs, user errors and the audit trail (0 keeps the audit trail forever, the default) |
| Syslog | Sends every audit event to a syslog server over UDP, TCP or TLS (RFC 5424, JSON body). **Send test message** checks it. |
| Email (SMTP) | Server, port, security (none, STARTTLS, SSL/TLS), optional sign-in, from address. **Email people when their jobs finish** turns on job emails. **Send test email** checks it. |
| Directory (group search) | An Authentik API token that can only view groups (`authentik_core.view_group`), and the Authentik URL (blank uses the sign-in issuer's address). Group fields then search Authentik. **Test search** shows what the token can see. |
| Downloads | Allow plain HTTP for Download from URL (off by default), and an HTTP(S) proxy |
| VM accounts | The Linux admin user for new environments (default `labape`). Existing environments keep theirs. |
| Maintenance mode, Maintenance window | See [§7](#7-maintenance-mode) |

Passwords and tokens entered here are kept encrypted in LABaPe's built-in secret store and never shown again.

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

**Secrets** lists every internal secret: what it is, which store holds it, its class, rotation interval, last and next rotation, and status. Open one to change its interval, turn scheduled rotation off, choose an elevated account or add notes; **Rotate now** rotates it at once. Every rotation and every change is audited.

| Secret | Rotated by | Default |
|---|---|---|
| Built-in store data key | the worker | daily |
| Worker SSH key for remote KVM hosts | the worker | daily |
| Authentik directory token | the worker, with an elevated account | weekly |
| OIDC client secret | the worker, with an elevated account | daily |
| Master key, PostgreSQL password, platform CA password | the update agent, with a restart | weekly, in the maintenance window |
| Engine vault password (and the command line's vault on the same host), VM bootstrap SSH key | the update agent, no restart | daily |
| SMTP password | you, on the mail server | reminder only |

**How a rotation works.** LABaPe makes the new value, applies it, checks it works, and only then switches. If anything fails, the old secret stays, the status shows the error (also on the dashboard and in syslog) and it retries an hour later.

**Restart-needing secrets** (master key, PostgreSQL, CA) restart the API and worker. They run at most weekly, inside the maintenance window, with maintenance mode on. A master key rotation signs everyone out.

**Elevated accounts.** Some secrets can't rotate themselves: a read-only Authentik token can't replace its own key. **Add elevated account** stores the credential of an account that can, and the secret's page names it. Without one, the Authentik directory token and the OIDC client secret show as **reminder** and aren't rotated. In Authentik, give the elevated token:
- for the directory token: `add_token` and `view_token` on that token;
- for the OIDC client secret: `view_oauth2provider` and `change_oauth2provider` on the LABaPe provider.

**OIDC client secret.** After the first rotation, LABaPe keeps the current client secret in its built-in store and uses it from the next sign-in, with no restart. The secret file the stack was installed with (`container/secrets/oidc_client_secret`) becomes a fallback. If you paste a new secret into that file later, the file takes over again.

**VM bootstrap key.** The 7 previous keys are kept and still tried, so a VM that was off during a rotation is still reached. The agent also copies the current key into the service user's `~/.ssh/labape_bootstrap` for the command line. Environments on remote KVM hosts are skipped.

**External stores** (OpenBao/Vault, Conjur) aren't available yet; the Store field offers the built-in store only. A secret marked "The secret store rotates it" is only read by LABaPe.

Lab environments' Windows passwords rotate per environment, from each environment's page ([Web-Interface-Guide.md §4](Web-Interface-Guide.md#4-working-with-an-environment)).

## 9. Updating LABaPe

Admins see every host and environment on the dashboard, plus a platform panel:
- **Sign-in**: each provider's health and last successful sign-in, break-glass state and the directory token;
- **Secrets and certificates**: the built-in store's self-test, the engine vault, the platform CA's health and its root;
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
| `ca-sign [--hours N] [--with-root]` | Signs a certificate request from stdin with the platform CA and prints the chain (default 30 days, at most 90), for services on the host itself. Audited. |
