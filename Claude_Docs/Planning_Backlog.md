# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Order of work (agreed 2026-10-14)

Items 3-9 of the 2026-10-14 review plus the earlier deferred work, in the order to build them. Each
batch gets a plan section when it starts, and its line moves out of here. Done: 1 M2 (design §31), 2 B1 (§32), 3 B2 (§33), 4 L1 (§34), 5 L2 (§35), 6 B3 (§36), 7 A1 (§37), 8 A2 (§38), 9 V1 (§39), 10 E1 (§40), 11 E2 (§41), 12 E3 (§42), 13 P1a (§43), 13b P1b (§45), 14 P2 (§46), 19 R1 (`tools/helpers/release.sh`), 15 F1 (§47), the logo of 16 (§44). Release v0.3.0 was cut after 8 (question 150). Questions 146-150 in
Planning_Questions.md.

| # | Batch | What | Why here |
|---|---|---|---|
| 16 | Kerberos sign-on | Built 2026-10-16 (design §58); still to do: a browser test from `l2win1`, an installer step or Settings page for the domain side. | Built (manual recipe) |
| 17 | Hyper-V parity | **Dropped (2026-10-16, question 175):** Hyper-V support will be removed, so the gaps are not closed. Questions 11-14 and 155 are superseded; Reference_Backend-Parity.md stays as history. | Dropped |
| 18 | User docs | The user-docs backlog is done (2026-10-14: Web-Interface-Guide.md and Web-Interface-Admin.md cover everything to §44). The published `.docx` guides wait (question 24: later); `release.sh --docx` makes them from `User_Docs/` with pandoc when they start. | |
| 19 | AI console access and MCP | Planned 2026-10-17 (batches X1-X4: token scope and `console` role, run a command, interactive text sessions, MCP server); questions 176-183 answered. | [Planning_AI-Console-And-MCP.md](Planning_AI-Console-And-MCP.md) |

**Next round (2026-10-14):** lists, secrets and settings, in [Planning_Lists-Secrets-Settings.md](Planning_Lists-Secrets-Settings.md) (batches L1, L2, N1, S1, S2, S3). v0.4.0 not yet (the user, 2026-10-14).

**Release v0.3.0:** when the user says so; proposed after batch 8 (question 150). The release key
is in place (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.

## Operations round (added 2026-10-15; the user agreed all of it, in this order)

| # | Item | What |
|---|---|---|
| P1 | Audit log and User errors paging | The existing pager (`Paging.tsx`, the user's rows-per-page): `offset`, `X-Total-Count` (audit) and `total` (errors). |
| H2 | KVM host: Update and Reboot | On the KVM dashboard, each with a confirmation. Update = `apt-get update` + `upgrade` (not `dist-upgrade`), the pending packages in the confirmation, output in a job log. Reboot also on the primary file host and the stack host; the confirmation warns about running VMs and jobs. **A reboot issued from the OS waits for a running job to finish** (the user, 2026-10-15): a systemd inhibitor lock (`block`) held while LABaPe runs a job on the host. |
| S4 | All stack logs to the stack syslog | Every stack container (not the collector's own) through Docker's syslog log driver to the collector, and the **host's own logs** (journald/rsyslog) too. |
| C1 | Container admin links on the stack detail page | A link per container with a web UI (Mailpit, OpenBao, Authentik); a popup says how to sign in; the page opens in a new tab. Containers without a UI (Postgres, guacd, syslog, CA) show none. |
| D1 | Drive Pools tab | Between Drives and Volumes/NICs, for KVM hosts and stack hosts, shown only when mdadm, ZFS or LVM is found. First version: pool list (type, level, state, size, used, mounts), pool detail (members and roles, rebuild/resync progress, UUID), Monitoring alerts (degraded, failed, rebuilding), and the safe actions with confirmation (scrub/check, mark faulty and remove, add a spare, stop/restart a resync). Create, destroy, grow and re-level: later. A state-change history: later. |
