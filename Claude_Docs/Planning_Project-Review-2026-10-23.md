# Project review, 2026-10-23

> **Stage: Planning.** A review of LABaPe after #249 (v0.6.2 plus the SMART flag): security, code practice, missing features, descriptions and tooltips, the guides and the built-in Help. Numbers are the review's own. Decisions that need an answer are questions 196-202 in [Planning_Questions.md](Planning_Questions.md). The handoff ([Planning_Handoff-2026-10-23.md](Planning_Handoff-2026-10-23.md) §7) points here.

**Status key:** *done* = in the branch `2026-10-23-review-quick-fixes`; *decided* = answered, build it next; *open* = waiting for an answer; *later* = no decision needed, not scheduled.

## Security

| # | Finding | Where | Status |
|---|---|---|---|
| 1 | A personal API token outlives its owner: roles come from the groups saved at the last sign-in, so someone removed from Authentik keeps a working token (up to 365 days) | `tokens.py` `effective_roles`, `security.py` `_token_principal` | **done** (question 196; branch `2026-10-23-breakglass-revoke-ends-sessions`): a sweep disables a deactivated or removed owner and revokes their personal tokens; not yet run against the real Authentik |
| 2 | Sessions are signed cookies only: sign-out ends one browser, `breakglass revoke` doesn't end open break-glass sessions | `main.py` SessionMiddleware, `auth/breakglass.py` `revoke_all` | **done** (question 197; same branch) |
| 3 | VM SSH (`StrictHostKeyChecking=no`) and WinRM (`server_cert_validation="ignore"`) send passwords without checking the VM | `engine/vmexec.py`, `engine/vmadmin.py` | **done** (question 198): SSH host keys and the WinRM certificate fingerprint are recorded and checked (`engine/vmidentity.py`); Ansible's deploy-time connections, RDP and the guacd `host-key` format are not covered or not tried live |
| 4 | No browser security headers | `container/caddy/Caddyfile` | **done** (HSTS, nosniff, Referrer-Policy, `frame-ancestors 'self'`; a script CSP is left out on purpose) |
| 5 | Download from URL blocks link-local only: loopback and the stack's own containers (OpenBao, step-ca) are reachable | `engine/downloads.py` `check_url` | open (question 199) |
| 6 | CSV exports don't neutralize `= + - @` cells (syslog lines come from any host) | `web/src/components/Paging.tsx` `csv` | **done** (a text cell that starts with one gets a leading quote; numbers are untouched) |
| 7 | Grants and role bindings match the username, which comes from `preferred_username` or email; a rename could pick up someone else's grants | `permissions.py`, `security.py` `resolve_roles`, `auth/oidc.py` | open (question 200) |
| 8 | Small: `/hosts/{id}/resources` answers for any host; tus `Upload-Length` unchecked and bad headers give a 500; `/api/docs` public; `caddy:2` and `postgres:17-alpine` float; the API image runs as root; raw errors to the browser (`api/remote.py` create_session); an environment without a named network allows IPs of every network in its certificates (`api/ca.py`) | as named | later |

## Code

| # | Finding | Status |
|---|---|---|
| 9 | Over the ~1,000-line rule: `api/environments.py` 1,164, `engine/runner.py` 1,149, `deploy/labape_deploy/steps.py` 1,000 (CLAUDE.md still says runner ~730) | later |
| 10 | 352 imports inside functions: circular dependencies worth untangling | later |
| 11 | `main.py`'s router list was one long line | **done** (one per line) |
| 12 | Hyper-V was dropped (question 175) but `tofu/*/hyperv`, `api/hosts.py`, `engine/pathperms.py` and scripts still carry it | later |
| 13 | Tests run on SQLite only (handoff gotcha 2): a Postgres run would catch foreign-key bugs | later |
| 14 | A session that expires mid-use shows errors instead of the sign-in page (only `App.tsx` startup handles 401) | later |
| 28 | Dead code after the `return` in `engine/hostio.py` `tunnel_conn` (the body of a function whose head was lost in a merge; nothing called it) | **done** (removed) |

Ruff otherwise: the syslog SQL hit is a false alarm (bound parameters), `sha1` is checksums only, libvirt XML parsed without `defusedxml` (host output, low), 3 unused imports.

## Missing features

| # | Feature | Status |
|---|---|---|
| 15 | Sharing an environment or handing it to another owner (a viewer sees nothing; stuck when the owner leaves) | open (question 201) |
| 16 | A Users page for admins (last sign-in, disable, which revokes tokens and sessions, and open sessions) | open (question 202); it is also what makes decisions 196 and 197 visible |

## Tooltips and descriptions

| # | Finding | Status |
|---|---|---|
| 17 | About 20 action buttons without tooltips | **done** for the drive pool buttons (Take out / Remove, Take offline / Bring online, Scrub / Check, Stop), Mail Use this server, Certificates Request and Revoke, Quotas Add and Edit, VM Refresh (the error bar), Dashboard Silence, Add account, Add a part, Add N hosts and deploy, Templates Edit; the rest already had one |
| 18 | Help text for the token form's role checkboxes (raw ids) and limits (1-600, 1-480, 1-4096); Settings OpenBao KV mount, AppRole mount, Role ID, Conjur Account, Authenticator; Parts Prefix; template Edition, Server SKU, Min vCPUs | later |
| 19 | `Certificates.tsx` example used the lab's domain | **done** (`app1.example.test`) |

## Guides and Help

| # | Finding | Status |
|---|---|---|
| 20 | The guides cover everything to v0.6.2; the backlog lines (folder check, SMART flag) wait for the release as usual | CHANGELOG line for #249 added under `[Unreleased]` |
| 21 | Guide §1's role table lacked `console` | **done** |
| 22 | Admin §11 lacked `rewrap-master`, `ca-repass`, `stores-configure`, `push-vm-key` | **done** |
| 23 | `Command-Line.md` didn't mention `bootstrap-secrets.sh`, `check-network.sh`, `generate-inventory.py` | **done** (new §6) |
| 24 | Help links: `/environment-templates` should open "Environment templates", `/admin/mail` "The stack's mail server"; `/admin` has no entry | the two links **done**; `/admin` has no entry still (no landing page to point from) |

## Done well (keep)

Hashed tokens and one-time break-glass, the cross-origin write check, the logo without SVG and with a sandbox CSP, the Help Markdown renderer without HTML, signed-tag updates, hash-locked pip, the certificate token kept off the VMs.

## Not checked

The web type-check and build were not run for the branch (no Node on the Windows box): run `npm ci && npm run build` in `web/` on the lab host before merging. The Caddyfile change needs a `caddy validate` and a look at the remote-session and mail pages in a browser.
