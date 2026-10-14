# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Order of work (agreed 2026-10-14)

Items 3-9 of the 2026-10-14 review plus the earlier deferred work, in the order to build them. Each
batch gets a plan section when it starts, and its line moves out of here. Questions 146-150 in
Planning_Questions.md.

| # | Batch | What | Why here |
|---|---|---|---|
| 1 | M2 Alert handling | Acknowledge and silence alerts (with an end time); no alert emails or syslog while maintenance mode is on; an alert when an alert email fails to send. Also the syslog sender keeps one TLS connection open to the collector instead of one per event (Testing_Troubleshooting-Log.md, Y2a item 3). | Small; builds on M1 while it's fresh. |
| 2 | B1 Full-stack backup | Today's backup is only LABaPe's database (`engine/maintenance.py`). Add Authentik's database, OpenBao's data, the platform CA, the bootstrap secrets, `.env`, the answer file and the syslog collector's certificates, as one encrypted archive (the key kept apart from it), with a scheduled restore check that the archive opens and the dumps load. | Nothing below is safe without it. |
| 3 | B2 Install from backup | `deploy/install.sh --from-backup FILE`: a fresh host becomes the same stack (same names, CA, secrets, Authentik users). Tested live by rebuilding the Pi's stack on a VM. Replaces Authentik high availability (too complex and fragile for a lab). | The recovery path for a dead stack host. |
| 4 | L1 Stack link | One stack issues a one-time link code; the other enters it; each then holds a client certificate from the other's platform CA. A read-only summary API (hosts, environments, jobs, open alerts, capacity) behind that certificate. A stack that stops answering shows its last data, marked stale, plus M1's peer alert. | Needed by L2 and B3. |
| 5 | L2 Overall dashboard | On every linked stack (question 148): totals across all stacks, one card per stack with its alerts; a card opens that stack's own dashboard. View-only (question 147). | |
| 6 | B3 Backups to linked stacks | Each stack sends its B1 archive over the link to every linked stack, which keeps it on its primary KVM host (question 149), with its own retention. Restoring from a copy is B2 with the file fetched from there. | |
| 7 | A1 API tokens | Personal and service tokens with roles and an expiry, for scripts and CI; created, listed and revoked in the UI; every use audited. | |
| 8 | A2 Metrics export | A Prometheus `/metrics` endpoint (hosts, environments, jobs, alerts), read with an A1 token. | Uses A1. |
| 9 | V1 VM snapshots | Take, revert and delete from the VM page; optionally automatic before risky jobs; shown on the host's disk use. | |
| 10 | E1 Environment leases | An expiry per environment, reminder emails to the owner, then shut down and, after a grace period, destroy; owners can extend. | |
| 11 | E2 Quotas | Limits per user and group: vCPUs, RAM, disk and the number of environments, checked when a job is queued. | Pairs with E1. |
| 12 | E3 More accounts per environment | Local and domain accounts beyond the built-in admins, created at deploy, on the credentials sheet, rotatable (question 113). | |
| 13 | P1 Placement across hosts | One environment spread over several KVM hosts, best-fit placement (§15), and automatic ISO and template copies between hosts. | |
| 14 | P2 Live migration | Move a running VM and its storage between hosts (decision 23: offline first, live later). | Needs P1. |
| 15 | F1 Sign-on between stacks | Depends on question 146. | |
| 16 | Kerberos sign-on, logo | Question 29 and question 68. | |
| 17 | Hyper-V parity | Questions 11-14; the order inside is in Reference_Backend-Parity.md (interface parity and `deploy.sh`, the boot-order fix, Windows, the Debian family, templates, the ownership guard); physical host or the nested `hvhost1` VM decided when it starts. | |
| 18 | User docs | The user-docs backlog (its own list, above), then the published `.docx` guides (question 24). | |

**Release v0.3.0:** when the user says so; proposed after batch 8 (question 150). The release key
is in place (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.
