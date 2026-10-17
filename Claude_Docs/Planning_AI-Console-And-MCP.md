# Plan: console and management access for other AIs (API and MCP)

> **Stage: Planning.** Asked 2026-10-17: let another AI agent work on a host's console and manage
> environments and other items through an API. Questions 176-183 are all answered (§0). Nothing is
> built. Once a batch ships, record what was built in
> [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) (a new section per batch).

## 0. Decisions (questions 176-183, answered 2026-10-17)

| Q | Decision | Changes the plan |
|---|---|---|
| 176 | Both ways to use a console: running a command and getting its result back, and an interactive text session | X2 (commands) and X3 (sessions) |
| 177 | A token can be limited either way: all the VMs and environments its owner can see, or only chosen environments | X1: the token scope |
| 178 | An MCP server is built in this round, over the same REST API | X4 |
| 179 | A command runs as LABaPe's admin account or as one of the environment's accounts: both. `account` is optional; a new token requires one by default | X2, X3 |
| 180 | No command allow or deny list: audit and limits only; scope a token to an environment of its own | X1 |
| 181 | KVM host consoles are in scope **as an option**: off by default, an admin sets it on a token (admin tokens only) | X1, X3 |
| 182 | Session output is kept only when recording is on (Settings → Remote access), as for browser sessions | X3 |
| 183 | The MCP server offers both stdio and HTTP | X4 |

Reading of 177 (correct it if it is wrong): "both options" means a token's scope is either
*everything its owner can reach* or *a list of environments*, chosen when the token is made.

## 1. What exists

| Piece | Where | Use here |
|---|---|---|
| API tokens: personal `lpat_`, service `lpst_`, roles, expiry, revoke, audit of writes | `app/labape/tokens.py`, `api/tokens.py`, `security._token_principal` (design §37) | Any REST endpoint is already reachable by an AI with a token |
| Browser consoles: SSH, RDP, VNC through guacd, serial through the KVM host (`virsh console`) | `remote.py`, `api/remote.py`, `RemoteSession` (design §28.5) | The targets and credentials (`vm_target`, `console_target`) are reused as they are |
| Roles: admin, template_editor, deployer, file_manager, viewer, auditor | `config.ROLES` | One new role, `console` (X1) |
| Object access: owners and admins | `permissions.py` (`can_see`, `is_owner`) | Every call checks it, as the browser path does |

Why the browser path can't just be reused: the tunnel needs the browser's session cookie and Origin
and speaks the Guacamole screen protocol, and session creation turns token callers away
(`via_token`). An AI wants text in and text out over plain HTTP.

## 2. Batches

| Batch | What | Notes |
|---|---|---|
| **X1** Token scope and the `console` role | A `console` role. A token gets an optional environment list (null = everything its owner can see) and limits: commands per minute, longest session, largest output. Console endpoints refuse a token without the role. An admin-only flag on a token allows KVM host consoles (off by default). A token can require an `account`. | Migration on `api_tokens`. Tokens page gets the fields. The effective reach is the token's list intersected with what the owner can see, checked on every call. |
| **X2** Run a command | `POST /api/environments/{id}/vms/{vm}/exec` with `{command, timeout, account?}` returns `{exit_code, stdout, stderr, truncated, duration}`. Linux over SSH; Windows over WinRM (PowerShell). | Synchronous up to a timeout, with the output capped. Longer work: the same call returns a job id to poll. |
| **X3** Interactive text sessions | Open, send, read, resize and close: `POST /api/console/sessions`, `POST .../{id}/input`, `GET .../{id}/output?after=<offset>` (long-poll) and `GET .../{id}/stream` (server-sent events), `DELETE`. SSH with a pty, or the serial console through the KVM host. | The API holds the pty. Idle timeout, one session per VM per token by default, closed when the token is revoked. Logged on the same `RemoteSession` row (protocol `text`); the transcript is kept only when recording is on (§5). |
| **X4** MCP server | `labape mcp` (stdio, for a local agent) and `/mcp` (streamable HTTP, same bearer token) exposing tools: list and inspect environments, VMs, hosts and jobs; deploy, destroy and refresh an environment; run a command; open, write to, read and close a session. | A thin layer over the REST API: it adds no authority of its own, and a tool is only offered when the token's roles allow it. |

Order: X1, X2, X3, X4. X2 is useful by itself, so it can be merged and tried before X3.

## 3. Rules every console call follows

1. **Same authority as the owner, never more.** Reach is the intersection of the token's scope and the owner's own access; losing a group removes it at the owner's next sign-in (design §37).
2. **Everything is audited**, including reads of console output: token, owner, VM, the command line or session id, exit code, and byte counts. Output itself is kept only in a recording (the admin setting from question 117), not in the audit table.
3. **Secrets are masked** in anything returned or stored, by the same masking the job logs use. A console can still print a secret that is not in the vault; say so in the docs.
4. **Maintenance mode** pauses writes, and with them commands and sessions, like every other change.
5. **Limits are per token** (X1) and answer 429 with the limit that was hit.
6. **No origin or cookie path.** These endpoints take a bearer token only; a browser session cannot call them, so the same-origin rules do not apply and cannot be a way around the token's scope.

## 4. Questions 179-183 (answered 2026-10-17)

| Q | Question | Answer |
|---|---|---|
| 179 | **Who does a command run as?** LABaPe's own admin account on the VM (root, or Administrator), or one of the environment's accounts (E2)? | Both, as recommended: `account` is optional; a token can be set to *require* an account, and the default for a new token is to require one. |
| 180 | **Guardrails.** Any command, with audit and limits only, or also a per-token allow or deny list of commands? | As recommended: audit and limits only. A command list is easy to get around and gives a false sense of safety; scope the token to an environment of its own instead. |
| 181 | **KVM host consoles.** Are the hosts themselves in scope, or only environment VMs? | Host consoles are in, **as an option**: a token flag, off by default, admin tokens only (changed from the recommendation). |
| 182 | **Keeping output.** Store the transcript of every API session, or only when the recording setting is on? | As recommended: only when recording is on, as in the browser. Where they go: see §5. |
| 183 | **MCP transport.** stdio only, HTTP only, or both? | Both, as recommended; HTTP is what a remote agent needs, stdio is what a local one expects. |

## 5. Where recordings go (Q182)

Browser sessions are recorded by guacd into the `labape-recordings` volume (`session-<id>`), which the
API mounts read-only (`api/remote.py`, `RECORDINGS`); the worker's daily purge deletes files past
**Settings → Remote access → keep recordings N days** (default 90). API text sessions (X3) don't go
through guacd, so X3 adds a writable transcript location the API owns (`session-<id>.txt`, in the same
volume if its mount can be made writable for the API, else a sibling volume) that the same purge and the
same auditor-only download cover. The transcript is written only when the record-sessions setting is on.

## 6. Not in this round

- A command allow or deny list (Q180), screen-based RDP or VNC for an AI.
- Accepting an AI's own identity or a second sign-in provider: it uses a LABaPe token like any script.
