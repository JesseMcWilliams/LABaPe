# Plan: syslog in the stack, the installer's answer file, and the primary file host

> **Stage: Planning.** Y1, Y2a, Y2b and Y3a built 2026-10-13 (Planning_Web-Interface-Design.md §29.1-§29.4); Y3b (the stack apart from the primary) next. From the user's review of 2026-10-13. Questions 121-141 in
> [Planning_Questions.md](Planning_Questions.md) are answered (§0). Building goes batch by batch,
> Y1 first; each batch ships on its own and is recorded as built in
> [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) (a new §29 subsection per batch).

## 0. Decisions (questions 121-141, answered 2026-10-13)

| Q | Decision | Batch |
|---|---|---|
| 121 | A syslog collector container in the stack, with its own PostgreSQL container as the backend; LABaPe's audit trail goes there from the first start. The catalog service stays for collectors inside an environment | Y1 |
| 122 | Listeners UDP 514, TCP 514, TLS 6514; client certificates optional but **required by default** | Y1 |
| 123 | Optional forwarding to a SIEM; messages kept 30 days by default (changed from 90 on 2026-10-13), a setting | Y1 |
| 124 | KVM-only hosts send their own logs to the stack collector: optional, default yes | Y1, Y2 |
| 125 | `syslog1` (environment 11) stays for testing; the user deletes it later | — |
| 126 | Web-driven KVM installs skip the CLI configuration steps and their questions | Y2 |
| 127 | Networks for web-driven installs come from Admin → Networks | Y2 |
| 128 | Add RHEL-family KVM hosts (Rocky, Alma) | Y2 |
| 129 | TemplatesToBuild isn't asked interactively; answer-file option for CLI installs | Y2 |
| 130 | Sign-in: if Authentik is installed with the deployment, use it; otherwise a throwaway Authentik for the first sign-in | Y2 |
| 131 | The primary KVM host holds every file LABaPe uses (ISOs, uploads) unless a share is used | Y3 |
| 132 | Other hosts get files from the primary over SSH when a job needs them, cached there | Y3 |
| 133 | File shares off by default; an option on the KVM host's page | Y3 |
| 134 | When the stack host isn't the primary, the Files page works through the primary over SSH | Y3 |
| 135 | Jobs needing files wait for the primary, up to a configurable time, then fail; a host that already has the file (same checksum) doesn't wait | Y3 |
| 136 | The syslog database is its own container | Y1 |
| 137 | A log viewer: a **Syslog** tab | Y1 |
| 138 | 30 days by default (changed from 90 on 2026-10-13), configurable; no plain log files for syslog messages | Y1 |
| 139 | Forwarding supports every transport (UDP, TCP, TLS, mTLS; RFC 5424 and 3164), configured in the Admin area | Y1 |
| 140 | The installer offers each service, default yes | Y2 |
| 141 | The answer file never holds secrets; an update reuses it and asks only new questions | Y2 |

Also decided: the web stack must be able to run apart from the primary (e.g. on a Raspberry Pi driving
KVM on a larger machine); test that with a VM deployed as the separate stack host (Y3). kvm1 has
nothing on 514/6514, so the collector binds there.

## Y1. Syslog collector in the stack (built: §29.1)

- **Containers.** `labape-syslog` (rsyslog with `imudp`, `imtcp`, TLS through `ossl`, and `ompgsql`) and
  `labape-syslog-db` (`postgres:17-alpine`, its own volume and password secret). The collector's
  database role can only insert; LABaPe's role reads and deletes (viewer, retention).
