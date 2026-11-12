# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- Installer: `Prereq.RaidAlerts` (RAID events to syslog; asks before replacing an existing `PROGRAM` in `mdadm.conf`, answer `ReplaceRaidProgram`): mention in `Install` guides and `Command-Line.md`.
- Drive Pools: member serial numbers, the empty slot, the drive failed out and which drive holds the only copy: `Web-Interface-Admin.md`.
- Installer: `Prereq.SmartAlerts` (smartd's disk warnings mailed to Mailpit: installs the `mail` command; leaves a `smartd.conf` without `-m` alone and says so): `Web-Interface-Admin.md` §6.1 and the install guide.
- Installer: `Prereq.SmartTests` (weekly short, monthly long SMART self-tests on `DEVICESCAN`); Drives and volumes: the Last self-test column and its warnings: `Web-Interface-Admin.md` Drives and volumes, the install guide.
- Containers page (any host with Docker or Podman; logs, stats, images, volumes, the alert switch; the level setting, the start/stop/restart/pull-and-recreate buttons, Clean up…): `Web-Interface-Admin.md` host sections.
- Drive Pools: **Re-add** a drive failed out of an md array (health check first, typed confirmation for a drive that fails it, full-rebuild option): `Web-Interface-Admin.md` Drive Pools.
- Installer: `Prereq.RaidDiskTimeouts` (SCT error recovery control 7 s on SATA disks of a host with RAID arrays, via a udev rule): `Web-Interface-Admin.md` §6.1 and the install guide.
- Installer: `Prereq.AlertMail` (the host's own mail to the stack's Mailpit; `MailBind`, `AlertMailHost`): `Web-Interface-Admin.md` §6.1 and the install guides.
- Admin → Notifications (channels, rules, quiet hours, reminders, delivery log; secrets write-only): `Web-Interface-Admin.md` (new section) and the Settings → Monitoring text.
- Settings → Monitoring: the daily alert-path check (heartbeat) and digest hour; Notifications rule option "daily digest": `Web-Interface-Admin.md`.
- Drive Pools: **Replace a drive…** (guided replacement of a failed md member; typed serial): `Web-Interface-Admin.md` Drive Pools.
- Containers: Update <project>, the weekly newer-image check (per-host switch, Check now, "newer image" chip), the unused-images alert (Settings → Monitoring); host setting "Check that its alerts arrive": `Web-Interface-Admin.md`.
- Admin → Users → Disable also closes the user's open remote and console sessions: `Web-Interface-Admin.md` Users.
- `/api/docs` is for admins only; uploads over 200 GB are refused; a sign-in notice when the session ends mid-use: `Web-Interface-Admin.md`, `Command-Line.md` (API docs), `Web-Interface-Guide.md`.
- Certificates → Extra ranges for environments; an environment without a named network gets certificates only for its allocated addresses: `Web-Interface-Admin.md` Certificates.
