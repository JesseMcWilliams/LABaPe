# Handoff, 2026-10-16

> **Stage: Planning.** Where LABaPe stands after v0.5.1 and the work since, for whoever picks it up next (a new
> Claude Code session or a developer). Read this first, then the documents it points to. LABaPe only: aPeDiscovery,
> LogaPe, aPeSecrets and LABaPe are separate projects with their own handoffs. It follows
> [Planning_Handoff-2026-10-15.md](Planning_Handoff-2026-10-15.md), which still describes the lab and the rules.

## Latest (end of 2026-10-16): v0.5.3 released

`main` is at the merge of PR #164 (the one after v0.5.3's release commit); tests **390 passing, 1 skipped**; no open PRs, no batch in progress; `[Unreleased]` in `CHANGELOG.md` holds only whatever came after v0.5.3 (nothing yet). **v0.5.2** (Kerberos, time zones, host deletion, the rest of §0 and §2) and **v0.5.3** (below) are released, both signed tags with the SBOM attached.

| What | PR | Where |
|---|---|---|
| **Sign in as**: the Secrets page shows the user name to type for each account (`L2WIN1\Administrator`, `LABAPE\jesse`) with a copy button; the credentials sheet shows the Windows local Administrator as `.\Administrator`, adds a user name above each per-VM password and masks those passwords; the `.rdp` file names the VM (or the domain on a controller). Cause: people typed the secret's name (`windows_admin`) as the user name, and a bare `Administrator` is completed with the client's own domain. | #162 | `remote.login_of`, `api/env_secrets.py`, `api/environments.py` `sheet_hints`, `EnvSecrets.tsx`; Testing_Troubleshooting-Log.md "Signing in to a domain member's local account" |
| **Browser RDP blank window** on a reconnected session: the page asks for its size again once connected and scales a bigger session to fit. The user confirmed it works. | #162 | `RemoteSession.tsx` |
| **`release.sh --resume`**: finishes the signed tag and the GitHub release when a run stopped after the release PR merged. The v0.5.3 run took over ten minutes and was cut off by the terminal's time limit, so its tag and release were made by hand; start a release in the background. | #164 | `tools/helpers/release.sh` |

**To do:**
1. **Update Director** (`pi1`) to v0.5.3 with its Admin → Updates click (it is still on v0.5.1).
2. A real-browser click-through of Windows sign-on on `l2win1` (§0); an installer step or Settings page for its domain side.
3. Published `.docx` guides (question 24, later). Hyper-V is dropped (question 175).

**Lab:** `~/LABaPe-web` was built from clean `main` for the v0.5.3 SBOM, so it matches `main` (reset it to `origin/main` after any later `deploy-web.sh` of a working tree). Everything else is as in §0 and §5.

**Gotchas added:** `release.sh` needs a worker image built from this clean `main` and the lab reachable (`deploy-web.sh` first); `--dry-run` checks both. A Windows `Administrator` typed bare on a domain member goes to the wrong domain: use `COMPUTER\Administrator` or `.\Administrator`.

## 0. Update, later on 2026-10-16: Kerberos, time zones, Hyper-V dropped

`main` is at the merge of PR #158 or later; tests **387 passing, 1 skipped**. Merged since the state in §1: PR #157 (below), #158 (D-number note in CLAUDE.md). aPeTemplate PR #8 (the user's personal numbering rules: Q/A for chat, D for document items, reset after 100) is merged too. Nothing is in progress. **v0.5.2 was released** (signed tag on the merge of PR #160; the SBOM is attached) and holds all of this and PRs #151-#154; `[Unreleased]` in `CHANGELOG.md` is empty again. Director (`pi1`) is still on v0.5.1 and needs the Update click.