- **Listeners** (question 122): 514/udp, 514/tcp and 6514/tcp, published on the host's lab-facing
  address (installer answer `SyslogBind`, default the host's primary address). The TLS server certificate
  comes from the platform CA and is renewed like the others; client certificates are required by default
  (a setting turns that off). LABaPe's own audit sender gets a client certificate from the same CA and
  reaches the collector on the stack network.
- **Storage.** One table (`syslog_messages`: received and reported time, sender address, host name,
  facility, severity, app name, proc id, msg id, structured data, message), indexed for the viewer's
  filters. Retention (question 138): a daily purge in the worker, 30 days by default, a setting.
- **Forwarding** (questions 123 and 139): Admin → Settings → Syslog lists forward targets, each with host,
  port, transport (UDP, TCP, TLS, TLS with a client certificate), format (RFC 5424 or 3164) and a filter
  (everything, chosen facilities, or a minimum severity). LABaPe writes the rsyslog forwarding file to a
  volume the collector shares; the collector's entrypoint restarts rsyslogd when that file changes
  (rsyslog doesn't reload its configuration on HUP). Queued, disk-assisted actions, so a SIEM that's down
  doesn't block the collector.
- **Syslog tab** (question 137): admins and auditors. Filters for time range, host, sender address,
  facility, minimum severity, app name and text; newest first with paging; a live mode that polls every
  few seconds. Opening it isn't audited (it's read-only), but changes to its settings are.
- **The audit trail** points at the stack collector by default for new installs; this lab switches from
  `syslog1` to the stack collector, and `syslog1` stays as a test target (question 125).
- **KVM-only hosts** (question 124): an answer `ForwardHostLogs` (default yes) makes the kvm profile
  install rsyslog's forwarding to the stack collector over TLS with a client certificate from the
  platform CA (the existing certificate API).
- **Installer:** a `Stack.Syslog` step (database, roles, certificates, settings).

## Y2. The installer: questions first, a saved answer file, chosen services (Y2a built: §29.2; RHEL family is Y2b)

- **First run** asks every question that applies (by profile and chosen services) before any step runs,
  shows a summary to confirm, and saves the answers to `deploy/state/answers.<instance>.json` (0600, no
  secrets; question 141). Later runs and updates reuse the file without prompting and ask only questions
  added since (non-interactive runs take their defaults), appending them.
- **Services** (question 140), each default yes: web interface, OpenBao, Conjur, syslog collector with its
  database, remote access (guacd), update agent, CLI toolchain, Authentik. At least one secret store is
  required with the web interface (LABaPe's own secrets live there): as built, OpenBao is always installed and Conjur
  is the optional one, since the update agent and the boot-time unseal depend on OpenBao. Services not chosen are left out
  through compose profiles.
- **Sign-in** (question 130): Authentik chosen → the stack's Authentik is the sign-in provider; not
  chosen → an existing provider if one is given, otherwise a throwaway Authentik for the first sign-in.
- **Web-driven KVM installs** (questions 126, 127, 129): the Prepare form picks networks from Admin →
  Networks; the CLI configuration steps run only when a `Cli` answer asks for a usable CLI checkout;
  templates aren't asked.
- **RHEL family** (question 128): `Prereq.Os` accepts Rocky and Alma; a package map for dnf; libvirt's
  modular daemons (`virtqemud`); SELinux file contexts for the storage folders (`virt_image_t`);
  firewalld openings for the syslog and VNC ports where needed. Tested on a Rocky 9 VM with nested KVM.

## Y3. The primary file host (Y3a built: §29.4; the stack apart from the primary is Y3b)

- **Primary.** One KVM host is marked **Primary** (Admin → KVM hosts, and an installer answer); for this
  lab, `kvm1`. It holds every file LABaPe uses (question 131), in the Files locations.
- **Stack host apart from the primary** (question 134): the Files page lists, scans, checksums, uploads and
  downloads through the primary over SSH as its service user (uploads streamed, never staged in full on
  the stack host). When the stack runs on the primary, it works on the local paths as today.
- **Other hosts** (question 132): a job that needs a file copies it from the primary over SSH (streamed
  through the worker), keeps it in that host's cache and checks its SHA-256; the current `sync_iso`
  becomes this general mechanism.
- **Shares** (question 133): off by default; a KVM host's page can say "files come from a share" (NFS or
  SMB, mount point and, for SMB, credentials in the secret store); that host then reads files there
  instead of copying from the primary. *(As built, §29.4: the admin mounts the share at the same paths;
  LABaPe doesn't mount it or keep its credentials.)*
- **Primary unavailable** (question 135): a job that needs a file the host doesn't have waits for the
  primary up to a setting (default 30 minutes), then fails; a host that already has the file with the
  right checksum doesn't wait.
- **Test setup:** a VM deployed as a separate stack host (web profile) driving `kvm1` (primary) and
  `garagepc`.

## Open issues

1. **ARM stack hosts (Raspberry Pi).** The image would need an arm64 build. Most parts exist for arm64
   (Python, PostgreSQL, rsyslog, OpenTofu, Packer, guacd needs checking); Conjur's official image is
   believed to be amd64 only, so on ARM the platform store would be OpenBao. To verify before promising
   ARM support.
2. **Client certificates required by default** means lab VMs sending over TLS need a client certificate;
   VMs can already get platform certificates (the certificate API), so the forwarding role can request
   one. UDP and plain TCP stay open for senders without one.
3. **Migration on existing installs:** the syslog containers and settings arrive with an update; the
   update flow must create the new secrets and database before the containers start.
