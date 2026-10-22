# Helper scripts

Scripts for working on LABaPe that get used more than once: shipping a
change, putting a working tree on the lab host's web stack, and live tests
against that stack. Add a script here as soon as it's needed a second time,
and describe it below. One-off scripts don't belong here.

They run from Git Bash on the Windows development machine (or any Linux
shell) and reach the lab host over SSH.

## Settings

The scripts that talk to the lab host read these environment variables
(through `lab.sh`). This lab's values are in `Live-Testing.local.md`; don't
write them into tracked files.

| Variable | Meaning | Default |
|---|---|---|
| `LABAPE_LAB_SSH` | `user@host` of the lab host | required |
| `LABAPE_LAB_KEY` | SSH private key for it | ssh's own defaults |
| `LABAPE_LAB_URL` | The web interface's URL | `https://<host>` |
| `LABAPE_LAB_REPO` | The stack's git clone on the lab host | `~/LABaPe-web` |

For example, in `~/.bashrc`: `export LABAPE_LAB_SSH=user@labhost LABAPE_LAB_KEY=~/.ssh/lab_key`.

## Scripts

| Script | Runs | What it does |
|---|---|---|
| `ship.sh` | locally | Commit, push, open a PR, merge, update `main` |
| `authentik_groups.py` | locally → Authentik | Create LABaPe's role groups (including `labape-ca-admins`, `labape-ca-requesters`, `labape-ca-auditors`, `labape-console`) that are missing, with an Authentik token that may create groups (`AUTHENTIK_URL`, `AUTHENTIK_TOKEN`; `--dry-run` first). Adds no members |
| `lock-python.sh` | locally | Refresh `container/requirements.txt`, the hash-pinned list of every Python package the worker image installs (`--upgrade` moves them all to the newest allowed). Needs uv. Then rebuild (`deploy-web.sh`), test, refresh the SBOM |
| `sbom.py` | locally | Write the software bill of materials: `Claude_Docs/Reference_SBOM.md` and `sbom/labape.cdx.json` (CycloneDX). `--check` says whether they're stale; a test fails when they are |
| `sbom_snapshot.py` | locally → lab | Take the worker image's inventory (`sbom/image-snapshot.json`) over SSH (`LABAPE_LAB_SSH`); `sbom_probe.py` is the part that runs inside the worker. `release.sh` runs it |
| `release.sh` | locally | (The newest handoff must have changed since the previous release and carry a `**Latest release:**` line, which the release stamps; `--skip-handoff` overrides the first check.) (It also builds the AI files and attaches them to the GitHub release: `labape-mcp-X.Y.Z.mcpb`, the Claude Desktop bundle for the MCP server, and `labape-skill-X.Y.Z.zip`, the skill; a dry run builds them too, and a test keeps the skill naming every MCP tool and role.) (`--resume` finishes a run that stopped after the release PR merged: the signed tag and the GitHub release. A run takes over ten minutes: start it in the background.) Cut a release: CHANGELOG section, versions, docs wording, the SBOM (needs the lab's worker image built from this clean main: `deploy-web.sh` first), optional `.docx` guides, then ship, signed tag and GitHub release with the SBOM attached (`release_prep.py` does the file changes) |
| `build_mcpb.py` | locally | Build the Claude Desktop bundle `dist/labape-mcp-<version>.mcpb` (manifest, a launcher and `app/labape/mcp.py`); Desktop asks for the address and token on import. Needs only Python |
| `deploy-web.sh` | locally → lab | Build and run this working tree on the lab's web stack |
| `lab-live.sh` | locally → lab | Run one of the `live/` scripts on the lab host |
| `lab.sh` | sourced | Shared settings for the two above |
| `live/oidc_login.py` | lab (container) | Sign in through the test Authentik; imported by the others |
| `live/env_create.py` | lab (container) | Create a one-VM environment (optionally with catalog applications, `--apps`, and services, `--services`) and wait for its deploy |
| `live/env_plan.py` | lab (container) | Preview a redeploy of an environment (design §56.1): queues the plan job, waits, and prints each VM's action (reinstalled, created, removed or kept) with the reasons. Nothing is applied |
| `live/disk_detail.py` | lab (container) | A drive's page data (design §64): current SMART numbers, readings in the history, when they moved, open alerts; `read` reads the host's drives first |
| `live/certs.py` | lab (container) | Certificates round trip with the real platform CA (sign a 1-day test certificate, download, revoke) and the register; `cleanup` revokes the leftover tests |
| `live/env_job.py` | lab (container) | Deploy, refresh or destroy an environment and wait |
| `live/env_secrets.py` | lab (container) | An environment's secrets on its page: list, or a store/replace/reveal/delete round trip (V1a) |
| `live/env_secrets_move.py` | lab (container) | Move an environment's secrets to another store and check they read the same |
| `live/openbao_people.py` | lab (container) | People in OpenBao (V1b): sync now, or sign a test user in with OIDC and try reads and writes (statuses only) |
| `live/desktops.py` | lab (container) | xrdp/VNC on an environment's Linux host groups (E3) |
| `live/host_files.py` | lab (container) | A KVM host's files path and free-space alert; change the path and wait for the move job |
| `live/host_pools.py` | lab | Every host's drive pools as the page sees them (`refresh` reads the facts first); read-only |
| `live/host_power.py` | lab | `info` a host's pending OS updates (read-only); `update` / `reboot` start real jobs |
| `live/host_logs.py` | lab | Which hosts forward their own logs to the stack syslog; `on`/`off` HOST_ID |
| `live/alert_to.py` | lab | Settings → Monitoring "Email alerts to": show or set |
| `live/stack_mail.py` | lab (container) | The stack's own Mailpit: status, the admin-only route, a test email, its inbox, forwarding through a server (`relay`) or off |
| `live/mail_check.sh` | lab host | The environment's mail server: send a test message over STARTTLS and read it back through Mailpit's API |
| `live/package_updates.py` | lab (container) | Catalog → Updates: list the sources, or check (all or one) and wait |
| `live/vm_secrets.py` | lab (container) | V2: store a shared secret for chosen VMs or a VM's own (random values, hashes printed), delete, sync |
| `live/job_log.py` | lab (container) | A finished job's log, or only one host's lines with their task names |
| `live/env_password.py` | lab (container) | Show or rotate an environment's Windows passwords |
| `live/rotate_agent.py` | lab (container) | Rotate a secret through the update agent |
| `live/rotate_r1.py` | lab (container) | Rotate the worker-run secrets end to end |
| `live/rotate_oidc.py` | lab (container) | Rotate the OIDC client secret end to end |
| `live/stores.py` | lab (container) | Check the stack's secret stores (Test connection, dashboard) |
| `live/stores_move.py` | lab (container) | Move real secrets between the stores and check them |
| `live/stores_rotate.py` | lab (container) | Rotate LABaPe's logins to its stores |
| `live/console.py` | lab (container) | Console API and MCP round trip with a throwaway console token: reach, exec (Linux and Windows), timeout and output cap, a shell and a serial session, `/mcp`, another environment refused, revoke |
| `live/catalog_platforms.py` | lab (container) | List the catalog's items of a kind with their status, OS families and (packages) members |
| `live/syslog_settings.py` | lab (container) | Point Settings → Syslog at a collector and send the test message |
| `live/jobs.py` | lab (container) | List jobs with the Jobs page's filters, as any test user |
| `live/files.py` | lab (container) | List the Files tab (installers with their OS targets) |
| `live/power.py` | lab (container) | VM power and start order for an environment |
| `live/remote_session.py` | lab (container) | Open an SSH/RDP session or a VM console through the tunnel like the browser does; toggle recording; list sessions |
| `live/host_exec.sh` | lab host | Run a command on a registered host as its LABaPe access account (sudo) |
| `live/vm_exec.sh` | lab host | Run a command on an environment's Linux VM as its admin user (the VM key stays in the worker) |
| `live/win_probe.sh` | lab host | Which stored Administrator password a Windows member VM takes (environment, own, pending), and its recent Ansible async results; never prints values |
| `live/hosts.py` | lab (container) | List KVM hosts; enable, disable, rename one or attach a network; turn its log forwarding on or off |
| `live/dashboard_hosts.py` | lab (container) | The KVM hosts the dashboard shows |
| `live/files_ops.py` | lab (container) | Files page through the API: a chunked tus upload, a download from a URL, list, delete |
| `live/relay_host_to_vm.sh` | lab (worker) | Copy a file from a registered host to a test VM through the worker (restore drills); prints only size and hash |
| `live/logo.py` | lab (container) | The logo: `roundtrip` uploads a generated PNG, checks the public URL, removes it |
| `live/placement.py` | lab (container) | Best-fit placement: the ranking for a small Debian environment, or `create NAME` with no host (waits for the deploy) |
| `live/spread.py` | lab (container) | One environment over two hosts (design §45): `create NAME HOME OTHER`, `vms ENV`, `power ENV ACTION`, `job ENV deploy|destroy|refresh`, `log JOB [LINES]` (waits for each job) |
| `live/env_accounts.py` | lab (container) | An environment's own accounts: `ENV [list]`, `set-local NAME ROLES`, `set-domain NAME`, `clear`, `rotate NAME` (passwords masked) |
| `live/quotas.py` | lab (container) | Quotas: list them, or `roundtrip` (limit labape-dev, check the refusal, remove the quota) |
| `live/leases.py` | lab (container) | Environment leases: `ENV [show]`, `days N`, `in-minutes N` (admin's exact end), `clear`; `settings on/off [grace=N] [after=destroy/keep] [remind=7,1]` |
| `live/snapshots.py` | lab (container) | VM snapshots: `ENV VM [list]`, `refresh`, `auto on/off`, `take NAME`, `revert NAME`, `delete NAME` (waits for each job) |
| `live/secret_health.py` | lab (container) | Secret health (S1): `list`, or `check all\|ID ...` (Check now, waits, prints status and recoverable) |
| `live/break_password.py` | lab (worker container) | Recovery tests: gives one Windows account of an environment an unknown password (`ENV_ID KIND`, KIND windows_admin, windows_admin2 or domain_admin2; run with `docker exec -i labape-labape-worker-1 /opt/labape/venv/bin/python - ENV_ID KIND < break_password.py`) |
| `live/tokens.py` | lab (container) | API tokens: list them, `roundtrip` (make, use, revoke a throwaway token; the secret is never printed), or `scrape` (`/api/metrics` with a throwaway auditor service token) |
| `live/links.py` | lab (container) | Linked stacks: list, `invite` (prints only the code), `join CODE`, `poll ID`, `remove ID`, `overall` (the dashboard's All stacks), `signon ID on\|off` (sign-on from that stack, §47.2; waits for the agent) |
| `live/linked_login.py` | lab (container) or the linked stack's host | Sign in to a stack's LABaPe with a linked stack's account (design §47.2): `SOURCE_NAME USER`; `LABAPE` the stack, `AK_ENV` the linked stack's Authentik `.env` |
| `live/stack_backup.py` | lab (container) | Full-stack backups: list them (and what went to, or came from, linked stacks), or `run [--wait]` one through the update agent |
| `live/monitoring.py` | lab (container) | Open and cleared alerts, silences, and each stack host's checks; `ack`/`unack`/`silence`/`end` |
| `live/syslog_stack.py` | lab (container) | The stack's syslog collector: status, messages, test senders, forwarding test, settings |
| `live/vm_details.py` | lab (container) | A VM's details page data; read its accounts and groups; an account round trip |
| `live/host_access.py` | lab (container) | KVM host administration: register, set up access, show sections, manage accounts, install |
| `live/host_access_vm.sh` | lab (host) | Test Set up access against a LABaPe VM with a throwaway password |
| `live/templates.py` | lab (container) | Show Settings → Templates; delete a template and wait for the job |
| `live/maintenance.py` | lab (container) | Turn maintenance mode on, check it, turn it off |
| `live/update_api.py` | lab (container) | Show the update API's view; offer it a tag |
| `live/agent-request.sh` | lab (host) | Send the update agent a request and wait |

### ship.sh

```bash
tools/helpers/ship.sh "<PR title>" <commit-message-file> <pr-body-file>
```

Run on a `YYYY-MM-DD-<topic>` branch, and only for a change you've been asked to ship. It:
1. stages everything (`git add -A`);
2. refuses to go on if a sensitive-looking file is staged: `.env`, `secrets/`, `*.vault.yml`, `*.local.md`, installer state or answers files;
3. refuses if an added line looks like a secret: *password* or *secret*, then `=` or `:`, then 16 or more letters and digits. `ThisIsMy_FAKE...` placeholders are allowed. The check also catches one variable assigned to another, such as a `domain_password` variable set from a `windows_admin_password` variable. If it stops on a line like that, check the line by eye and commit by hand;
4. commits with the message file, pushes, opens a PR into `main` with the body file, merges it with `--delete-branch` (the branch goes locally and on GitHub), then checks out and pulls `main` and prunes stale remote-tracking refs.

Commit messages end with a `Co-Authored-By:` line and PR bodies with the Claude Code line, when Claude writes them.

### deploy-web.sh

```bash
tools/helpers/deploy-web.sh
```

Copies the working tree, uncommitted and untracked files included, into the lab's stack clone. It then builds `labape:local` stamped with `git describe` (this build is also the web UI's type-check), recreates the API and worker, and prints `/api/health`. If the build fails, it prints the build's errors.
- Don't run it while jobs are running: recreating the worker kills them. Check with `docker compose exec labape-api labape maintenance status` (`running_jobs`).
- It leaves the lab clone dirty. Once the change is merged, reset the clone and reinstall the agent there, because the agent runs from a copy:

  ```bash
  git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main
  ~/LABaPe-web/deploy/update-agent/install.sh
  ```

  Resetting before the merge would put the old agent back if the change touched it.

### lab-live.sh

```bash
tools/helpers/lab-live.sh <script> [args...]
tools/helpers/lab-live.sh env_password.py 3 list
```

Copies `live/` to `~/labape-test-tools` on the lab host, then runs the script.
- A `.py` script runs in a throwaway `labape:local` container on the host network, so it needs no Python packages on the host. The test Authentik's `.env` is mounted read-only; the scripts read the test users' password from it and never print it.
- A `.sh` script runs on the lab host itself.

The `live/` scripts sign in as the test Authentik's `labape-admin` (and `labape-dev`), so they need `tools/authentik-test/` running on the lab host. Each one's docstring repeats its usage.

| Script | Usage | Notes |
|---|---|---|
| `oidc_login.py` | `oidc_login.py <user>` | Signs in and prints `/api/auth/me`. The others import `LABAPE`, `ENV`, `login()` and `job_log()` (a finished job's log, parsed from the event stream) from it. |
| `env_create.py` | `env_create.py list` or `<name> <os key> <template> [--vm NAME] [--cpu N] [--mem MB] [--disk GB] [--network NAME] [--addressing static\|dhcp] [--apps A,B] [--services S,T]` | `list` shows hosts, their templates and networks, and the environments. Otherwise it creates the environment as `labape-admin` (or `--as` user), waits for the deploy and prints the VM's address and the job's summary lines. |
| `env_job.py` | `env_job.py <env id> <deploy\|refresh-addresses\|destroy>` | Waits, then prints the result, duration, and the `labape:`, `fatal:` and PLAY RECAP lines. **destroy deletes the VMs.** |
| `job_log.py` | `job_log.py <job id> [host]` | The whole log, or one host's lines under their `TASK [...]` headers. |
| `env_password.py` | `env_password.py <env id> list` or `<env id> <windows_admin\|domain_admin>` | Lists the environment's rotation policies, or rotates one. It prints the credentials sheet's labels, never its values. |
| `rotate_agent.py` | `rotate_agent.py <kind>` | Kinds: `master_key`, `postgres_password`, `ca_password` (these restart the stack) and `vault_password`, `bootstrap_ssh_key`. Afterwards it checks the store's self-test, directory search, maintenance mode and health. |
| `rotate_r1.py` | `rotate_r1.py` | Adds the test rotator token as an elevated account, then rotates the directory token (checking the old key now gets 403), the data key and the worker key. Needs the test tokens in Authentik's `.env`. |
| `rotate_oidc.py` | `rotate_oidc.py` | Adds the rotator token as an elevated account if it's missing, rotates the OIDC client secret, then checks the token endpoint now refuses the old secret (`invalid_client`) and a fresh sign-in works. Afterwards `LABAPE_TEST_CLIENT_SECRET` in `.env` is out of date. |
| `stores.py` | `stores.py` | Runs Test connection for each enabled store (the stack's OpenBao and Conjur, design §26) and prints the dashboard's store checks. Never sees a login: LABaPe owns and rotates them. |
| `stores_rotate.py` | `stores_rotate.py [openbao\|conjur ...]` | Rotate now on each store's login policy, waits for the job, prints its `labape:` lines, then runs Test connection. |
| `stores_move.py` | `stores_move.py [environment]` | Needs `stores.py` first. Moves the directory token built-in → OpenBao → Conjur → built-in, running group search after each move, moves an environment's credentials sheet (default `lab2`) to OpenBao and back comparing the sheet's hash, and sets the default store to OpenBao and back. |
| `remote_session.py` | `vm ENV VM ssh\|rdp\|serial\|vnc`, `host HOST ssh`, `record on\|off`, `sessions` | Reports the opcodes guacd sent (and any error); acknowledges frames; sends no keystrokes. `serial` and `vnc` are the VM's consoles through its KVM host. |
| `syslog_stack.py` | `status`, `messages [filter=value ...]`, `send ADDRESS`, `forwardtest ADDRESS udp\|tcp\|tls`, `settings [...]` | `send` puts one tagged message over UDP, TCP and TLS without a client certificate (refused while certificates are required); `forwardtest` listens on ADDRESS:5514, adds a forward to it, sends a message and waits for the copy, then removes the forward. |
| `vm_details.py` | `show ENV VM`, `accounts ENV VM`, `roundtrip ENV VM` | `roundtrip` adds a test account and group, adds the member, locks, unlocks, sets the password (the fake placeholder), deletes both, and checks a protected delete is refused and the password stays out of the audit log. Use a throwaway VM. |
| `host_access.py` | see its docstring | `setup` and `install` read the password or answers on stdin. |
| `host_access_vm.sh` | `host_access_vm.sh <vm address> <host id> <web URL>` | Sets a random password for the VM's `labape` user, feeds it to `host_access.py setup` on stdin, then locks it again. Never prints it. |
| `power.py` | `power.py <env id> vms`, `<env id> <start\|shutdown\|restart\|force_off> [--vm NAME ...] [--force-after] [--timeout S]` or `<env id> order GROUP=ORDER[:agent\|none[:DELAY]] ...` | Waits for the job and prints its `labape:` lines. **Shut down and force off stop VMs.** |
| `files.py` | `files.py [isos\|installers\|files] [--targets]` | Name, size, SHA-256 prefix; installers with their targets, ISOs with their OS. `--targets` prints the vocabulary. |
| `jobs.py` | `jobs.py [--as USER] [--facets] [filter=value ...]` | Filters: `environment` (name contains), `type` (prefix), `state`, `requested_by`, `since`, `until`. Shows how visibility and filters combine for that user. |
| `syslog_settings.py` | `syslog_settings.py show` or `<host> [--port N] [--protocol udp\|tcp\|tls] [--facility F] [--no-verify] [--off]` | Saves Settings → Syslog (enabled, TLS with verification by default), then runs Send test message. Check the collector's `/var/log/remote/all.log` for it. |
| `maintenance.py` | `maintenance.py on\|check\|off` | `check`: a developer's write gets 503, a read works, and Back up now is refused. |
| `update_api.py` | `update_api.py [tag]` | Shows the current version, releases, standby version and agent state. With a tag, offers it to the API, which refuses unpublished tags. |
| `agent-request.sh` | `agent-request.sh <update\|rollback> [tag]` | Writes the agent's request file directly and waits up to 20 minutes. Then it shows the state, running version, checkout, standby version, maintenance mode and the log tail. |

### Kerberos proof (2026-10-16)

| Script | Runs | What it does |
|---|---|---|
| `live/ad_run.py <env> <host> <script.ps1> [secret [user]]` | lab (container) | Run a PowerShell file on a Windows VM over WinRM with the environment's revealed password (default the DC as Administrator); `@@UTCNOW@@` in the script becomes the real UTC time |
| `live/ad_inspect.ps1`, `krb_time.ps1` | on the DC | Read-only: domain, DNS, users; clock and the service account's key version |
| `live/krb_dc_setup.ps1` | on the DC | DNS name for Authentik, service account with the `HTTP/` SPN, keytab (random password, AES256) |
| `live/krb_fix_clock.ps1` | on a Windows VM | Set the time zone to Pacific and the real time (libvirt's localtime clock vs the guests' UTC zone) |
| `live/krb_source.py <env> <DC>` | lab (container) | Create or update (`--remove` deletes) the test Authentik's Kerberos source `lab2-kerberos`; reads the keytab from the DC and deletes it there |
| `live/krb_negotiate.py <env> <DC> <secret> <user>` | lab (container) | Sign in as an AD user through SPNEGO (`curl --negotiate` in a scheduled task) and finish Authentik's enrollment flow |
| `live/krb_schema.py`, `krb_users.py`, `ak_admin.py` | lab (container) | Read-only: the Kerberos source's fields; users the source created; the akadmin client the others import |
| `live/krb_ldap.py <env> <DC> [--remove]` | lab (container) | AD groups `lab2-*` and a bind account on the DC; Authentik's LDAP source `lab2-ad` (users and groups) beside the Kerberos source |
| `live/krb_groups.py`, `krb_mappings.py` | lab (container) | Read-only: Authentik's groups with their members and the LDAP sync status; the LDAP property mappings |
| `live/krb_negotiate.py ... --labape https://name` | lab (container) | After the SPNEGO sign-in, sign in to LABaPe on that name and print `/api/auth/me` (user, roles, groups) |
| `live/role_bind.py list\|add GROUP ROLE` | lab (container) | LABaPe role bindings for groups, through the API |
| `live/stack_names.sh [add\|remove NAME]` | lab host | The stack's names (`LABAPE_ALT_NAMES`): show, add or remove one, re-sign the certificate, sync Authentik's redirect URIs, recreate the API |
| `live/sync_redirects.sh` | lab host | Run `tools/authentik-test/sync-redirects.sh` for the stack's current names and show the result |
| `live/tz_check.ps1` | on a Windows VM | The VM's zone, UTC and local time, and `RealTimeIsUniversal` (design §57) |
| `live/krb_client_setup.ps1` | on a domain-joined Windows VM | Trust the platform root CA, put the stack's name in the Local intranet zone and allow it for Kerberos in Edge and Chrome (run it with `ad_run.py <env> <vm address> krb_client_setup.ps1 vm/<vm>/windows_admin`); `krb_negotiate.py` takes `--winrm-secret/--winrm-user` to run its test from that VM |
