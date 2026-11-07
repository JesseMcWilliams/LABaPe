# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- Installer: `Prereq.RaidAlerts` (RAID events to syslog; asks before replacing an existing `PROGRAM` in `mdadm.conf`, answer `ReplaceRaidProgram`): mention in `Install` guides and `Command-Line.md`.
- Drive Pools: member serial numbers, the empty slot, the drive failed out and which drive holds the only copy: `Web-Interface-Admin.md`.
- Installer: `Prereq.SmartAlerts` (smartd's disk warnings mailed to Mailpit: installs the `mail` command; leaves a `smartd.conf` without `-m` alone and says so): `Web-Interface-Admin.md` §6.1 and the install guide.
- Containers page (any host with Docker or Podman; read-only; logs, stats, images, volumes, the alert switch): `Web-Interface-Admin.md` host sections.
- Drive Pools: **Re-add** a drive failed out of an md array (health check first, typed confirmation for a drive that fails it, full-rebuild option): `Web-Interface-Admin.md` Drive Pools.
- Installer: `Prereq.RaidDiskTimeouts` (SCT error recovery control 7 s on SATA disks of a host with RAID arrays, via a udev rule): `Web-Interface-Admin.md` §6.1 and the install guide.
- Installer: `Prereq.AlertMail` (the host's own mail to the stack's Mailpit; `MailBind`, `AlertMailHost`): `Web-Interface-Admin.md` §6.1 and the install guides.
