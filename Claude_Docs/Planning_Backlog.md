# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Order of work (agreed 2026-10-14)

Items 3-9 of the 2026-10-14 review plus the earlier deferred work, in the order to build them. Each
batch gets a plan section when it starts, and its line moves out of here. Done: 1 M2 (design §31), 2 B1 (§32), 3 B2 (§33), 4 L1 (§34), 5 L2 (§35), 6 B3 (§36), 7 A1 (§37), 8 A2 (§38), 9 V1 (§39), 10 E1 (§40), 11 E2 (§41), 12 E3 (§42), 13 P1a (§43), the logo of 16 (§44). Release v0.3.0 was cut after 8 (question 150). Questions 146-150 in
Planning_Questions.md.

| # | Batch | What | Why here |
|---|---|---|---|
| 13b | P1b Spreading one environment over hosts | §15's host per group; question 151 (later, recommended: kvm1's stack has one enabled KVM host). P1a (best-fit placement, template copies) is done (§43). | |
| 14 | P2 Live migration | Move a running VM and its storage between hosts (decision 23: offline first, live later); question 152. | Needs P1b. |
| 15 | F1 Sign-on between stacks | Question 146 chose OIDC sources; waits on question 153 (each Authentik's TLS). | |
| 16 | Kerberos sign-on | Question 29; waits on question 154. The logo (question 68) is done (§44). | |
| 17 | Hyper-V parity | Waits on question 155. Questions 11-14; the order inside is in Reference_Backend-Parity.md (interface parity and `deploy.sh`, the boot-order fix, Windows, the Debian family, templates, the ownership guard); physical host or the nested `hvhost1` VM decided when it starts. | |
| 18 | User docs | The user-docs backlog is done (2026-10-14: Web-Interface-Guide.md and Web-Interface-Admin.md cover everything to §44). The published `.docx` guides wait (question 24: later). | |

**Release v0.3.0:** when the user says so; proposed after batch 8 (question 150). The release key
is in place (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.
