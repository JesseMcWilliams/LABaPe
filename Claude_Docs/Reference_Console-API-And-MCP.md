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

## Certificates (the CA roles)

A token may carry the roles **`ca_requester`**, **`ca_admin`** and **`ca_auditor`** (a personal token only those its owner holds; an admin's may carry all three). They give access to the platform CA's REST API, `/api/certificates`:

| Role | May |
|---|---|
| `ca_requester` | request certificates, list and read its owner's own and their environments' certificates |
| `ca_auditor` | list and read every certificate (`scope=all`) and see the CA's details (`GET /api/certificates/ca`); changes nothing |
| `ca_admin` | everything: also revoke, edit the settings, add certificates to the register; not limited by the name lists |

| Call | |
|---|---|
| `GET /api/certificates?scope=mine\|all&q=&state=&source=&limit=` | The certificates the token may see (`scope=all` for `ca_admin`, `ca_auditor` and the auditor role). Each row has `uses`, `state`, `not_before`, `not_after`. |
| `GET /api/certificates/{id}` | One certificate with its PEM chain. |
| `POST /api/certificates` | `{common_name, dns[], ips[], days, environment_id, csr, uses[]}`: with names LABaPe makes a P-256 key pair and returns it once in `key`; with `csr` only the certificate comes back. Names must be under the stack's names and the allowed domains and addresses in the allowed networks (not for `ca_admin`). `uses` (`server_auth`, `client_auth`, `code_signing`, `email_protection`, `time_stamping`, `ocsp_signing`, `smartcard_logon`, `signing`, `encryption`) works only when a CA administrator turned extra attributes on; none chosen is the CA's default. |
| `POST /api/certificates/{id}/revoke` | `{reason}` (`ca_admin`). Passive: a client that never checks keeps trusting it. |
| `GET /api/certificates/ca` | Whether the CA answers, its root and fingerprint, provisioners, register counts. |

The MCP tools are `list_certificates`, `get_certificate`, `ca_info`, `request_certificate` and `revoke_certificate`, offered by the same roles. A request's private key is in the tool result: the agent must store it, LABaPe does not.

## MCP

- **Local agent (stdio):** run `labape mcp` (or `python -m labape.mcp`) with `LABAPE_URL=https://<stack>`, `LABAPE_TOKEN=...` and, for the platform CA, `LABAPE_CA_FILE=<root.crt>`. As an MCP client entry: `{"command": "labape", "args": ["mcp"], "env": {"LABAPE_URL": "...", "LABAPE_TOKEN": "..."}}`.
- **Remote agent (HTTP):** the server address `https://<stack>/mcp` with the header `Authorization: Bearer lpat_...`.
- **Claude Desktop:** build the bundle with `python tools/helpers/build_mcpb.py` (writes `dist/labape-mcp-<version>.mcpb`) and import it in Claude Desktop (Settings → Extensions → Advanced → Install Extension, or double-click the file). It asks for the LABaPe address and the API token (kept in the system's secret store); the certificate authority file and a skip-verification switch are optional. It runs `mcp.py` with the computer's Python (3.9 or newer, standard library only, nothing to install). If the token is wrong or the stack can't be reached, the tools say so instead of listing nothing. It also works with the Microsoft Store version of Claude Desktop (whose sandboxed profile hides the extension's folder from other programs); Desktop's log is then under `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\Claude\logs\mcp-server-LABaPe.log`.
- The session tools take the id from `open_session` as `session` (`session_id` is accepted too, but the Claude desktop app's bridge drops an argument by that name).
- The tools offered depend on the token's roles; each is one REST call with that token. Management tools (deploy, destroy, jobs, hosts) need the matching roles on the token, so a console-only token sees only the console tools.

## What is recorded

Every command and session is a row in the session log (protocols `exec` and `text`) and an audit event (token, VM, account, command line or session id, exit code, byte counts; typed input only as a size). Output is kept only as a transcript when Settings → Remote access has recording on; auditors download it from the session log. A console can still print a secret that isn't in the vault.
