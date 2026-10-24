# Handoff, 2026-10-23

> **Stage: Planning.** Where LABaPe stands at the end of the 2026-10-23 session, for whoever picks it up next (a new Claude Code session or a developer). Read this first, then [Planning_Handoff-2026-10-22.md](Planning_Handoff-2026-10-22.md) (its section 5 gotchas still apply) and [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) (the lab's access). It replaces the 2026-10-22 file as the starting point. LABaPe only.

## 1. State

**Latest release:** v0.6.2 (2026-10-23)

`main` has everything below merged and **no PR is open**. **v0.6.0** (environment templates, the Fix buttons) and **v0.6.1** (template history, the store-setup fix, count 0 on the New environment form, clearer missing-ISO messages, and a stack copying by itself what a job needs from a linked stack) were both released on 2026-10-23, each with its signed tag and GitHub release (the SBOM, `labape-mcp-X.Y.Z.mcpb` and `labape-skill-X.Y.Z.zip` attached). pytest: **565 passed, 2 skipped** (re-run 2026-10-23 after #249, on Windows in a scratch venv, about 6 minutes). Playwright (`web/e2e`, against OZ): **all pass** apart from the 5 tests that skip themselves.

The session ran in two parts: the lab checks left over from 2026-10-22, then the follow-ups that came out of them.

| PR | What | Design |
|---|---|---|
| #216 | **Certificate attributes** found broken against the real step-ca and fixed (wrong template flag, `.Token` vs `.Insecure.User`, script not executable in the CA container) | §66, log |
| #217 | **Copy and paste in browser SSH and RDP** (text, both ways; **Paste** button) | log |
| #218 | The Dashboard says **why a host's VM storage could not be read**, with a link to the permissions help | log |
| #219, #220 | **Session recordings play** again (guacamole-common-js 1.5.0 Blob bug) and a **downloadable offline recording player** | log |
| #221, #222, #223 | Paste hint for SSH vs RDP; the live-run log; **VM key rotation** waits for deploys and VMs that missed it are brought onto the current key | log |
| #224, #225 | Links readable on the dark theme; a cancelled linked copy ends `cancelled` | log |
| #226 | **Fix buttons** on the admin checks: Directory (Authentik, from an admin token used once) and Email | §67 |
| #227, #228, #230, #231 | **Environment templates**: save from an environment, parameters, warnings, starters; **parts** (sub-assemblies) with prefixes and values; YAML export and import; a part **pinned to a version** | §68, §69, §71 |
| #229, #232 | **Fix buttons** for the Platform CA and OpenBao / Conjur (update agent `restart_service`, unseal after) and for Syslog | §70, §72 |
| #233, #234 | v0.6.0 release prep and the release | |
| #235 | Tests for the update agent's `restart_service` and `stores_setup` (real script, stub commands) | §70 |
| #236 | The store Fix "new login" (= the stores' full setup) survives a drifted command-line vault: a warning, a `--rebuild` for that vault, OpenBao's CLI policy reads the engine vault | log |
| #237 | **Template history**: versions, what changed between two, restore | §73 |
| #238 | New environment: **count 0 asks to remove the host group**; a labelled Remove host group button | |
| #239 | A missing install ISO is said plainly before anything is created (found on the Director) | log |
| #240 | **A stack copies by itself what a job needs from a linked stack** (the install ISO of an OS, installers of chosen applications) | §74 |
| #241, #242 | v0.6.1 release prep and the release | |
| #243 | This handoff after the v0.6.1 release | |
| #244 | **A host's folders checked and fixed on save** (`engine/pathperms.py`, a Check / Fix folders button; AppArmor reported only) | §50.3a |
| #245 | **Convert to template on a remote host** used the deploy job's removed SSH key path | log |
| #246, #247 | v0.6.2 release prep and the release | |
| #248 | Handoff: the automatic ISO copy and template creation checked live by the user | |
| #249 | **Drives and volumes flags a drive with a SMART warning** (alert on top, tinted row with a chip, "check it" beside a passed health); **not released, no CHANGELOG entry yet** (add one under `[Unreleased]` before the next release) | log |
| #250 | This handoff after #249 | |
| (this PR) | Handoff after a **project review** (security, code, tooltips, guides, Help); findings in §7, nothing changed in the code yet | |

## 2. How much of it has been seen

| Thing | Checked how |
|---|---|
| Everything | Unit tests; the web type-check; the browser runs below on OZ |
| Certificate attributes, clipboard (SSH and RDP), recordings and the offline player, linked file and **resumed** linked copy, a standalone group + a narrowed account + Add host, plain `ca_requester` | **Live on OZ** (2026-10-23), see the log's "Live run of the open tests" |
| Templates, parts, pinning, YAML | Unit tests and a browser run on OZ (create, edit, export, import, delete); the Postgres delete bug was found there |
| Directory fix | Live against OZ's Authentik (repeatable, bad token refused); the create-from-nothing path is unit-tested with a fake Authentik only |
| CA restart, OpenBao restart + unseal through the agent | **Live on OZ**; a new store login and a Conjur restart were not run |
| Syslog fix | Unit tests; live for the refusal only (OZ's syslog is the stack's collector) |
| The update agent's `restart_service` and `stores_setup` | `app/tests/test_z13_update_agent.py` (real script, stub commands); `full_backup`, the CA restart and an OpenBao restart live on OZ; the Director's update to v0.6.0 was done by the user |
| Automatic copy from a linked stack (§74) | Unit tests with a faked library; live on OZ for the read side only (it lists the Director's ISOs, copies nothing when not needed). **The ISO copy has since run for real (user, on the Director, 2026-10-23) and works** |
| The Director's failed builds (a Rocky deploy and a template build, 2026-10-23) | Diagnosed from its job logs: no Rocky ISO in its Files; fixed in the messages (#239) and by the automatic copy (#240) |
| **Never run for real** | A template download from a linked stack and the deploy-time copy of a linked *template* (OZ has `debian13-linktest-2026.10` for it); the automatic ISO and installer copy; Rotate overdue now (nothing overdue on OZ; the user started it on the Director) |

## 3. What's left (in order)

0. **Folder check on `garagepc` (the user):** change the VM folder on its host page to `/data2/VMs/LABaPe` to see the preview and fix against a real `libvirt-qemu` and AppArmor. Not yet run on a real host; the script itself was run under WSL as a plain user. (Done live by the user on v0.6.2: the convert to template on `Single1`, the automatic ISO copy from a linked stack and template creation all work.)
1. ~~**Update the Director and try the automatic copy for real**~~ **Done (user, 2026-10-23):** the automatic ISO copy and template creation work on `garagepc`.
2. **Reset the lab clone and reinstall the agent** (the user runs them; the classifier blocks them from a session): `git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main`, then `~/LABaPe-web/deploy/update-agent/install.sh`. The clone reads `v0.6.0-dirty` now: a deploy overlays files on it and never moves its checkout, so it shows a version behind the running one until reset.
3. **Delete the test template** `debian13-linktest-2026.10` from OZ when the linked-template tests are done: `/data/VMs/LABaPe/templates/debian13-linktest-2026.10.{qcow2,json}` on kvm1 (the qcow2 is read-only: `chmod u+w` first).
4. **Try the rest for real:** the syslog fix against a real outside server (against OZ's own TLS collector it works); a template download from a linked stack in the OZ-to-Director direction.
5. **Look at Drives and volumes for `kvm1` in a browser** (#249 was only type-checked; I never saw `/dev/sdc`'s SMART data, so I assumed it passes overall with reallocated or pending sectors: if it shows `FAILED` and wasn't flagged, that is a different bug). **Replace the failing disk** `kvm1: /dev/sdc`. **Kerberos click-through** on `krbw1` (manual).
6. **The review's findings (§7):** the user picks next between the quick-fix branch (§7, items marked *quick*), adding the items that need a decision to `Planning_Questions.md` (from **196**, the next free number), or saving the review as its own doc.
7. **Later:** the published `.docx` guides (`release.sh --docx`); comparing the *composed* result of two template versions; naming a version; a bundle signature for template files; automatic copy of other files (category *files*); the Kerberos domain side as an installer step; mail relays against a real outside SMTP account; the rest of the older queue in the 2026-10-19 handoff §3.

## 4. Lab state (2026-10-23)

| Thing | State |
|---|---|
| kvm1 / OZ | Runs **v0.6.1** (`deploy-web.sh` from the tag; the Dashboard reports v0.6.1, latest, no update available; every check green); migrations through **0035**. `~/LABaPe-web` reads `v0.6.0-dirty` until the reset in §3.2. The update agent copy on the host has `restart_service` and the stores-setup change (installed by hand); the agent test harness's `LABAPE_RESTART_HEALTH_SECONDS` came later and needs a reinstall to take effect (no behaviour change). The CA's provisioner has the attribute template (inlined in `ca.json`) and **The CA applies extra attributes** is ticked |
| Authentik (`https://oz.jlm.abq:9445`) | The groups `labape-auditors`, `labape-console`, `labape-ca-admins`, `labape-ca-requesters`, `labape-ca-auditors` now exist (no members); `zz-requester` was created for a test and deleted |
| Test data left behind | The template `debian13-linktest-2026.10` (a copy of an image, for the linked-copy tests); `secrets.vault.yml.bak-orphaned-*` in `~/LABaPe/` on kvm1 (the command-line vault before it was rebuilt); the files `node-v24...msi`, `gpg4win-5.1.1.exe` copied from the Director; revoked test certificates (`zz-cert-test...`, `zz-attr-test...`, `zz-req-test...`); the destroyed environment `zz-t1`; a Notepad window open in `l2win1`'s RDP session; the earlier `zz-` catalog drafts. On the Director: the failed environment `Single1` left a half-made VM on `garagepc` (Redeploy or Destroy it once the ISO is there) |

## 5. Gotchas learned this session

1. **The auto-mode classifier** refused (also once a commit, push, PR and merge chain for the release prep until the user said "yes, commit, push, pr and merge"; split into separate commands afterwards): merges without the user's say-so (it let `gh pr merge` through after an explicit "merge them"), creating Authentik groups without an explicit grant (allowed once the user said so), a read of a container's environment. It does not stop `deploy-web.sh`, `scp` of a file into the lab clone or `install.sh` for the agent.
2. **Postgres enforces foreign keys, SQLite in the tests does not.** A delete that touches two tables needs a run on the lab (found with template versions). Delete child rows by a statement that runs first.
3. **`deploy-web.sh` overlays the working tree on the lab clone and deletes nothing**: a file from another branch stays and can break the build (a stale `PartsEditor.tsx`). Also, **never deploy a branch that lacks a migration the lab database already has** (the API would not start): deploy a throwaway local merge of the branches in play, and delete it.
4. **Heredocs with many quotes fail in the Bash tool** (`unexpected EOF`): write a script with the Write tool and run it. A Python script that edits a file with `"""` inside a `"""` string breaks the same way.
5. **Authentik's API** takes a session cookie plus `X-authentik-CSRF` **and a `Referer`/`Origin`** header for writes; `view_key` returns a token's key; the login flow's password box needs a short wait and typing (a fast `fill` is cleared).
6. **step-ca:** `--set-file` data reaches a template as `.Insecure.User`; `step ca provisioner update` takes `--x509-template` (not `-file`) and inlines the template into `ca.json`.
7. **guacamole-common-js 1.5.0:** `SessionRecording` ignores a Blob (use a tunnel); the guacd terminal pastes on Ctrl+Shift+V or a middle click, not Ctrl+V.
8. **Stacked PRs conflict in the same doc appendices** (design doc, backlog, log): resolve by keeping both sides, theirs first. `main.py`'s router list is the other place two PRs collide.
9. **A scheduled VM-key rotation can run while an environment deploys**: it now waits (30 minutes at most); a VM that still missed it is healed at first use.

10. **`stores.sh setup` is the stores' full provisioning**, not a light "new login": it pulls images, restarts stores, reconfigures OpenBao, people, VM sign-in, Conjur and LABaPe's logins. Its last check (the command-line vault) used to stop it; the agent now runs it with `LABAPE_STORES_CLI_CHECK=warn`. The store keeps only 7 previous vault passwords: a copy not used between 8 rotations is orphaned (`store_get.py vault-pass --rebuild` fixes that).
11. **A job that names a host file in its error is wrong help in the web interface** (the Director's missing ISO said `os_iso_paths`): say what to do under Files first.
12. **`wait_healthy N` in the update agent waits all N seconds** (it needs healthy checks to the end); the restart action uses `LABAPE_RESTART_HEALTH_SECONDS` (60).

## 6. Where things are

| Need | Look at |
|---|---|
| Templates | `app/labape/api/env_templates.py` (compose, versions, export/import), `web/src/pages/EnvTemplates.tsx`, `components/{TemplatePicker,PartsEditor,SaveAsTemplate}.tsx`; design §68, §69, §71 |
| The Fix buttons | `app/labape/fixes.py` (the registry), `api/fixes.py`, `components/FixDialog.tsx`, the agent's `restart_service` in `deploy/update-agent/labape-update.sh`; design §67, §70, §72 |
| Sessions | `web/src/pages/RemoteSession.tsx` (clipboard), `Sessions.tsx` (player), `web/guac-player.template.html` and the plugin in `web/vite.config.ts` (the offline player) |
| The live checks | the log's "Live run of the open tests (2026-10-23)" and the entries after it in `Testing_Troubleshooting-Log.md` |
| Everything else | the 2026-10-22, 2026-10-21, 2026-10-19 and 2026-10-16 handoffs §6-§7 |

## 7. Project review (2026-10-23, after #249)

A review of security, code practice, missing features, descriptions and tooltips, the user and admin guides and the built-in Help. Numbers are the review's own (as given in the chat). **Nothing is fixed yet.** *quick* = fits one small branch; *decide* = needs the user's answer first (a question from 196).

**Security**

| # | Finding | Where | Kind |
|---|---|---|---|
| 1 | A personal API token outlives its owner: roles come from the groups saved at the last sign-in, so someone removed from Authentik keeps a working token (up to 365 days) | `tokens.py` `effective_roles`, `security.py` `_token_principal` | decide |
| 2 | Sessions are signed cookies only: sign-out ends one browser, `breakglass revoke` doesn't end open break-glass sessions | `main.py` SessionMiddleware, `auth/breakglass.py` `revoke_all` | decide |
| 3 | VM SSH (`StrictHostKeyChecking=no`) and WinRM (`server_cert_validation="ignore"`) send passwords without checking the VM; keep host keys at deploy, check WinRM against the platform CA | `engine/vmexec.py`, `engine/vmadmin.py` | decide |
| 4 | No browser security headers (HSTS, CSP/frame-ancestors, nosniff, Referrer-Policy) | `container/caddy/Caddyfile` | quick |
| 5 | Download from URL blocks link-local only: loopback and the stack's own containers (OpenBao, step-ca) are reachable | `engine/downloads.py` `check_url` | decide |
| 6 | CSV exports don't neutralize `= + - @` cells (syslog lines come from any host) | `web/src/components/Paging.tsx` `csv` | quick |
| 7 | Grants and role bindings match the username, which comes from `preferred_username` or email; a rename could pick up someone else's grants | `permissions.py`, `security.py` `resolve_roles`, `auth/oidc.py` | decide |
| 8 | Small: `/hosts/{id}/resources` answers for any host; tus `Upload-Length` unchecked and bad headers give a 500; `/api/docs` public; `caddy:2` and `postgres:17-alpine` float; the API image runs as root; raw errors to the browser (`api/remote.py` create_session); an environment without a named network allows IPs of every network in its certificates (`api/ca.py`) | as named | quick |

**Code**

| # | Finding |
|---|---|
| 9 | Over the ~1,000-line rule: `api/environments.py` 1,164, `engine/runner.py` 1,149, `deploy/labape_deploy/steps.py` 1,000 (CLAUDE.md still says runner ~730) |
| 10 | 352 imports inside functions: circular dependencies worth untangling |
| 11 | `main.py`'s router list is one long line (gotcha 8): one per line |
| 12 | Hyper-V was dropped (question 175) but `tofu/*/hyperv`, `api/hosts.py`, `engine/pathperms.py` and scripts still carry it |
| 13 | Tests run on SQLite only (gotcha 2): a Postgres run would catch foreign-key bugs |
| 14 | A session that expires mid-use shows errors instead of the sign-in page (only `App.tsx` startup handles 401) |
| 28 | Dead code after `return` in `engine/hostio.py` `tunnel_conn` (lines 283-291, names that don't exist there; a lost function from a merge) |

Ruff otherwise: the syslog SQL hit is a false alarm (bound parameters), `sha1` is checksums only, libvirt XML parsed without `defusedxml` (host output, low), 3 unused imports.

**Missing features (decide):** 15 sharing an environment or handing it to another owner (a viewer sees nothing; stuck when the owner leaves); 16 a Users page for admins (last sign-in, disable, which revokes tokens and sessions, and open sessions).

**Tooltips and descriptions (quick)**
- 17: Guide §10 says every button has a tooltip; about 20 action buttons don't (drive pool Take out / Take offline / Scrub / Stop, Mail Use this server, Certificates Revoke… and Request, Rotate N now, Quotas Add / Edit, VM Take snapshot / Refresh, Dashboard Silence, Fix, Run the preview, Add N hosts and deploy, Add a part, Add account, Templates Edit). Dialog Cancel/Save are fine without.
- 18: help text for the token form's role checkboxes (raw ids) and limits (1-600, 1-480, 1-4096); Settings OpenBao KV mount, AppRole mount, Role ID, Conjur Account, Authenticator; Parts Prefix; template Edition, Server SKU, Min vCPUs.
- 19: `Certificates.tsx` example uses the lab's domain (`app1.jlm.abq`): use `app1.example.test`.

**Guides and Help (quick)**
- 20: The guides cover everything to v0.6.2; the two backlog lines (folder check, SMART flag) wait for the release as usual; #249 still needs its CHANGELOG line.
- 21: Guide §1's role table lacks `console` (`labape-console`).
- 22: Admin §11 lacks `rewrap-master`, `ca-repass`, `stores-configure`, `push-vm-key`.
- 23: `Command-Line.md` doesn't mention `bootstrap-secrets.sh`, `check-network.sh`, `generate-inventory.py`.
- 24: Help links (`HelpHere.tsx`): `/environment-templates` should open guide §14 "Environment templates", `/admin/mail` admin §6.1 "The stack's mail server"; `/admin` has no entry.

Done well (keep): hashed tokens and one-time break-glass, the cross-origin write check, the logo without SVG and with a sandbox CSP, the Help Markdown renderer without HTML, signed-tag updates, hash-locked pip, the certificate token kept off the VMs.
