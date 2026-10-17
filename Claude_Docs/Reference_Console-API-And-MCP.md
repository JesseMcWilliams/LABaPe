# Reference: console and management API for AIs and scripts, and the MCP server

Design: [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) §59; plan: [Planning_AI-Console-And-MCP.md](Planning_AI-Console-And-MCP.md).

## Get a token

Tokens page (user menu → API tokens) → **New token**. Choose the **console** role to let it run commands and open text sessions, and then:

| Option | Meaning |
|---|---|
| Only chosen environments | The token reaches only these; otherwise every environment its owner can use (admins: all; others: those they own). |
| Must name an account | Each call says which environment account to run as (`account`); off lets it use LABaPe's own access account on the VM (root-level on most VMs). Default on. |
| KVM host consoles | Admins only, off by default; the token must also hold the admin role. |
| Calls per minute, session minutes, output KB | Limits; a call over the rate gets 429. Output past the limit stops a command and rolls a session's buffer. |

Send it as `Authorization: Bearer lpat_...`. Console calls take a token only, never a browser session.

## Run a command

`POST /api/environments/{id}/vms/{vm}/exec` with `{"command": "uname -a", "timeout": 60, "account": ""}` returns `{exit_code, stdout, stderr, truncated, timed_out, duration, session}`. A shell command on Linux, PowerShell on Windows.

## Interactive session

| Call | |
|---|---|
| `POST /api/console/sessions` | `{environment_id, vm, kind: "shell"\|"serial", account}` or `{kvm_host_id}`; returns `{id, next}`. One per token per target. |
| `POST /api/console/sessions/{id}/input` | `{data}`; end a line with a newline, `\u0003` is Ctrl-C. |
| `GET /api/console/sessions/{id}/output?after=N&wait=S` | Output after offset N (long poll up to 30 s); returns `{data, next, lost, open, reason}`; pass `next` back as `after`. |
| `GET /api/console/sessions/{id}/stream?after=N` | The same as server-sent events. |
| `DELETE /api/console/sessions/{id}` | Close. A session also ends at the token's session length or after the idle time in Settings → Remote access. |
| `GET /api/console/environments` | What the token can reach. |

## MCP

- **Local agent (stdio):** run `labape mcp` (or `python -m labape.mcp`) with `LABAPE_URL=https://<stack>`, `LABAPE_TOKEN=...` and, for the platform CA, `LABAPE_CA_FILE=<root.crt>`. As an MCP client entry: `{"command": "labape", "args": ["mcp"], "env": {"LABAPE_URL": "...", "LABAPE_TOKEN": "..."}}`.
- **Remote agent (HTTP):** the server address `https://<stack>/mcp` with the header `Authorization: Bearer lpat_...`.
- The tools offered depend on the token's roles; each is one REST call with that token. Management tools (deploy, destroy, jobs, hosts) need the matching roles on the token, so a console-only token sees only the console tools.

## What is recorded

Every command and session is a row in the session log (protocols `exec` and `text`) and an audit event (token, VM, account, command line or session id, exit code, byte counts; typed input only as a size). Output is kept only as a transcript when Settings → Remote access has recording on; auditors download it from the session log. A console can still print a secret that isn't in the vault.
