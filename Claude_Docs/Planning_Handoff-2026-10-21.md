# Handoff, 2026-10-21

> **Stage: Planning.** Where LABaPe stands at the end of 2026-10-21, for whoever picks it up next (a new Claude Code session or a developer). Read this first, then [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) (its section 5 gotchas and the lab's access still apply) and [Planning_Handoff-2026-10-16.md](Planning_Handoff-2026-10-16.md). It replaces the 2026-10-19 file as the starting point. LABaPe only.

## 1. State

`main` is at the merge of PR #198 (`e3f678a`); **no PR is open**. **v0.5.5 is the latest release** (2026-10-20); everything since is in `CHANGELOG.md` `[Unreleased]`, **not released** (the user said "no release yet" on 2026-10-21). pytest: **467 passed, 1 skipped, 0 failed** (on Windows too). Questions continue from **196**; **D184, D185 and D186 are open** (§4), the others are answered.

| PR | What | Design |
|---|---|---|
| #190 | Live helper `env_plan.py` (preview a redeploy) | |
| #191, #192 | Release v0.5.5; handoff update | |
| #193 | Fixes from the manual and browser runs of v0.5.5: revoked and expired tokens hidden and purged, a published package refuses draft items on save, filter drop-downs apply at once, live validation in the New environment form, the Role follows the OS, accounts show VM names and clear sudo with the last Linux host, host-load bars line up | §60-§61, log |
| #194 | Overdue rotations highlighted on the Secrets page; sticky top bar; the plan for rotation, hosts, disks and the CA | §62 |
| #195 | **Rotate overdue now** (R2): a confirmed, ordered, stop-on-failure bulk rotation with a progress banner | §62.1 |
| #196 | **Add host** (E2): more VMs in a group, or a new group copying one, then a normal redeploy | §63 |
| #197 | **Drive pages with SMART history** (H3): readings every 6 hours kept 365 days, charts, when the numbers moved, links from alerts | §64 |
| #198 | **Certificates** (C2): a register of every certificate the platform CA signs, a page to list and request them, revoke, the CA's details, roles `ca_admin` and `ca_requester` | §65, §65.1 |

Plan for the four batches: [Planning_Rotation-Hosts-Disks-CA.md](Planning_Rotation-Hosts-Disks-CA.md) (all built). Manual checks: [Testing_Manual-Checks-v0.5.5.md](Testing_Manual-Checks-v0.5.5.md) and the findings in `Testing_Troubleshooting-Log.md`.

## 2. How much of it has been seen

| Thing | Checked how |
|---|---|
| Everything above | Unit tests, and the web build with its type-check on the lab. |
| #193's fixes | A browser agent ran lab-exchange requests 03-09 on 2026-10-21: 03, 04, 07, 08, 09 passed; 06 passed except 06.5 (the duplicate group-name message, fixed after); 05 stopped because the request reused a retired package name. |
| Certificates | **Live against the real step-ca on OZ** (`tools/helpers/live/certs.py`): sign, key once, download, revoke; two bugs found and fixed there. |
| Drive page | Live API on kvm1 `/dev/sdc`: migration applied, a reading stored, 17 attributes, the alert attached. |
| **Not seen in a browser** | The remaining fixes of #193, the overdue display and sticky bar, Rotate overdue now's dialog, Add host's dialog, the drive page, the Certificates page. They are lab-exchange requests **10 to 16** in `C:\Code\lab-exchange\requests`, written and not yet run (no results in `results/`). All of them only look and press Cancel. |
| **Never run for real** | **Rotate overdue now** (it restarts the lab's services: three "/ restart" rotations are overdue on OZ: Platform CA key password, Master key, PostgreSQL password); a real **Add host** (it creates VMs); a certificate request by a plain `ca_requester`. |

## 3. What's left (in order)

1. **Run browser requests 10-16** with the browser agent (README of `lab-exchange`; `data/browser-testing-common.md`), then fix what they find. The user wants a better way to run these (fewer "allow" clicks): Planning_Backlog.md row 21.
2. **Release v0.5.6** when the user says (`release.sh` on Windows, where the signing key is; `deploy-web.sh` from clean `main` first; the 2026-10-20 request `02-release-0.5.5.sh` is the pattern). `[Unreleased]` is tidied (features under Added, fixes under Fixed). Fold the user-docs backlog (about 20 lines) into `User_Docs/` at that release.
3. **Rotate overdue now, for real** (the user): Admin → Secrets → the warning's button; watch the banner through the restarts; the three rotations should clear.
4. **Replace the failing disk** `kvm1: /dev/sdc` (76 reallocated, 3,960 pending sectors, open since 2026-10-14). The drive page now records its trend; history starts 2026-10-21, so the chart needs a day or two.
5. **Authentik groups** `labape-console`, `labape-ca-admins` and `labape-ca-requesters` are mapped to roles by default but not created in Authentik; create them and bind users.
6. **A real Add host** on a throwaway environment (creates VMs on the lab), and a **request by a `ca_requester`** who is not an admin, to see the name limits live.
7. **Kerberos click-through** (the user): RDP to `krbw1` (environment `krbwin11`, 31) as `LABAPE\jesse`, Edge to `https://auth.labape.test:9445/`, sign in to LABaPe with Windows credentials (design §58); then destroy environment 31 and delete the computer account `KRBW1` in `labape.test`.
8. **Director (`pi1`)**: check its version and update it with Admin → Updates (the user's click) once the next release exists.
9. **Open questions:** D184 domain member or standalone per host group; D185 save a new environment as a draft, and duplicate an environment; D186 bind a local account to individual VMs. Recommendations are in `Planning_Questions.md`.
10. **Later:** the Kerberos domain side as an installer step or Settings page; the published `.docx` guides (D24); mail relays against a real outside SMTP account; the rest of the older queue in the 2026-10-19 handoff §3.

## 4. Lab state (2026-10-21)

| Thing | State |
|---|---|
| kvm1 / OZ (host 1) | Runs the code of `main` (`deploy-web.sh` of the C2 branch, `v0.5.5-10-...-dirty`, same content), health ok. **`~/LABaPe-web` is dirty** (deploy-web leaves it so): reset it with `git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main` and `~/LABaPe-web/deploy/update-agent/install.sh` before using Admin → Updates; a release needs `deploy-web.sh` from clean `main` anyway. Migrations applied: `0031` (drive history), `0032` (certificates). |
| Hosts, environments | As in the 2026-10-19 handoff: kvm1, nkvm1, pi1 (Director), garagepc; `lab2` (3), `syslog1`, `nestkvm`, `agent1`, `testweb`, `krbwin11` (31). `sudotest` (32) was deleted by the user. |
| Test data left behind | Catalog: `zz-draft-app` (draft application, item 28), `zz-test-pkg` (retired, item 25); revoked tokens named `zz-console-test`; three revoked test certificates `zz-cert-test.auth.labape.test` in the register. None can be deleted from the UI. |
| lab-exchange | `C:\Code\lab-exchange`: requests 01-02 (scripts, done), 03-09 (browser, done), **10-16 (browser, waiting)**, `data/browser-testing-common.md` (rules and report format for the browser agent), `labape-mcp.cmd`. |

## 5. Gotchas learned since 2026-10-19

1. **Stacked PRs:** merge from the bottom, `gh pr edit <next> --base main` after each merge, and delete the branches only at the end; deleting a base branch on merge closes the PRs above it (it happened to #172).
2. **`step ca`:** the CLI stops reading flags at the first positional argument (the serial goes last), and `step ca revoke` has no provisioner flags: mint a token with `step ca token --revoke` first (`ca.revoke` does both). Revocation is passive.
3. **Rotations that restart services go through the update agent**, which restarts the API and worker; the bulk queue (`rotation_queue` in the settings table) is moved on by `rotate_overdue.advance()` from the worker's rotation schedule, so it survives that. The agent does one request at a time.
4. **SMART:** read every 6 hours and on Read now; the history table `disk_samples` is purged by Settings → History → Drive history. `hostadmin.warnings()` carries the drive (`disk`) so alerts can link to its page; a stored alert's drive is parsed from its message (`disk_of`).
5. **The register of certificates** is LABaPe's own (step-ca has no list); a failure to record never fails a signing. The stack's own certificates enter at their next renewal or by **Add to the register**.
6. **Tools:** `\n` inside a Python string in a shell heredoc becomes a real newline; write edit scripts with the Write tool or use Edit. `deploy-web.sh` puts the working tree on the lab (and syncs untracked files), so keep the tree clean of stray files when a release will follow.
7. **The browser agent** (lab-exchange 03-16) is only for looking: its requests say never to press Create, Destroy, Save or the final button of a dialog; a request that changes anything states it in `Changes:`.
8. **Two sessions use this repo** (the Cowork session switches branches and opens PRs): check `git branch --show-current` before deploying or releasing.

## 6. Where things are

| Need | Look at |
|---|---|
| Rotate overdue, add host, drive pages, certificates | design §62-§65; `engine/rotate_overdue.py`, `api/env_hosts.py`, `api/host_disks.py`, `certs.py`, `api/certificates.py`; web: `components/RotateOverdue.tsx`, `AddHosts.tsx`, `LineChart.tsx`, `pages/DiskDetail.tsx`, `Certificates.tsx` |
| Live checks | `tools/helpers/live/` (`certs.py`, `disk_detail.py`, `env_plan.py`, `console.py`, `catalog_platforms.py`) through `tools/helpers/lab-live.sh` |
| The browser tests | `C:\Code\lab-exchange\README.md`, `data/browser-testing-common.md`, `requests/` |
| Console API and MCP | Reference_Console-API-And-MCP.md (design §59) |
| Everything else | the 2026-10-19 and 2026-10-16 handoffs §7 |
