# Access, mail, updates and files round

> **Stage: Planning.** The user's list of 2026-10-14 (after V1), in batches. Each batch ships on
> its own (branch, tests, live check, PR, merge). Decisions: questions 167-173 (answered 2026-10-14) in
> [Planning_Questions.md](Planning_Questions.md); built batches get a section in
> [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md). V2 (VMs fetching their own
> secrets, [Planning_Lists-Secrets-Settings.md](Planning_Lists-Secrets-Settings.md)) waits for the
> user's go and isn't part of this round.

## The requests

| # | Request | Batch |
|---|---|---|
| Q1 | Environment → Secrets: paging and search, like the other lists | E1 |
| Q2 | From a secret on that list, connect in the browser as that account: RDP or SSH where the account can sign in, choosing the target among the environment's VMs | E2 |
| Q3 | A local Windows account: RDP to its VM only, no SSH. A domain account: RDP to the Windows VMs, SSH to the Linux VMs | E2 |
| Q4 | A local Linux account: SSH, and VNC where the VM runs a VNC server; with VNC picked, only Linux VMs with VNC enabled are offered | E2, E3 |
| Q5 | A mail server with a web interface that every VM can send to, forwarding outside when wanted, for testing SMTP in the lab | M1 |
| Q6 | Catalog → Updates: download sites checked weekly, new versions downloaded into the installers repository, keeping a set number of versions | U1 |
| Q7 | New packages: .NET (runtime), ASP.NET Core Hosting Bundle, .NET SDK, Chrome, Claude Code, KeePassXC, PuTTY | P1 |
| Q8 | Each KVM host has a path for files, next to its VM folder: `/data/VMs/LABaPe` → `/data/Files/Installers` and `/data/Files/Other` | F1 |
| Q9 | Monitoring: whatever tools it needs (SMART, sensors, Ethernet) installed by setup | H1 |
| Q10 | The files path shown on the KVM host's settings; a configurable alert for drive space, default 20% | F1 |
| Q11 | Updates: admins can add new sources | U1 |
| Q12 | Claude Code: an option, not installed by default; kept up to date once installed | P1 |

## The batches

| Batch | What | Waits on |
|---|---|---|
| **H1** (built, design §50.1) | Monitoring tools on every host LABaPe sets up: `smartmontools`, `lm-sensors` (with `sensors-detect --auto`, so the sensors are found without asking), `ethtool`. In the installer's prerequisites (Debian/Ubuntu, RHEL family, the Pi) and in a KVM or stack host's access-account setup, so existing hosts get them at their next **Prepare**. The host page's "isn't installed" warnings then only show where installing failed. Today the installer has the first two and access setup installs only those. | Question 173 |
| **E1** (built, design §50.2) | Paging and search on the environment's Secrets panel (the shared `Paging.tsx`; search over name, VM and place; filters for group, VM and store). | — |
| **E2** (built, design §50.4) | **Connect** on a secret that is an account (LABaPe's admins, the environment's own accounts, and owner secrets marked as an account with a user name): a menu of what that account can sign in to, built from the environment (question 167): local Windows account → RDP to its VM; domain account → RDP to the Windows VMs, SSH to the domain-joined Linux VMs (`linux_domain_join`); local Linux account → SSH to its VMs, VNC to its VMs that run a VNC server. The session is the existing guacd tunnel (design §28.5), with that account's password filled in on the server, never sent to the browser; recorded and audited like the others. | Question 167; E1 |
| **E3** (built, design §50.5) | VNC on Linux VMs for an account (question 168): a catalog option on Linux roles that starts a VNC server for desktop sessions, and LABaPe recording which VMs have it, so the Connect menu only offers VNC there. | Question 168; E2 |
| **M1** (built, design §50.6) | A lab mail server (question 169). Suggestion: **Mailpit**, a single small container: it accepts SMTP from anyone allowed, keeps every message, and shows them in a web UI and an API, so tests can check what was sent. It can relay: with relay on, messages to chosen recipients or domains are forwarded through a real SMTP server (here: the stack's Settings → Email), everything else is only caught. VMs get its address as their smarthost (Linux: Postfix `relayhost`; Windows: the SMTP setting the app uses). Alternatives: MailHog (unmaintained), Postfix + Roundcube (a real mail system, much more to run). | Question 169 |
| **U1** (built, design §50.7) | Catalog → **Updates** (question 170): a list of sources, each a package, a source type and its settings: the **.NET release feed** (Microsoft's `releases.json` for a channel: runtime, hosting bundle, SDK, with their SHA-512), **GitHub releases** (KeePassXC: newest release's asset by name pattern), a **"latest" URL** (Chrome's enterprise MSI, PuTTY's `latest/` folder: a new file when its version or hash changes), and the **Chocolatey feed** (a package's newest `.nupkg`, internalized). A weekly job (day and time in Settings) checks each, downloads what's new into Files → Installers (OS and version set, hash checked when the source publishes one), and keeps the newest N (default 3, per source). The page lists each source with its last check, newest version and history, and **Check now**. | Question 170 |
| **P1** | The new packages in the catalog (question 171): Windows from the installers repository when U1 has them there, else Chocolatey; Linux from the vendors' repositories (Microsoft's for .NET, Google's for Chrome, EPEL or the distribution for KeePassXC and PuTTY); Claude Code with Anthropic's native installer. | Question 171; U1 for offline Windows installs |
| **F1** (built, design §50.3) | Files on each KVM host (question 172): a **Files path** on the host, by default beside its VM folder (`/data/VMs/LABaPe` → `/data/Files`), with `ISOs`, `Installers` and `Other` under it; the file distribution (design §43) puts each host's copies there instead of the stack-wide roots. Existing hosts keep their current paths until an admin changes them on the host page, which moves the files with a job. | Question 172 |

Order (the user's go of 2026-10-14, without waiting): H1, E1, F1, E2, E3, M1, U1, P1.