| What | Where |
|---|---|
| **Environment time zone** (browser's by default; UTC hardware clock for new VMs; Ansible sets the zone, and on Windows the "clock is UTC" flag and a clock fix). Fixes Windows VMs running 7 h behind real time. | Design §57; `app/labape/timezones.py`, `tofu/timezones.json` |
| **Sign-in on the name used**: the OIDC redirect and the trip to Authentik follow the stack's name the browser used (`LABAPE_ALT_NAMES`); `sync-redirects.sh` keeps Authentik's redirect addresses in step. | Design §58; `app/labape/auth/oidc.py` |
| **Windows sign-on (Kerberos)**: Authentik Kerberos source (SPNEGO) + LDAP source for AD users and groups; role bindings map AD groups. The domain side is a manual recipe of helper scripts. | Design §58; `tools/helpers/README.md` "Kerberos proof" |
| **Hyper-V will be dropped** (question 175): docs say so; no code removed, removal not scheduled. | Planning_Questions.md 175; banners across the docs |

**Left from this round:** a click-through in a real browser on `l2win1` (setup is scripted: `krb_client_setup.ps1`); an installer step or Settings page for the domain side (DNS name, service account and keytab, the two Authentik sources, role bindings); Windows VMs created before §57 keep their skewed clock until recreated.

**Lab state added today** (the details of §5 still apply):
1. The stack's names are `172.21.20.11` (main), `OZ`, `OZ.jlm.abq` and **`auth.labape.test`** (`LABAPE_ALT_NAMES` in `container/.env`); one certificate covers them; Authentik's LABaPe provider has a redirect address for each. `~/LABaPe-web` was reset to `main` at the PR #157 merge, so it is **clean** again (supersedes the "dirty" note in §5).
2. The domain `labape.test` (`l2dc1`): DNS record `auth.labape.test` → 172.21.49.100; service accounts `svc-authentik` (SPN `HTTP/auth.labape.test`) and `svc-authentik-ldap`; AD groups `lab2-admins` (with `jesse`), `lab2-deployers`, `lab2-viewers`. **`l2dc1` and `l2win1` run on Pacific time with the real clock** (set by hand, `krb_fix_clock.ps1`).
3. The test Authentik has the sources `lab2-kerberos` ("Sign in with Windows", promoted) and `lab2-ad` (LDAP), a property mapping "labape: AD group name", and the synced AD users (`jesse` is Domain Admins + `lab2-admins`).
4. LABaPe role bindings: group `lab2-admins` → admin, `lab2-deployers` → deployer, `lab2-viewers` → viewer, `LABAPE.TEST` → viewer.
5. `l2win1` trusts the platform root CA, has `auth.labape.test` in the Local intranet zone and the Edge/Chrome `AuthServerAllowlist` policy.
6. The test environments `tzlin` and `tzwin` were destroyed. Environments `lab2`, `syslog1`, `nestkvm` as before.

**Gotchas from today:**
1. Kerberos needs the full host name (the SPN) and clocks within 5 minutes: check both first when a ticket is refused ("Clock skew too great", "cannot decrypt ticket").
2. `ktpass`: no `-setupn` (it rewrites the account's UPN and breaks the key's salt); use `-mapOp set`. It writes its success line to stderr.
3. `Start-Process -Credential` fails over WinRM (0xC0000142); run as a user with a scheduled task (`krb_negotiate.py`).
4. A group with the same name in LDAP and Authentik merges without its AD members (`name_link`): the lab's AD groups are named `lab2-*` and matched by identifier.
5. `git archive` (deploy-web) drops the execute bit: call scripts with `bash`, and test with `-f`, not `-x`.
6. Python in a Bash heredoc turns `\a`/`\n` in strings into control characters (a registry path got a bell): use the Write tool for anything with backslashes, and grep for the result.
7. The platform CA publishes no revocation list; `curl` on Windows needs `--ssl-no-revoke` against its certificates.

## 1. State in one paragraph (as of the first part of 2026-10-16)

`main` was at `ebc96ac` (PR #154) when this was first written, no open PRs, tests **379 passing, 1 skipped** (`pytest app/tests`).
**v0.5.1** is released (signed tag, release PR #150, SBOM attached). Four changes merged after it and were
released in v0.5.2 (PRs #151-#154): the rotators' TLS fix with elevated-account health, the installer's
own-Authentik-login prompt, deleting a KVM host, and the redeploy documentation. The user then asked for v0.5.2 and it was cut (see §0). No batch is in progress.

## 2. What was built this session (since v0.4.0)

Design sections are in [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md).

| Area | PRs | Design | Released in |
|---|---|---|---|
| SBOM, release script regenerates it, AGPL-3.0-only license, locked Python dependencies | #134-#137 | §51 | v0.5.0 |
| The stack's own mail server, Mailpit: Admin → Mail, relay settings, default Email/alert settings, `/mail/` behind admin sign-in | #138, #139 | §52 | v0.5.0 |
| Update-agent fixes: a changed Caddyfile reaches Caddy; installer files (`compose.override.yaml`) no longer make the checkout "dirty"; OpenBao is unsealed after the stack comes up | #141, #142 | §52, §53 | v0.5.0/v0.5.1 |
| Audit log and User errors paging | #143 | | v0.5.1 |
| H2: KVM host Update and Reboot as jobs (a reboot waits for the host's jobs) | #144 | §53.1 | v0.5.1 |
| S4: every stack container logs to the stack syslog (Docker syslog driver, UDP, rfc5424micro); Podman/no-collector installs log locally | #145 | §53.2 | v0.5.1 |
| C1: **Admin page** button per container on the Stack section | #146 | §53.3 | v0.5.1 |
| D1: **Drive Pools** (mdadm, ZFS, LVM): state, progress, alerts, safe actions | #147 | §53.4 | v0.5.1 |
| The Authentik administrator (akadmin) password in the secret store: check by signing in, rotate, **Reveal** | #148 | §54 | v0.5.1 |
| Rotators trust the platform CA; elevated accounts get a weekly check and are restored with akadmin | #151 | §54.1 | v0.5.2 |
| Installer asks for your own Authentik login; `create-admin.sh` | #152 | §54.2 | v0.5.2 |
| Delete a KVM host: soft delete, restore, purge after a retention period (migration 0029) | #153 | §55 | v0.5.2 |
| What a redeploy keeps and when it reinstalls (confirmation, user guide) | #154 | §56 | v0.5.2 |

## 3. What's left

| Item | Where | Note |
|---|---|---|
| Update Director to v0.5.3 | Director's Admin → Updates | The user's click; v0.5.3 is released. The next release: only when the user says. |
| Director's rotation account | Director's Secrets page | The user is to add the elevated account `authentik-rotator` (token `LABAPE_TEST_ROTATOR_TOKEN` in Director's `tools/authentik-test/.env`) and choose it on the akadmin, directory-token and OIDC rows; steps were given in chat. Until then Director's Authentik rotations are reminders. |
| Redeploy plan preview | design §56 | A list of VMs a redeploy would replace (an OpenTofu plan before applying) is not built; the user was asked, no answer yet. |
| Drive Pools actions on real hardware | design §53.4 | Never run on kvm1's RAID10 `md10` (read-only view only); the user was asked, no answer yet. |
| Published `.docx` guides | Planning_Backlog.md row 18; question 24 | "Later" by the user's choice. (Kerberos is built, §58; Hyper-V parity is dropped, question 175.) |

Follow-ups noticed, not asked for:
1. **Fresh installs keep akadmin's first password in `.env`** until `labape authentik-admin-import` runs: `Stack.Auth` runs before `Stack.Up`, so `deploy.sh`'s import into the secret store is best-effort and usually can't reach the API yet. A later installer step (after `Stack.Up`) should import it and blank the line.
2. The installer's own-login prompt is covered by unit tests and `create-admin.sh` was run live on OZ, but a full installer run with the prompt was not done.
3. The **Admin page** popups, the Drive Pools page, the Update/Reboot dialogs and the Deleted hosts list were type-checked by the image build but not driven in a browser.
4. OZ's `oidc_client_secret` row showed health "none" ("not in use") although sign-in is through OIDC; not investigated.
5. The lab is at its four-host limit (`LABAPE_MAX_KVM_HOSTS`), so host deletion was not exercised end to end live (tests cover it).
6. The lab mail relay (M1) and the stack mail relay (§52) have not been tried against a real outside SMTP account (the user said "not right now").
7. ~90 old merged branches remain on GitHub.

## 4. How the work is done here

- **Read first:** `CLAUDE.md`, this file, [Planning_Questions.md](Planning_Questions.md) (new questions continue from **176**; 175 (Hyper-V dropped) is answered), [Testing_Troubleshooting-Log.md](Testing_Troubleshooting-Log.md).
- **Shipping:** a `YYYY-MM-DD-<topic>` branch first; pytest; a live check on the lab; docs (design section, `CHANGELOG.md` `[Unreleased]`, user guides); `tools/helpers/ship.sh <title> <msg-file> <body-file>` (the commit ends with the `Co-Authored-By` line, the PR body with the Claude Code line); then make the next branch. Releases only when the user says: `tools/helpers/release.sh vX.Y.Z <summary-file>` after `deploy-web.sh` from a clean `main` (it regenerates the SBOM from the lab image).
- **The user prefers numbered lists** for actions, options and questions, and answers by number.
- **Lab:** details are in the gitignored `Live-Testing.local.md` (read it only for live work). `tools/helpers/deploy-web.sh` puts the working tree on kvm1; `tools/helpers/lab-live.sh <script>` runs a script from `tools/helpers/live/` in a throwaway container signed in through the test Authentik, or on the host for `*.sh`; `lab-live.sh host_exec.sh <host id> '<command>'` runs a command on a registered host as its access account. New live helpers this session: `akadmin.py`, `secret_row.py`, `secret_rows.py`, `host_pools.py`, `host_power.py`, `host_logs.py`, `host_delete_test.py`, `alert_to.py`, `stack_mail.py`.
- **Root on lab hosts is the user's.** Commands for the user use absolute paths and `sudo -u AutomationUser ...`. Ask before anything destructive, and **never run a reboot or shutdown on a lab host to test something** (Director was rebooted by accident on 2026-10-15 while testing an inhibitor lock; root ignores such locks).
- **Secrets:** never print, log or commit a value; the placeholder password is `ThisIsMy_FAKE_Password6!`.

## 5. Lab state (2026-10-16)

| Thing | State |
|---|---|
| kvm1 / OZ (host 1) | Main stack from the working tree (`deploy-web.sh`), so `~/LABaPe-web` is **dirty** and a web Update would refuse ("local changes"); before using Admin → Updates there run `git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main` and `~/LABaPe-web/deploy/update-agent/install.sh` over SSH as AutomationUser (not `sudo -u`: it needs the user's systemd bus). akadmin's password was rotated and is in the secret store; the directory token and OIDC client secret were rotated 2026-10-15 with `authentik-rotator`, whose stored key akadmin had to restore. A person-owned Authentik login exists (created by the user). |
| Director (`pi1`, host 3) | **v0.5.1**, healthy. The update agent's copy (`/home/labape/.local/libexec/labape-update.sh`) was replaced by hand with the fixed script. akadmin's password was imported into its secret store and the `.env` line blanked; the rotation account was granted `reset_user_password` on akadmin. Its Email points at its Mailpit; alerts go to `alerts@director.jlm.abq`; its containers (Authentik included) and host logs go to its syslog. The elevated account is not added yet (§3). |
| nkvm1 (host 2), garagepc (host 4) | Unchanged. The host limit (4) is reached. |
| Environments | `lab2`, `syslog1` and `nestkvm` as before (see the 2026-10-15 handoff). |

## 6. Gotchas learned this session

1. **CHANGELOG sections:** an edit that targets the first `### Added` / `### Fixed` lands in an old released section, not `[Unreleased]` (it happened three times, and `release.sh` then says "nothing to release"). Check where an entry went.
2. **Shell escapes:** `\n` in Bash commands and heredocs becomes a real line break; write Python edit scripts with the Write tool. For commands on a lab host, write a script file, base64 it and pass it through `host_exec.sh` (nested quoting broke repeatedly).
3. **A recreated OpenBao starts sealed.** `container/stores/stores.sh unseal` (run as the service user, `sudo -u labape sh -c 'cd /opt/LABaPe/container/stores && ./stores.sh unseal'`) unseals it and starts the stack with its secrets; the update agent and `deploy-web.sh` now run it. A failed update that rolled back "unhealthy" was this.
4. **Authentik API as akadmin** needs the session cookie, the `X-authentik-CSRF` header **and a Referer** over HTTPS; calls to Authentik must verify with `ca.trust()` (system roots plus the platform CA), not plain `verify=True`.
5. **A deleted KVM host is hidden by a query hook** in `db.py` (`do_orm_execute` + `with_loader_criteria`); use `execution_options={"include_deleted_hosts": True}` to see it. New queries need nothing.
6. **Docker's syslog log driver** sends from the daemon: UDP so a missing collector never blocks a container start, `syslog-format: rfc5424micro` so the host name is present. Podman and no-collector installs get a `logging: !override` in the installer's `compose.override.yaml`.
7. **Systemd inhibitor locks don't stop root**: an OS-issued reboot can't be held back by LABaPe (question 174, answered "leave it").
8. **Redeploy** reinstalls a VM when its install recipe changes, including a LABaPe update that edits the answer-file templates (§56).

## 7. Where things are

| Need | Look at |
|---|---|
| How a feature works | Planning_Web-Interface-Design.md (§51-§56 are this session's) |
| Why something broke before | Testing_Troubleshooting-Log.md |
| Open and answered questions | Planning_Questions.md |
| The rotation, health and elevated-account code | `app/labape/engine/rotation.py`, `secret_health.py`, `app/labape/elevated.py`, `authentik_admin.py` |
| Host power, pools, deletion | `engine/hostpower.py`, `hostpools.py`, `hostdelete.py` |
| User-facing behaviour | User_Docs/Web-Interface-Guide.md, Web-Interface-Admin.md |
| The software bill of materials | Reference_SBOM.md, `sbom/` |
| Release history | CHANGELOG.md, GitHub releases (v0.5.1 is the latest) |
