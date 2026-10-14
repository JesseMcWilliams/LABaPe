# Lists, secrets and settings round

> **Stage: Planning.** The user's list of 2026-10-14 (after F1), in batches. Each batch ships on
> its own (branch, tests, live check, PR, merge). Decisions are questions 158-162 in
> [Planning_Questions.md](Planning_Questions.md); built batches get a section in
> [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md).

## The requests

| # | Request | Batch |
|---|---|---|
| R1 | Stacks answer on their IP address, host name and FQDN; the certificate carries the matching SANs | N1 |
| R2 | Paging on Environments, Jobs, Files, Catalog and Syslog: 50 rows by default, the user's own choice kept in their profile | L1 |
| R3 | A CSV export asks: this page, a range, or all | L1 |
| R4 | Environments: filters and CSV export | L1 |
| R5 | Jobs: download the listed items as JSON | L1 |
| R6 | Files: each type filterable by OS (a drop-down of the OS restrictions in use) and CSV export | L2 |
| R7 | Files → Installers: the OS filter too; a file's OS is set when it's uploaded | L2 |
| R8 | Catalog: filters | L2 |
| R9 | Networks: filters | L2 |
| R10 | The stack can recover a secret that got out of sync | S1 |
| R11 | A secret that can't be recovered is flagged: Recoverable yes/no | S1 |
| R12 | Secrets in sections: stack, KVM hosts, environments | S2 |
| R13 | Environment secrets filterable by environment, store, VM, local or domain, password or key, last changed | S2 |
| R14 | Settings in sections | S3 |
| R15 | Disks and partitions hide loop devices unless asked | D1 |
| R16 | The host setup installs the sensors tools | D1 |
| R17 | People get and store their environment's secrets; the store keeps each environment apart | V1 |
| R18 | VMs fetch their secrets themselves | V2 |

## Batches, in order

| Batch | What | Waits on |
|---|---|---|
| **L1** (built, design §48.2) | A shared paged table: the API lists take `offset`/`limit` and return a `total`; the page size (50 by default; 25, 50, 100, 250 or a number the user types) is kept per user (`users.preferences`, `GET/PUT /api/me/preferences`) and applies to every paged list. Export dialog (CSV, and JSON for Jobs): this page, a range, or all, with the list's filters. Environments: filters (name, status, KVM host, owner, created) and CSV. Jobs: JSON. Syslog and Files keep their filters and gain paging. | Questions 159 (rows from-to) and 160 (Jobs JSON, no logs): answered |
| **L2** (built, design §48.1) | Files: an OS filter per type (ISOs, installers, other files), its choices the OS restrictions used in that list; CSV export. Upload asks for the file's OS (installers: required; the same targets as the catalog). Catalog filters (kind, name, status, platform). Networks filters (name, CIDR, addressing, KVM host). | — |
| **D1** (built, design §48.3) | Host details: loop devices hidden in Drives and volumes (a **Show loop devices** switch). Host setup (access-account setup and the installer's packages) installs `lm-sensors` (Debian) / `lm_sensors` (RHEL), so temperatures read without the thermal-zone fallback. | — |
| **N1** (built, design §48.4) | The web interface and Authentik (9445) answer on every name of the stack: `LABAPE_HOSTNAME` plus `LABAPE_ALT_NAMES` (the installer fills it from the host: short name, FQDN, the default route's address; editable in Settings). Sign-in still returns to the main name (the OIDC redirect URI is one address). | Question 158: one combined certificate; LABaPe requests it from the platform CA with every name as a SAN, hands it to Caddy and renews it |
| **S1** (built, design §48.5) | Secret health: each secret LABaPe holds gets a **check** (does the stored value still work: a sign-in, an API call, a TLS key match) and a **Recoverable** flag (yes: LABaPe can set a known value again without the old one, with another credential it holds; no: it can't, and losing it needs a person). A daily check, and **Check now**; when a value no longer works: try the previous values it kept (rotation history) and, for a recoverable secret, set a new one; otherwise flag it, raise an alert and say what a person must do. | Question 161: checks every quarter of the rotation period, at least daily; none for a secret rotating daily or more often; weekly for one that doesn't rotate |
| **S2** | Secrets page in sections: **Stack** (platform, links, stores, engine), **KVM hosts** (access accounts, the worker's key), **Environments** (VM local admins, domain admin, the environment's own accounts, keys). Environment secrets: filters for environment, store, VM, local or domain, password or key, last changed. | S1 |
| **V1** | Environment secrets in their own paths and policies; the environment page's secrets list with Reveal and Store; then OpenBao's UI and CLI with Authentik sign-in and per-environment groups (question 163-164, 166). | Questions 163, 164, 166 |
| **V2** | VMs read their secrets with OpenBao certificate sign-in; `labape-secret` / `Get-LabapeSecret`; OpenBao on the lab network (question 165). | Question 165; V1 |
| **S3** | Settings in sections, a list on the left like Admin: **Sign-in** (directory, break-glass, VM accounts), **Stack** (branding, monitoring, notifications, syslog, maintenance), **Updates and backups**, **Environments** (leases, quotas, snapshots, placement, templates), **Files** (file distribution, downloads), **Secrets** (rotation, stores), **Remote access**. | Question 162: answered (yes) |

## V1 and V2: people's and VMs' access to environment secrets (proposal; questions 163-166)

**Where they live.** The platform store (OpenBao by default) gets a path per environment: `env/<environment>/accounts/` (what LABaPe generates), `env/<environment>/user/` (what owners store), `env/<environment>/vm/<vm>/` (one VM's own), apart from `platform/` and `kvm/<host>/`. One OpenBao policy per environment for its owners (read all of it, write `user/`) and one per VM (read `user/` and its own `vm/<vm>/`).

**People (V1).** First through LABaPe: the environment's page lists its secrets with **Reveal** (audited, as the credentials sheet's reveal is today) and **Store a secret** / **Replace**, LABaPe's permissions deciding who may. Then, for OpenBao, its own web UI and command line: OpenBao's OIDC sign-in against the stack's Authentik, an identity group per environment whose members LABaPe keeps equal to the environment's owners, so people can use `bao kv get` and the UI with their own sign-in. Conjur's open-source edition has no web UI.

**VMs (V2).** OpenBao's certificate sign-in, trusting the platform CA: a VM signs in with the certificate it already has (`labape_cert`), and its role maps the certificate's name to that VM's policy. A helper on each VM (`labape-secret get <name>`; `Get-LabapeSecret` on Windows) reads `user/` or its own `vm/<vm>/`. OpenBao then listens on the lab network too (TLS on 8200), not only on the stack host's loopback.

## Notes from the code

- Lists today: Jobs, Audit, Errors, Sessions, Syslog and Files have URL filters (`components/Filters.tsx`, O1); Jobs and Audit cap at a `limit`, nothing pages.
- Settings.tsx is one 770-line page of panels.
- The Secrets page lists rotation policies; stored secrets (the external store catalog) are a separate component.
