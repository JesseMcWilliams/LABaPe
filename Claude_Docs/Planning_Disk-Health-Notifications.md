# Plan: disk self-tests, a Notifications page, an alert-path check, drive replacement, container extras

> **Stage: Planning.** What OZ's failed-out drive (2026-11-02, noticed 2026-11-06) showed was missing, as the user chose it on 2026-11-06 (items 1, 2, 4 and 5 of the list, with the alert delivery of item 3 as its own page). Questions 208-214 in [Planning_Questions.md](Planning_Questions.md) hold the choices I want confirmed; every recommendation is the default if there is no answer. Related: [Planning_Container-Management.md](Planning_Container-Management.md) (Containers page, built), design §30-§31 (monitoring and alert handling).

## 0. What OZ showed

| Finding | Where it came from |
|---|---|
| The failed drive had no SMART self-test for about 40,000 power-on hours | `smartctl -x`: the self-test log's last entries are at 21,163 h; the drive is at 61,359 h |
| Two alert paths failed silently for days | mdadm had no mail program; smartd's `10mail` needed `/usr/bin/mail`. Nothing noticed that nothing arrived |
| Alerts end in Mailpit, which nobody reads | The mail page can relay to a server, but there is no webhook or push channel, no rules, no digest |
| Replacing a drive is a manual, error-prone job | Which serial to pull, partition copy, add, watch the rebuild; the partner drive holds the only copy meanwhile |
| 78.7 GB of Docker images were reclaimable on kvm1 and nothing said so | `docker system df`; the Containers page now shows it, but nothing alerts |

## 1. Batches

| Batch | What | Notes |
|---|---|---|
| S1 | **Scheduled SMART self-tests.** An installer step puts a schedule on the `DEVICESCAN` line of `smartd.conf` (a short test every Sunday 02:00, a long one on the 1st at 03:00) when it has none. The SMART read keeps each drive's last short and long test (time, result, power-on hours). Drives and volumes shows "last test"; a drive with no completed test in 14 days of running (21 for a long one) raises a warning. | Question 208 (the schedule). ATA drives only; NVMe has its own log and is left for later. |
| N1 | **A Notifications page** (Admin). **Channels**: email (to a list, through Settings → SMTP), webhook (generic JSON, plus Teams/Slack/Discord-shaped bodies), ntfy, syslog. **Rules**: match on level, hosts and a category (disk, RAID, container, certificate, backup, secret, stack, alert path), send to one or more channels, with quiet hours and a repeat reminder for an alert still open after N hours. A **Test** button per channel and a delivery log (last 100: when, channel, ok or error). The existing "email to" list and syslog switch become the first channel and rule, so nothing changes until a rule is added. | Questions 209-211. Webhook URLs and tokens are secrets: kept in the platform secret store, never shown again. |
| N2 | **An alert-path check.** Each host sends a heartbeat (a mail through its own mail program to Mailpit, and a syslog line) every day; Monitoring alerts when a host's heartbeat stops arriving in either place, saying which path is dead. The installer's mail and RAID steps are checked the same way. A **digest** (daily, chosen hour) lists open alerts, hosts that have gone quiet, and the day's cleared ones, and goes through the rules as a channel message. | Question 212. |
| R1 | **A guided drive replacement** on Drive Pools: names the drive to pull by serial and slot, waits for a new drive to appear, shows its model and size against the old one, copies the partition layout (`sfdisk --dump` to the new drive), adds it and follows the rebuild. Warns while the partner holds the only copy and refuses a smaller drive. Builds on Re-add (the failed drive is `former_members`). | Question 213. The power-down and swap stay manual. |
| C4 | **Container extras.** A "newer image available" check per container (registry digest compare, weekly, off by default); update of a whole compose project; an alert when Docker's reclaimable space passes a limit; CPU and memory history charts; rootless Podman containers of the access account. | Question 214. |

## 2. Order

S1 first (small, closes the gap that hurt), then N1, N2 (the digest needs the rules), R1, C4. N1 changes how every alert is sent, so it keeps the current behaviour as its default rule and is checked on OZ with a real webhook before it is the only path.

## 3. Questions (recommendations are the defaults)

| # | Question | Recommendation |
|---|---|---|
| 208 | The SMART self-test schedule? | Short test every Sunday 02:00, long test on the 1st at 03:00, both set on `DEVICESCAN`. The long test of every drive on one night slows arrays; staggering per drive is possible only with one `smartd.conf` line per drive, which the installer would write when asked. |
| 209 | Channel types for N1? | Email, generic webhook (with Teams, Slack and Discord body shapes), ntfy, syslog. SMS and phone are left to webhooks. |
| 210 | What may a rule match on? | Level (warning, error), hosts (all or chosen), category, and quiet hours; plus a reminder every N hours while open. |
| 211 | Where do channel secrets live? | The platform secret store (OpenBao or Conjur), referenced by name; the page shows only that one is set. |
| 212 | Heartbeat and digest times? | Heartbeat daily; alert after two missed; digest at 07:00 local, one a day, only when something is open or cleared. |
| 213 | Replacement: may LABaPe write the partition table of the new drive? | Yes, with a typed confirmation naming the new drive's serial, and only to a drive with no filesystem and no array label. |
| 214 | Registry checks for newer images: allowed to reach out? | Off by default; per host switch; weekly; digest compare only, no pull. |
