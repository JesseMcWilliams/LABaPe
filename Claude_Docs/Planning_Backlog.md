# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Order of work (agreed 2026-10-14)

Items 3-9 of the 2026-10-14 review plus the earlier deferred work, in the order to build them. Each
batch gets a plan section when it starts, and its line moves out of here. Done: 1 M2 (design §31), 2 B1 (§32), 3 B2 (§33), 4 L1 (§34), 5 L2 (§35), 6 B3 (§36), 7 A1 (§37), 8 A2 (§38), 9 V1 (§39), 10 E1 (§40), 11 E2 (§41). Release v0.3.0 was cut after 8 (question 150). Questions 146-150 in
Planning_Questions.md.

| # | Batch | What | Why here |
|---|---|---|---|
| 12 | E3 More accounts per environment | Local and domain accounts beyond the built-in admins, created at deploy, on the credentials sheet, rotatable (question 113). | |
| 13 | P1 Placement across hosts | One environment spread over several KVM hosts, best-fit placement (§15), and automatic ISO and template copies between hosts. | |
| 14 | P2 Live migration | Move a running VM and its storage between hosts (decision 23: offline first, live later). | Needs P1. |
| 15 | F1 Sign-on between stacks | Depends on question 146. | |
| 16 | Kerberos sign-on, logo | Question 29 and question 68. | |
| 17 | Hyper-V parity | Questions 11-14; the order inside is in Reference_Backend-Parity.md (interface parity and `deploy.sh`, the boot-order fix, Windows, the Debian family, templates, the ownership guard); physical host or the nested `hvhost1` VM decided when it starts. | |
| 18 | User docs | The user-docs backlog (its own list, above), then the published `.docx` guides (question 24). | |

**Release v0.3.0:** when the user says so; proposed after batch 8 (question 150). The release key
is in place (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.
