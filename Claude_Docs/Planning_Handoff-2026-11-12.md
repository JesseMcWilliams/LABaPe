# Handoff, 2026-11-12

> **Stage: Planning.** Where LABaPe stands at the end of the 2026-11-02 to 11-12 sessions (OZ's failed drive, then everything it led to), for whoever picks it up next. Read this first, then [Planning_Handoff-2026-10-24.md](Planning_Handoff-2026-10-24.md) (the review, v0.7.0, its gotchas in §5 still apply) and the older ones it builds on. It replaces the 2026-10-24 file as the one to start from.

## 1. State

**Latest release:** v0.8.1 (2026-11-12)

`main` has everything below merged and **released as v0.8.0** (2026-11-12; the CHANGELOG's `[Unreleased]` is empty again). pytest on SQLite in a fresh Windows venv with `pip install -e app pytest requests`: **681 passed, 2 skipped, 1 failed** (`test_pywinrm_session_uses_the_pin`: no `winrm`; with `pywinrm` installed all pass); on PostgreSQL (the release's run): **680 passed, 3 skipped** (the two `pg_dump` tests skip without `pg_dump`). `npm run build` passes. Questions 203-226 are all answered (the default recommendation where the user said so); the next free question is **230** (227 and 228 are the root-image plan's, open with recommendations; 229 is answered).

| PR | What |
|---|---|
| #259-#261 | OZ's incident: mdadm and smartd mail reach the stack's Mailpit (`Prereq.AlertMail`, `RaidAlerts`, `SmartAlerts`), SCT error recovery 7 s (`RaidDiskTimeouts`), Drive Pools **Re-add** with a typed confirmation, `PoolsSection.tsx` split out |
| #262, #263 | **Containers page** for any Docker or Podman host: read (C1), start/stop/restart/pull-and-recreate by a per-host level off/known/new/all (C2), clean-up with a preview (C3); the Stack's own containers are read-only (design §75) |
| #264 | Scheduled **SMART self-tests** and a Last self-test column and warnings (S1, §76); **Notifications page** with channels (email, webhook, ntfy, syslog), rules, quiet hours, reminders, delivery log (N1, §77); **heartbeat** (each host mails and logs to the stack daily) and **daily digest** (N2, §78); **guided drive replacement** (R1, §79); container extras (C4, §80: unused-images alert, update a compose project, weekly newer-image check); `Prereq.DiagSudo` |
| #265 | The next round's plan; the manual-check list ([Testing_Manual-Checks-Disk-Notifications.md](Testing_Manual-Checks-Disk-Notifications.md)); disabling a user ends their open sessions (B4); review fixes (tus header checks, admin-only `/api/docs`, pinned Caddy and PostgreSQL, a session-ended notice, host resources visibility, help text); the CA signs only an environment's allocated addresses plus a new **Extra ranges for environments** setting (question 221); the **Postgres test run** (`tools/helpers/test-postgres.sh`) and the empty-group grants bug it found |
| #266 | Installer `steps.py` split into `steps`, `steps_base`, `steps_stack` |
| #267, #269, #270 | **B2**: a Windows VM's WinRM listener answers with its platform-CA certificate, checked against the CA (not pinned); **B3**: Ansible's later runs check recorded SSH host keys and CA-signed WinRM certificates (plan: [Planning_WinRM-And-Host-Keys.md](Planning_WinRM-And-Host-Keys.md)) |
| #268 | **A1**: container CPU and memory history (§81), migration 0045 |
| #271 | A stale apt index no longer fails a Debian deploy; **A2**: rootless Podman of the access account on the Containers page, read-only (§82) |
| #273-#276 | Handoff; the root-image plan; Hyper-V removed; PostgreSQL run in `release.sh`; stdlib imports hoisted; guides up to date; a second `pg_dump` skip (#275); **Release v0.8.0** (#276) |
| #278 | **Apply host setup** (question 229, design §83): a `host.setup` job runs the installer's six alert and disk `Prereq.*` steps on a registered host; an **Apply host setup** panel in a host's SSH access section; the monitoring tick queues it for every ready host whose setup predates the installer's steps (hash of `install-labape.py` and `steps.py`), so a stack update brings hosts along (`hostsetup.auto` switches it off). Merged after v0.8.0, so it is in `[Unreleased]` |
| #280 | **Rollback across added tables:** the restore empties the schema and loads the dump in one transaction (the Pi's rollback from v0.8.0 failed on `host_containers`, §5.14); the Roll back choice asks to confirm; `web-tls.sh` committed executable. Released as v0.8.1 |
| #272 | `runner.py` split into `runner` and `runner_prepare`; `api/environments.py` into `environments` and `environments_vms` (no code file is over 1,000 lines now) |

Migrations are through **0045** (0039-0045 are new: host containers, container levels, notifications, digest, host heartbeat, container updates, container samples).

## 2. How much of it has been seen

| Thing | Checked how |
|---|---|
| Containers page (C1-C3), the newer-image check (C4), the unused-images alert, history (A1) | **Live on OZ** (kvm1 and pi1): reads, actions on throwaway containers, clean-up, a newer-image check (caddy and postgres tags reported newer), samples stored |
| Notifications (N1) | A Slack-shaped webhook to a local receiver on OZ (secret in the store, test message arrived); **not** a real ntfy, Teams or Slack |
| Heartbeat (N2) | kvm1: mail and syslog both arrived end to end; pi1 and garagepc: "no mail program" (a real gap, §3.2); nkvm1 unreachable |
| B2 and B3 | **Live** on throwaway VMs (`b2win` Windows 10, `b3lin` Debian): listener bound to the platform certificate, a forced renewal moved it, commands and `win_ping` validated against the platform root; a changed SSH key failed with the hint; the first attempt exposed the ControlPersist reuse (fixed) |
| Postgres test run | Run through an SSH tunnel to a throwaway postgres on OZ; the full suite on PostgreSQL passed apart from two Windows-box gaps (the backup-job test needs `pg_dump`, `test_update.py::test_cli_commands` needs a capturable stdout) |
| **Apply host setup** | **Live 2026-11-12:** ran by itself on kvm1, pi1 and garagepc after the deploy (steps Completed or NotApplicable, mail to Mailpit); nkvm1 failed (unreachable), retried daily. The panel is type-checked by the image build, **not seen in a browser** |
| **Never run for real** | The self-test read on real drives (the first run showed empty `tests` for all five kvm1 drives: confirm with `sudo smartctl -a`); the installer steps `DiagSudo`, `SmartTests`, `SmartAlerts`, `RaidDiskTimeouts` on OZ and `AlertMail` on pi1 and garagepc (the user runs them); **Replace a drive** on a real array (`md10` is healthy now); rootless Podman on a host that has one; Podman hosts generally; the UI pages in a browser (Notifications, the History tab, Replace dialog) |

## 3. What's left (in order)

1. ~~**Release v0.8.0**~~ **Done 2026-11-12** (PR #276, signed tag, GitHub release with the SBOM, the MCP bundle and the skill zip). No `.docx` guides: pandoc isn't installed on the Windows box (`Published_Docs/` stays empty). The release's PostgreSQL run found a second `pg_dump` test (#275).
2. **The user, on OZ:** the `Prereq.*` steps have already been applied to kvm1, pi1 and garagepc by Apply host setup (2026-11-12), so skip the installer run; do the **checks** in [Testing_Manual-Checks-Disk-Notifications.md](Testing_Manual-Checks-Disk-Notifications.md) §1 (1.1-1.6: `sudo -l`, `smartctl -a` as the service user, the smartd test mail in Mailpit, the `DEVICESCAN` line, `scterc`, `echo hi | mail -s test root` on pi1 and garagepc), then record the results table; **replace the failed drive `P8G8JSPR`** (Replace a drive…, not Re-add).
3. **Build the non-root images** from [Planning_Root-Image.md](Planning_Root-Image.md) (questions 227-228): entrypoint, `user:` and `.env` keys, the agent's one-time volume `chown`, live test on OZ; then the hardening PR. (The plan is written, nothing built.)
4. **Remaining small review items:** done 2026-11-12 (stdlib imports hoisted: the 325 imports of the package itself and `cryptography`, `httpx`, `winrm` stay on purpose; Hyper-V removed; Postgres run in `release.sh`). Left over: the WSL install guides are still a Hyper-V walkthrough (backlog line).
5. **Later:** actions on rootless Podman containers (question 216); a real ntfy or webhook on a rule (and Teams); per-host staggering of long self-tests; NVMe self-tests; a check that a Podman host's `skopeo` exists for the newer-image check.
6. **From the older handoffs:** the Director's Fix, deleting the test template `debian13-linktest-2026.10`, the garagepc folder check, the Kerberos click-through (handoff 2026-10-24 §3).

## 4. Lab state (2026-11-12)

| Thing | State |
|---|---|
| kvm1 / OZ | The web stack runs `v0.8.0-4-g32defec` (main after #278), put there by `deploy-web.sh`; and `~/LABaPe-web` was reset to `main` with the update agent reinstalled (2026-11-12); the stack was then redeployed from clean `main` as `v0.8.0-4-g32defec`. Migrations through **0045**. The compose pins (`caddy:2.11.7`, `postgres:17.11-alpine`) are in the repo but its containers still run the older builds of the same versions until the stack is recreated. `md10` is healthy (the failed drive is back or replaced: confirm which). No Notifications channels or rules are configured |
| pi1 (the Director, 172.21.21.16) | Updated to v0.8.0 at 09:19 MST; a rollback at 16:57 failed (§5.14) and left it stopped in maintenance; recovered by hand (checkout v0.8.0, `compose up -d`, `labape maintenance off` via `docker exec`); database intact at 0045. Its `previous.json` should be removed until it runs v0.8.1 (a rollback there would have failed the same way); `web-tls.sh` needed `chmod +x` |
| garagepc, nkvm1 | pi1 and garagepc got the mail program from Apply host setup, so the "alerts may not arrive" warning should clear after the next daily heartbeat (confirm); nkvm1 (172.21.50.3) is unreachable, so its setup job fails and retries daily |
| Test environments | `b2win`, `b3lin`, `fxcheck` were destroyed; `lab2`, `krbwin11`, `agent1`, `nestkvm`, `syslog1` are the long-lived ones (lab2's `l2lin1` did not answer on 2026-11-12) |
| Test data left behind | A few notification log rows from the live test; the `container_samples` and `host_containers` data are real |

## 5. Gotchas learned

1. **Heredocs (and the shell tool) eat backslashes:** `\\x`, `\t`, `\n` inside a Python script passed on the command line turn into a tab or newline (a `\tls` path became a TAB; the NUL replacement failed twice). Write the script with the Write tool, build a backslash with `chr(92)`, or use the Edit tool.
2. **The auto-mode check can refuse a combined command** (deploy plus commit plus push in one line): run them as separate commands.
3. **Stopping a background test run leaves its pytest and ssh tunnel alive** (they kept using the shared database): find and kill them (`Get-CimInstance Win32_Process`) before a rerun; `test-postgres.sh` with paths runs only those paths now.
4. **Python 3.13+ verifies TLS strictly:** a test CA needs KeyUsage (certificate signing), SubjectKeyIdentifier and AuthorityKeyIdentifier or the handshake fails.
5. **ssh ignores an unparseable key in known_hosts:** a corrupted record is no mismatch; a mismatch test needs a valid different key. And **a connection left open by a previous Ansible run is reused without a host-key check**: `harden` gives each run its own control-socket folder.
6. **SQLite doesn't enforce foreign keys,** so fixtures point rows at hosts that don't exist; on PostgreSQL `conftest.py` adds a stub host. And **PostgreSQL rejects a NUL byte in a parameter** (an empty group list used to bind one).
7. **`deploy-web.sh` restarts the API: don't run it while a deploy job runs** (still true), and a `docker exec ... python -c` against the API container needs `from labape.db import session_factory` (there is no `SessionLocal`).
8. **A Debian VM's package index goes stale** (the mirror drops superseded versions): the apt install now refreshes it.
9. **The module split rule:** tests patch names on the module; after a split, patch the module that now holds the code (`runner_prepare.overlay_vault`).
10. **Docs:** the SBOM is generated (`python tools/helpers/sbom.py`; it reads `steps_base.py`); a test fails when it is stale.

11. **Releasing from the Windows box:** `release.sh` needs `LABAPE_LAB_SSH`, `LABAPE_LAB_KEY` and `PYTHON` (a venv with `pip install -e app pytest requests pywinrm`) set; run `deploy-web.sh` from clean `main` first or pass `--sbom-anyway`; the PostgreSQL run takes about 13 minutes; start the release in the background and poll its log. A `--dry-run` skips the PostgreSQL run. `--docx` needs pandoc (not installed).
12. **The setup job's installer instance is `setup`** (`/opt/labape-kvm/deploy/state`, logs `install-setup-*.log`), apart from Prepare's `web`; its summary lists every other step as "Not yet completed", which is only noise. **The tick runs it on the first monitoring pass after an update** that changes `install-labape.py` or `steps.py`, so after any change to those files expect root-run installer steps on every host without a click.
13. **A new `.sh` file may be CRLF in the working copy** (`scripts/test/test-opentofu-config.sh` was): git normalises it at commit, bash on the box can't run it until then.

14. **A rollback across a release that added tables used to fail** (`pg_restore --clean` cannot drop a primary key a newer table points at). The restore is now `pg_restore -f - | psql --single-transaction` after `DROP SCHEMA public CASCADE`, so a failure leaves the database as it was; but the agent has already stopped the API and worker and checked out the old code by then, so a failed rollback needs a hand roll-forward: `git checkout --detach <tag>`, `docker compose build && up -d` as the service user, then `docker exec labape-labape-api-1 labape maintenance off --source update` (the `labape` CLI is only inside the container). The agent on a Pi runs as the `labape` user: its journal is `sudo -u labape XDG_RUNTIME_DIR=/run/user/$(id -u labape) journalctl --user`, its log `/opt/LABaPe/container/update/agent.log`.

## 6. Where things are

| Need | Look at |
|---|---|
| Containers page | `engine/hostcontainers.py` (collector script, build, actions, clean-up, history, rootless), `engine/containerupdates.py`, `api/host_containers.py`, `web/src/components/ContainersSection.tsx`, `ContainersCleanup.tsx`, `ContainerHistory.tsx` |
| Notifications and the digest | `engine/notifications.py`, `api/notifications.py`, `web/src/pages/Notifications.tsx`; `stackmon.sync_alerts` calls it |
| Heartbeat | `engine/heartbeat.py` (daily, Setting `heartbeat.host<id>`, `monitor:heartbeat:<id>` alerts) |
| SMART self-tests, replacement, Re-add | `engine/hostadmin.py` (`selftest_warnings`), `engine/hostpools.py`, `api/hosts.py` (`/pools/replace`), `web/src/components/PoolsSection.tsx`, `PoolReplace.tsx` |
| WinRM and host-key checks | `engine/vmidentity.py` (`chains_to_platform_ca`, `harden`), `engine/vmexec.py`, `engine/vmaccess.py`, `ansible/roles/labape_cert/tasks/windows.yml` |
| Installer steps | `deploy/labape_deploy/steps.py`, `steps_base.py`, `steps_stack.py`; the step table in `deploy/install-labape.py` |
| Tests on PostgreSQL | `tools/helpers/test-postgres.sh` (see its README entry) |
| Live checks | `tools/helpers/live/` (`winrm_ca_check.py`, `env_create.py`, `env_job.py`), the manual-check list for the disk and notification work |
| The plans | [Planning_Disk-Health-Notifications.md](Planning_Disk-Health-Notifications.md), [Planning_Container-Management.md](Planning_Container-Management.md), [Planning_Next-Round-2026-11-08.md](Planning_Next-Round-2026-11-08.md), [Planning_WinRM-And-Host-Keys.md](Planning_WinRM-And-Host-Keys.md) |
