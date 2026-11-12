# Plan: finish the disk work and work down the queue (next round, 2026-11-08)

> **Stage: Planning.** The user chose items 1 and 2 of the next-round list on 2026-11-08: finish the OZ disk and notification work, and work down the open queue. Questions 215-220 in [Planning_Questions.md](Planning_Questions.md) hold the choices; every recommendation is the default if there is no answer. Builds on [Planning_Disk-Health-Notifications.md](Planning_Disk-Health-Notifications.md) (S1, N1, N2, R1, C4 all built; design §76-§80).

## 1. Finish the disk and notification work

| Batch | What | Notes |
|---|---|---|
| A1 | **Container history charts.** CPU and memory per container, sampled with each 5-minute read, kept 7 days, drawn on the container's detail dialog. | Question 215. Rest of C4. |
| A2 | **Rootless Podman.** Containers of the access account's own Podman show on the Containers page, marked rootless; read-only first. | Question 216. Rest of C4. |
| A3 | **(Written: [Testing_Manual-Checks-Disk-Notifications.md](Testing_Manual-Checks-Disk-Notifications.md).) A manual-check list for what only the lab can show:** run the installer steps on OZ (`Prereq.DiagSudo`, `SmartTests`, `SmartAlerts`, `RaidDiskTimeouts`, `AlertMail` on pi1 and garagepc), read `smartctl` for the self-test log, a real webhook or ntfy rule, a heartbeat that fails on purpose, the first real **Replace a drive** on `md10`. | A results table like `Testing_Manual-Checks-v0.5.5.md`. The user runs the steps; Claude writes the list and records results. |

## 2. The open queue

| Batch | What | Notes |
|---|---|---|
| B1 | **Small review items** (project review items 8-14, 18): the unchecked tus `Upload-Length`, the public `/api/docs`, floating image tags, the root API image, files over 1,000 lines, imports inside functions, a Postgres test run, a session that expires mid-use, help text on the token form and Settings fields. | Questions 219, 220. **Part 1 built 2026-11-11:** tus header checks, `/api/docs` admin-only, Caddy and PostgreSQL tags pinned, the session-ended notice. **Part 2 built the same day:** `/hosts/{id}/resources` visibility, raw errors in `create_session`, help text on the token form and 19 Settings fields. **Left:** the API image runs as root (the volume, `/dev/kvm` and SSH keys need a user change and a migration of existing volumes: its own plan), (question 221, the CA's addresses for an environment with no named network: **built 2026-11-11** with the extra-ranges setting), the files over 1,000 lines, imports inside functions, the Postgres test run (**built 2026-11-11**, `tools/helpers/test-postgres.sh`; it found the no-groups bug), help text. |
| B2 | **WinRM listener certificate from the platform CA**, so WinRM is checked against the CA instead of pinned. | |
| B3 | **Ansible host-key and certificate checks** at deploy time (today they accept what they first see). | Needs a first-seen record per VM. |
| B4 | **Close open remote sessions when a user is disabled** (the Users page's Disable). | **Built 2026-11-08:** the guacd relay checks every 5 s that the user is still enabled; `userstatus.disable` closes the owner's text sessions. |
| B5 | **Remove the Hyper-V remnants.** | Question 218. |
| B6 | Kerberos click-through from `l2win1` and the Director update: **manual**, the user's. | Listed in the manual checks. |

## 3. Order

A3 first (it finds what is really broken before more is built), then B4 and B1 (security), A1, B2, B3, B5, A2. Question 217.
