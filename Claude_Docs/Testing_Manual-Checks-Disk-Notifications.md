# Manual checks: disk health, notifications, containers (2026-11-08)

> **Stage: Testing.** What only the lab can show, for the work built after OZ's drive failure (design §70-§80; plan [Planning_Disk-Health-Notifications.md](Planning_Disk-Health-Notifications.md)). The user runs the steps that change the lab; Claude records the results in the table at the end. Lab details are in `Live-Testing.local.md` and are not repeated here.

Order matters: 1 to 4 first (they fix what the later checks read).

## 1. Installer steps on OZ (kvm1)

Run `sudo deploy/install-labape.py --profile kvm --step 'Prereq.*'` (or the named steps) on OZ, then check:

| # | Step | Check | Expect |
|---|---|---|---|
| 1.1 | `Prereq.DiagSudo` | `sudo -l -U <service user>` | `smartctl *`, `mdadm --detail *`, `mdadm --examine *` with NOPASSWD, nothing else |
| 1.2 | `Prereq.DiagSudo` | `sudo smartctl -a /dev/sdd` as the service user | Reads without a password; shows the self-test log |
| 1.3 | `Prereq.SmartAlerts` | `grep -n DEVICESCAN /etc/smartd.conf`; `sudo smartd -q onecheck` or the test mail in Mailpit | `-m root` and `-M exec` present; the test mail is in Mailpit |
| 1.4 | `Prereq.SmartTests` | the same line | `-s (S/../../7/02\|L/../01/./03)` appended; a second run changes nothing |
| 1.5 | `Prereq.RaidDiskTimeouts` | `sudo smartctl -l scterc /dev/sdb` | Read and write 7.0 seconds on each array member |
| 1.6 | `Prereq.AlertMail` on **pi1** and **garagepc** | `echo hi \| mail -s test root` | The message is in Mailpit |

## 2. Self-test warnings

| # | Check | Expect |
|---|---|---|
| 2.1 | Admin → KVM hosts → kvm1 → Drives and volumes, **Last self-test** column | "never" or a date per drive; before the first scheduled test a warning "no SMART self-test has ever run" |
| 2.2 | Start a short test by hand (`sudo smartctl -t short /dev/sdb`), refresh the host after it ends | The column shows the short test; the warning for that drive goes |
| 2.3 | After the first Sunday 02:00 | Every drive has a short test newer than 7 days |

## 3. Heartbeat (alert-path check)

| # | Check | Expect |
|---|---|---|
| 3.1 | Dashboard after a day | No `heartbeat` warning for kvm1; mail and syslog both arrived |
| 3.2 | pi1 and garagepc before 1.6 | "alerts from this host may not arrive: it has no mail program"; after 1.6 and the next day the warning clears. Or untick the host's "Check that its alerts arrive" |
| 3.3 | On kvm1: `sudo systemctl stop msmtp` is not a daemon; instead rename `/etc/msmtprc`, wait two days (or run the heartbeat twice from the host's Refresh) | Warning "its own mail didn't reach the stack's Mailpit"; put the file back, the warning clears the next day |

## 4. Notifications

| # | Check | Expect |
|---|---|---|
| 4.1 | Admin → Notifications → New channel: ntfy (a long random topic) or a Teams/Slack/Discord webhook; **Test** | The phone or chat room gets "[TEST] ..."; the delivery log shows "sent" |
| 4.2 | The webhook address after saving | Not shown anywhere; Edit says "Saved (host)" |
| 4.3 | New rule: errors only, that channel, reminder every 1 hour, "daily digest" on | Saved |
| 4.4 | Raise an alert (unplug nothing: stop a non-stack test container with Alerts on) | One message within a minute; a reminder an hour later; the all-clear when it is started again |
| 4.5 | Quiet hours covering now | The alert is held and sent when they end |
| 4.6 | Break the webhook (change the address to a dead one), **Test** | "failed: ..." on the channel, and a warning "notification channel ... can't deliver" |
| 4.7 | At the digest hour (UTC) | One message listing open and cleared alerts; none on a quiet day |

## 5. Containers

| # | Check | Expect |
|---|---|---|
| 5.1 | Containers → kvm1 → Check weekly on, **Check now** | caddy:2 and the postgres tags marked "newer image" if they are; the locally built images are "couldn't be checked" |
| 5.2 | A throwaway compose project on kvm1, level "all", **Update <project>** | Pulls, recreates changed services, nothing else touched |
| 5.3 | Clean up… (the unused images) | The preview lists them; after removal the "unused images" warning (over 20 GB) goes |
| 5.4 | Podman host, if any | The same page works (untried) |

## 6. Drive replacement (when a drive next fails, or on a spare test array)

| # | Check | Expect |
|---|---|---|
| 6.1 | Drive Pools → degraded array → **Replace a drive…** | Names the drive to pull by serial; offers only free whole disks at least as large |
| 6.2 | Type the new drive's serial, **Prepare and add** | The partition layout is copied, the partition joins the array, the pool shows "rebuilding" with progress |
| 6.3 | A wrong serial, a drive that is smaller or has data | Refused with the reason |
| 6.4 | After the rebuild | Pool "healthy"; the old drive's serial is no longer in the array |

## 7. Disabling a user (B4)

| # | Check | Expect |
|---|---|---|
| 7.1 | A user has a browser SSH session open; an admin disables them in Admin → Users | The session closes within about 5 seconds; the session log says "closed: the account was disabled" |
| 7.2 | The user had a console text session (an AI token) open | It is closed at once |

## Results

| Check | Date | Result | Notes |
|---|---|---|---|
| | | | |
