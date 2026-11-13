# Plan: show, adjust and disable the SMART self-test schedule (2026-11-13)

> **Stage: Planning.** Follows the S1 batch of [Planning_Disk-Health-Notifications.md](Planning_Disk-Health-Notifications.md) (design §76), which schedules self-tests with `Prereq.SmartTests` but leaves the schedule fixed and the warning "no SMART self-test has ever run (schedule one: Prereq.SmartTests)" blind to it. Questions 230-234 were answered on 2026-11-13; 235 and 236 are open with recommendations (in [Planning_Questions.md](Planning_Questions.md); in chat they carry a D prefix). Nothing is built yet.

## 1. Why

On the Pi (2026-11-13) the warning said no self-test had ever run although the schedule was in `smartd.conf`: the first scheduled run hadn't come yet. The warning cannot tell "not scheduled" from "scheduled, waiting". There is also nowhere to see the schedule, change it, or say that a host (a Pi with a USB drive smartd cannot test, a hardware-RAID box) should not be warned.

## 2. Decisions (answered 2026-11-13)

| Question | Answer |
|---|---|
| 230 where | **Both:** a fleet page (all hosts: schedule, next run, last result, Edit) and a section on each host's page |
| 231 whose schedule | A **stack default** plus a **per-host override** (use default / custom / off) |
| 232 how much | The short test's weekday and hour, the long test's day of month and hour, each switchable off, **and which drives are tested** (a per-host list of drives to skip) |
| 233 disable | **Two separate switches** per host: *run self-tests* and *warn when none ran*. A failed test still raises its error either way |
| 234 source of "scheduled" | The **host's own `smartd.conf`**, read at collection time, so the page shows what is really configured, even after a hand edit |

## 3. Design

**Settings.** A new section `selftests` (`SelfTestSettings`, the stack default): `run` (true), `short` {enabled, weekday 1-7 (smartd: 1 = Monday, 7 = Sunday), hour 0-23}, `long` {enabled, day 1-28, hour}, `warn` (true). Defaults are today's schedule: short Sunday 02:00, long the 1st at 03:00. A host's override is the Setting `selftests.host<id>`: `{"mode": "default" | "custom" | "off", ...the same fields, "skip": ["/dev/sdb"], "warn": true | false | null}` (`warn` null: follow the default). `effective(db, host)` merges them.

**Applying.** `Prereq.SmartTests` takes the schedule from the answers file (`SmartSchedule`: the `-s (...)` text or empty for none; `SmartSkip`: devices) and **owns one marked block** in `smartd.conf`: a `# LABaPe (Prereq.SmartTests)` comment, then one explicit line for each skipped drive (`/dev/sdX -a -m root`, no `-s`, placed before `DEVICESCAN`; smartd does not monitor a device twice, to be confirmed live) and the `DEVICESCAN` line carrying `-s`. Today the step only appends when there is no `-s`; it must also **replace** its own earlier text, never touch a line it didn't write (an admin's own `-s` stays and is reported). Saving a host's schedule queues a `host.setup` job with `params.steps = ["Prereq.SmartTests"]` so it takes seconds, not the whole health set; `smartd` is restarted. Saving the stack default queues it for every ready host whose mode is `default` (question 236).

**Reading.** The SMART collector (`hostadmin.SMART`) also reports `smartd`: `{conf, active, schedule (the -s text found on DEVICESCAN or on each device line), mtime}`. `selftest_warnings(d, smartd, sched, now)` then says:

| Situation | Message and level |
|---|---|
| Never tested, a schedule covers this drive, its first run is still ahead | `no self-test has run yet; the first is scheduled (short: Sundays 02:00, next 2026-11-15)`: **info** |
| Never tested, scheduled, and a scheduled time has passed since the file changed (+1 day) | `scheduled but no test has run: smartd may be unable to test this drive (a USB bridge needs -d sat)`: warning |
| Never tested, nothing schedules it | `no SMART self-test has ever run and none is scheduled`, naming the page: warning |
| Drive in the host's skip list, or `warn` off | nothing for "never ran" and "stale" |
| Last test failed | the error, as now (never switched off) |

`smartd` not active is its own warning. Times are the host's local time (smartd's), shown as such (question 235).

**API.** `GET /api/admin/selftests` (the fleet: per host the effective schedule, its source, smartd state, per drive the last short and long test, the next run), `PUT /api/admin/selftests/default` (a settings section), `PUT /api/hosts/{id}/selftests` (the override; queues the job and says so), all admin-only, audited. **Web:** a **Self-tests** page next to Notifications, and a *Self-tests* panel in a host's Drives and volumes section with the same edit dialog (a toggle for each switch, weekday and hour pickers, the drive list with checkboxes, a preview line "short: every Sunday at 02:00, long: the 1st at 03:00", and the "applies on the host in a minute" note).

## 4. Batches

| Batch | What |
|---|---|
| T1 | `SelfTestSettings`, `effective()`, the step rewritten to own its block (tests with a fake `smartd.conf`), the collector's `smartd` report and `selftest_warnings` with the new messages, tests |
| T2 | The API, the job's `steps` parameter, queueing on save and on a default change, audit, tests |
| T3 | The Self-tests page, the host panel, the edit dialog, help text and tooltips |
| T4 | Live on the Pi (the `-d sat` case, skip list before `DEVICESCAN`, restart), guides (`User_Docs/Web-Interface-Admin.md`), design §76 update, changelog |

## 5. Open questions

- **235.** Hours are the host's local time (smartd's): show the host's zone in the dialog. Recommendation: yes; a UTC conversion would be wrong the day the host's zone changes.
- **236.** Saving the stack default queues a setup job for every ready host in `default` mode. Recommendation: yes (a quiet, idempotent job), with the confirm dialog naming how many hosts.
