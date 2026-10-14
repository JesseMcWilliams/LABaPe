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
| `deploy-web.sh` | locally → lab | Build and run this working tree on the lab's web stack |
| `lab-live.sh` | locally → lab | Run one of the `live/` scripts on the lab host |
| `lab.sh` | sourced | Shared settings for the two above |
| `live/oidc_login.py` | lab (container) | Sign in through the test Authentik; imported by the others |
| `live/env_create.py` | lab (container) | Create a one-VM environment (optionally with catalog applications, `--apps`, and services, `--services`) and wait for its deploy |
| `live/env_job.py` | lab (container) | Deploy, refresh or destroy an environment and wait |
| `live/env_password.py` | lab (container) | Show or rotate an environment's Windows passwords |
| `live/rotate_agent.py` | lab (container) | Rotate a secret through the update agent |
| `live/rotate_r1.py` | lab (container) | Rotate the worker-run secrets end to end |
| `live/rotate_oidc.py` | lab (container) | Rotate the OIDC client secret end to end |
| `live/stores.py` | lab (container) | Check the stack's secret stores (Test connection, dashboard) |
| `live/stores_move.py` | lab (container) | Move real secrets between the stores and check them |
| `live/stores_rotate.py` | lab (container) | Rotate LABaPe's logins to its stores |
| `live/syslog_settings.py` | lab (container) | Point Settings → Syslog at a collector and send the test message |
| `live/jobs.py` | lab (container) | List jobs with the Jobs page's filters, as any test user |
| `live/files.py` | lab (container) | List the Files tab (installers with their OS targets) |
| `live/power.py` | lab (container) | VM power and start order for an environment |
| `live/remote_session.py` | lab (container) | Open an SSH/RDP session or a VM console through the tunnel like the browser does; toggle recording; list sessions |
| `live/host_exec.sh` | lab host | Run a command on a registered host as its LABaPe access account (sudo) |
| `live/vm_exec.sh` | lab host | Run a command on an environment's Linux VM as its admin user (the VM key stays in the worker) |
| `live/hosts.py` | lab (container) | List KVM hosts; enable, disable, rename one or attach a network; turn its log forwarding on or off |
| `live/dashboard_hosts.py` | lab (container) | The KVM hosts the dashboard shows |
| `live/files_ops.py` | lab (container) | Files page through the API: a chunked tus upload, a download from a URL, list, delete |
| `live/relay_host_to_vm.sh` | lab (worker) | Copy a file from a registered host to a test VM through the worker (restore drills); prints only size and hash |
| `live/stack_backup.py` | lab (container) | Full-stack backups: list them, or `run [--wait]` one through the update agent |
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
