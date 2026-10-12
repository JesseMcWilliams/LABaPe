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
| `live/env_create.py` | lab (container) | Create a one-VM environment and wait for its deploy |
| `live/env_job.py` | lab (container) | Deploy, refresh or destroy an environment and wait |
| `live/env_password.py` | lab (container) | Show or rotate an environment's Windows passwords |
| `live/rotate_agent.py` | lab (container) | Rotate a secret through the update agent |
| `live/rotate_r1.py` | lab (container) | Rotate the worker-run secrets end to end |
| `live/rotate_oidc.py` | lab (container) | Rotate the OIDC client secret end to end |
| `live/stores.py` | lab (container) | Connect and exercise the test OpenBao and Conjur |
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
4. commits with the message file, pushes, opens a PR into `main` with the body file, merges it, then checks out and pulls `main`.

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
| `env_create.py` | `env_create.py list` or `<name> <os key> <template> [--vm NAME] [--cpu N] [--mem MB] [--disk GB] [--network NAME] [--addressing static\|dhcp]` | `list` shows hosts, their templates and networks, and the environments. Otherwise it creates the environment as `labape-admin` (or `--as` user), waits for the deploy and prints the VM's address and the job's summary lines. |
| `env_job.py` | `env_job.py <env id> <deploy\|refresh-addresses\|destroy>` | Waits, then prints the result, duration, and the `labape:`, `fatal:` and PLAY RECAP lines. **destroy deletes the VMs.** |
| `env_password.py` | `env_password.py <env id> list` or `<env id> <windows_admin\|domain_admin>` | Lists the environment's rotation policies, or rotates one. It prints the credentials sheet's labels, never its values. |
| `rotate_agent.py` | `rotate_agent.py <kind>` | Kinds: `master_key`, `postgres_password`, `ca_password` (these restart the stack) and `vault_password`, `bootstrap_ssh_key`. Afterwards it checks the store's self-test, directory search, maintenance mode and health. |
| `rotate_r1.py` | `rotate_r1.py` | Adds the test rotator token as an elevated account, then rotates the directory token (checking the old key now gets 403), the data key and the worker key. Needs the test tokens in Authentik's `.env`. |
| `rotate_oidc.py` | `rotate_oidc.py` | Adds the rotator token as an elevated account if it's missing, rotates the OIDC client secret, then checks the token endpoint now refuses the old secret (`invalid_client`) and a fresh sign-in works. Afterwards `LABAPE_TEST_CLIENT_SECRET` in `.env` is out of date. |
| `stores.py` | `stores.py` | Saves the test stores' LABaPe logins in Settings → Secret stores, runs both Test connections and the dashboard check, then writes, reads, replaces, lists and deletes a throwaway value in each store. Needs `~/.config/labape/secrets-test.env` on the lab host (`OPENBAO_ROLE_ID`, `OPENBAO_SECRET_ID`, `CONJUR_HOST_API_KEY`, copied from `/opt/labape-secrets-test/.env` by root, mode 0600); `lab-live.sh` mounts it when present. |
| `maintenance.py` | `maintenance.py on\|check\|off` | `check`: a developer's write gets 503, a read works, and Back up now is refused. |
| `update_api.py` | `update_api.py [tag]` | Shows the current version, releases, standby version and agent state. With a tag, offers it to the API, which refuses unpublished tags. |
| `agent-request.sh` | `agent-request.sh <update\|rollback> [tag]` | Writes the agent's request file directly and waits up to 20 minutes. Then it shows the state, running version, checkout, standby version, maintenance mode and the log tail. |
