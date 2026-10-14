# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Order of work (agreed 2026-10-14)

Items 3-9 of the 2026-10-14 review plus the earlier deferred work, in the order to build them. Each
batch gets a plan section when it starts, and its line moves out of here. Done: 1 M2 (design §31), 2 B1 (§32), 3 B2 (§33), 4 L1 (§34), 5 L2 (§35), 6 B3 (§36), 7 A1 (§37), 8 A2 (§38), 9 V1 (§39), 10 E1 (§40), 11 E2 (§41), 12 E3 (§42), 13 P1a (§43), 13b P1b (§45), 14 P2 (§46), the logo of 16 (§44). Release v0.3.0 was cut after 8 (question 150). Questions 146-150 in
Planning_Questions.md.

| # | Batch | What | Why here |
|---|---|---|---|
| 15 | F1 Sign-on between stacks | Question 146 chose OIDC sources; question 153 (a): each Authentik served with a platform-CA certificate, linked stacks trust each other's CA, the agent creates the provider, application and source over the link. | |
| 16 | Kerberos sign-on | Question 29; question 154: later. The logo (question 68) is done (§44). | Later |
| 17 | Hyper-V parity | Later (question 155). Questions 11-14; the order inside is in Reference_Backend-Parity.md (interface parity and `deploy.sh`, the boot-order fix, Windows, the Debian family, templates, the ownership guard); physical host or the nested `hvhost1` VM decided when it starts. | |
| 18 | User docs | The user-docs backlog is done (2026-10-14: Web-Interface-Guide.md and Web-Interface-Admin.md cover everything to §44). The published `.docx` guides wait (question 24: later), and will be made by the release script (R1). | |
| 19 | R1 Release script | One script cuts a release: the version in the CHANGELOG (`[Unreleased]` becomes the new version with its date), the docs' version references, the `.docx` guides from `User_Docs/` (when question 24's guides start), the signed tag and the GitHub release. Question 24 (2026-10-14). | After F1 |

**Release v0.3.0:** when the user says so; proposed after batch 8 (question 150). The release key
is in place (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.
