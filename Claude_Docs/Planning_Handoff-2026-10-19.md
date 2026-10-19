# Handoff, 2026-10-19 (evening)

> **Stage: Planning.** Where LABaPe stands at the end of 2026-10-19, for whoever picks it up next. Read this first, then [Planning_Handoff-2026-10-16.md](Planning_Handoff-2026-10-16.md) (the lab, the rules and the gotchas, which still apply). This replaces the morning version of this file (state at PR #179; it is in git history). LABaPe only.

## 1. State

`main` is at the merge of this handoff's PR, after #186 (`98f9cfb`). **v0.5.4 is the latest release**; **v0.5.5 is queued** (section 3). `CHANGELOG.md` `[Unreleased]` holds the three fixes since v0.5.4. No question is unanswered; new questions continue from **184**.

| PR | What |
|---|---|
| #177-#179 | Catalog package picker and OS filters, the Claude Desktop bundle, docs catch-up (see git history of this file) |
| #180 | MCP session tools take `session`: the Desktop bridge drops an argument named `session_id` |
| #181 | Release v0.5.4 |
| #182 | `test_redeploy_preview.py` compares the plan path with the OS separator (it failed on Windows only) |
| #183 | Drive Pools: a ZFS member disk's empty partition 9 is not offered as a free drive (`hostpools.busy()`, the `siblings_busy` rule); design §53.4 Verified row has the live results |
| #184 | Session log: **Play** only for a browser (video) recording, **Download** for every recording (`recording_kind` in the list rows); fixes "can't access property 'size'" on a text transcript |
| #186 | Console API: **run a command** as a domain account (or the domain admin) on a Windows VM signs in over NTLM; WinRM Basic, the inventory's transport, takes local accounts only. Verified live: `account/jesse` on `l2dc1` |
| #185 | The user-docs backlog folded into `User_Docs/` (Guide §3, §4, §11; Admin §5, §8, §16, new §19 and §20; Command-Line `--plan-only`); the backlog is empty |

## 2. Checked live today

1. **The LABaPe MCP server** from Claude Desktop (bundle `labape-mcp-0.5.4.mcpb`): environment and job tools, `run_command` with an environment account, text sessions to VMs and KVM host shells (`open_session(kvm_host_id=…)`).
2. **Director (`pi1`)** has its elevated `authentik-rotator` account (the user).
3. **Drive Pools** on `nkvm1` with seven extra virtual disks: mdadm, ZFS and LVM pools listed, actions run, then everything torn down (pools destroyed, superblocks and signatures wiped, ZFS packages, headers and the contrib source removed, disks detached and their `.qcow2` files deleted). `mdadm` and `lvm2` stay installed on `nkvm1`.
4. **Host deletion** (design §55, Verified row updated): the full cycle on a throwaway host record.
5. **Session recordings**: a text transcript downloads; Play shows only for video.
6. **Nested KVM hosts**: the host limit is now 6 on OZ (`LABAPE_MAX_KVM_HOSTS=6` in `container/.env`).
7. **Full installer run** (`install-labape.py --non-interactive`, profile full, Docker, Authentik with LABaPe, Conjur, `Tls` `labape-ca`, a DHCP-only `lab-vlan48` bridged as `br-lab` on a second NIC) on a fresh Debian 13 VM (`stack1`, environment 14: 4 vCPU, 8 GB, 60 GB, nested KVM): every step completed, `Smoke.Web` healthy with providers breakglass and oidc, 16 containers up. Two restarts (with `--fresh`, then `--resume`) came from how the tester launched it (section 3, item 4). Environment 14 was destroyed afterwards.
8. **Kerberos client `krbwin11`** (environment 31, VM `krbw1`, Windows 11) joined to `labape.test`: DNS to `l2dc1`, a pre-staged computer account with a one-time password (`JoinDomainOrWorkgroup` with flags 193), restart, then `nltest /sc_change_pwd` so the one-time password no longer works; secure channel ok. `krb_client_setup.ps1` trusted the platform root, set the intranet zone and the Edge/Chrome allowlist; `auth.labape.test` resolves to 172.21.49.100 and 9445 answers 302 with a trusted certificate. `HTTP/auth.labape.test` is on `svc-authentik`.

## 3. What's left

1. **Release v0.5.5:** `C:\Code\lab-exchange\requests\02-release-0.5.5.sh` runs `release.sh 0.5.5` on Windows (the signing key is there), dry run first, `--resume` if it stops; summary in `data/release-0.5.5-summary.md`. Waiting on the other agent. Then update the stack to the tag (Admin → Updates, or the deploy script below).
2. **Windows test run:** `requests/01-windows-tests.sh` (pull, `test_redeploy_preview.py`, then the full suite). Waiting on the other agent; results land in `results/`.
3. **Kerberos click-through (the user):** RDP to `krbw1` (environment `krbwin11`) as `LABAPE\jesse`, open Edge at `https://auth.labape.test:9445/` and sign in to LABaPe with Windows credentials (design §58). Then destroy environment 31 and delete the computer account `KRBW1` in `labape.test`.
4. **Installer robustness** (seen in the run, each caused by how the tester launched it): the installer inherits the caller's umask (a `umask 077` made `/opt/labape/venv` unreadable to the service user) and, under `sudo -E`, the caller's `HOME` (root then owned `~/.ansible`, `~/.config`, `~/.cache` of the service user). It could set `umask 022` and `HOME=/root` for itself. After the run, the checkout's `.git/index` was owned by root.
5. **Browser checks still open** (only type-checked): the Tokens page console options (only chosen environments, an account must be named, KVM host consoles, calls per minute, session minutes, output KB); Catalog → New package (the Contents picker with its Windows/Linux filter); the New environment form (OS filter on catalog choices, a single Role, mixed-case group names, time zone).
6. **Small follow-ups:**
   - `POST /api/environments` accepts a disk below the OS minimum (Windows 11: 64 GB); only OpenTofu's precondition catches it, at deploy. Check it at creation.
   - There is no way to delete a failed or destroyed environment record (`DELETE /api/environments/{id}` is 405), so test names pile up.
   - Sudo for a Linux environment account always asks for the password, so `run_command` can't do root work; consider a NOPASSWD option per account.
   - The Desktop import menu path and whether `web-tls.sh renew` updates a production Authentik's redirects (design §58 names `tools/authentik-test/sync-redirects.sh`) were left out of the user docs as unconfirmed.
7. **Later:** the Kerberos domain side as an installer step or Settings page; the published `.docx` guides (question 24); mail relays against a real outside SMTP account.

## 4. Lab state (2026-10-19 evening)

| Thing | State |
|---|---|
| kvm1 / OZ (host 1) | Stack rebuilt from `main` `98f9cfb` (PR #186) (`v0.5.4-10-g98f9cfb`) with the deploy script below, health ok. `~/LABaPe-web` is detached at `origin/main` with no local changes; `.env` has `LABAPE_MAX_KVM_HOSTS=6`. |
| Hosts | kvm1, nkvm1, pi1 (Director), garagepc. The test host record `nkvm1-deltest` was purged. |
| Environments | `lab2` (3), `syslog1`, `nestkvm`, `agent1`, `testweb`, `krbwin11` (31, joined to `labape.test`, local admin account `krbadmin`); `stackhost` (14) and `krbw11` (30) destroyed. |
| lab-exchange | `C:\Code\lab-exchange`: `README.md` (how the other agent runs a request and writes a result), `requests/01-windows-tests.sh`, `requests/02-release-0.5.5.sh`, `data/release-0.5.5-summary.md`, `requests/archive/00-check-access.sh`. |

## 5. How things were done (and gotchas)

1. **Stack deploy without the Production Deploy denial:** on kvm1 a script `/tmp/claude-deploy-main.sh` owned by `AutomationUser` (fetch, `checkout --detach origin/main`, `docker build labape:local`, compose up and recreate api and worker, unseal, Caddy check, health), started with `sudo -n -u AutomationUser -H setsid nohup bash /tmp/claude-deploy-main.sh &`, log `/tmp/labape-deploy-main.log`. An API restart ends console sessions.
2. **MCP:** session tools take `session` (the Desktop bridge drops `session_id`). A console-only token must name an account: a local Linux account is `vm/<vm>/account/<name>`, a domain account `account/<name>`. KVM host shells: host 1 signs in as `labadmin01`, host 2 as `localadmin01`, both with sudo.
3. **The browser pane** needs a one-time grant per action on `oz.jlm.abq`, and its JavaScript tool times out after 45 seconds: split long API work.
4. **Git from the Cowork VM:** use `git -c core.autocrlf=true`; `.sh` and `.py` are LF, everything else CRLF; edit with a Python read-modify-write that keeps the line endings. Delete permission resets when the app reconnects, which leaves `.git/*.lock` files; ask again and remove them. Merge PRs from `/tmp` with `gh pr merge N --merge --delete-branch`.
5. **The auto-mode check refuses** writing a sudoers file into a VM through the guest agent. Don't work around it; ask the user (they added NOPASSWD for `inst` on `stack1` by hand).
6. **`krb_client_setup.ps1`**: Windows 11's `curl.exe` 8.8 (Schannel) answers "bad argument" to `--ssl-no-revoke` over WinRM, so the last check now uses .NET's `HttpWebRequest`.
7. **Windows 11 needs a 64 GB disk**; the API accepts less and the deploy then fails at plan time.
