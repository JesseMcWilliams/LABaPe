# Handoff, 2026-10-23

> **Stage: Planning.** Where LABaPe stands at the end of the 2026-10-23 session, for whoever picks it up next (a new Claude Code session or a developer). Read this first, then [Planning_Handoff-2026-10-22.md](Planning_Handoff-2026-10-22.md) (its section 5 gotchas still apply) and [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) (the lab's access). It replaces the 2026-10-22 file as the starting point. LABaPe only.

## 1. State

**Latest release:** v0.6.1 (2026-10-23)

`main` has everything below merged. **v0.6.0** was released the same day (environment templates, the Fix buttons); **v0.6.1** follows (template history, the store-setup fix, count 0 on the New environment form, clearer missing-ISO messages, and a stack copying what a job needs from a linked stack: the release-prep PR is the last open one). The summary paragraph is in `Release-Summary-0.6.1.local.md` (gitignored). pytest: **560 passed, 1 skipped**. Playwright (`web/e2e`, against OZ): **all pass** apart from the 5 tests that skip themselves (the `test:writes` ones, and the ones waiting on something overdue); the suite now also opens the Templates page and starts a form from a starter.

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
| (this PR) | v0.6.1 release prep: SBOM, guides, CHANGELOG, this handoff | |

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
| **Never run for real** | A template download from a linked stack and the deploy-time copy of a linked template (the Director has nothing OZ lacks); Rotate overdue now (nothing overdue on OZ; the user started it on the Director); the Director running this build |

## 3. What's left (in order)

1. **Release v0.6.1** (if not done yet): merge the release-prep PR, then `tools/helpers/release.sh 0.6.1 Release-Summary-0.6.1.local.md --dry-run`, then without `--dry-run` (over ten minutes: run it in the background). `deploy-web.sh` from clean `main` must have been run since the last code change.
2. **Update the Director to v0.6.1 and try the automatic copy for real:** it has no Rocky 9 ISO and OZ has one, so a Rocky 9 template build (or an environment from the ISO) on `garagepc` should say "no ISO for rocky9 here; copying ... from the linked stack ..." in its log. Then the deploy-time copy of a *template* only OZ has (OZ has `debian13-linktest-2026.10` for that, 2 GB; delete it when done: `/data/VMs/LABaPe/templates/debian13-linktest-2026.10.*` on kvm1). Also, on the Director, the Directory (group search) Fix.
3. **Reset the lab clone** (`git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main`) and reinstall the agent (`~/LABaPe-web/deploy/update-agent/install.sh`) after a release; the clone was clean at v0.6.0.
4. **Try the rest for real:** the syslog fix against a real outside server (against OZ's own TLS collector it works); a template download from a linked stack.
5. **Replace the failing disk** `kvm1: /dev/sdc`. **Kerberos click-through** on `krbw1` (manual).
6. **Later:** the published `.docx` guides (`release.sh --docx`); comparing the *composed* result of two template versions; naming a version; a bundle signature for template files; the Kerberos domain side as an installer step; mail relays against a real outside SMTP account; the rest of the older queue in the 2026-10-19 handoff §3.

## 4. Lab state (2026-10-23)

| Thing | State |
|---|---|
| kvm1 / OZ | Runs `main` as built by `deploy-web.sh` (`v0.6.0-17-g793d1a7`, then the v0.6.1 release); migrations through **0035**. `~/LABaPe-web` is dirty until the reset in §3.2. The update agent copy on the host has `restart_service` (installed by hand on 2026-10-23). The CA's provisioner has the attribute template (inlined in `ca.json`; backup `ca.json.bak-pre-attrs` in the container) and **The CA applies extra attributes** is ticked |
| Authentik (`https://oz.jlm.abq:9445`) | The groups `labape-auditors`, `labape-console`, `labape-ca-admins`, `labape-ca-requesters`, `labape-ca-auditors` now exist (no members); `zz-requester` was created for a test and deleted |
| Test data left behind | The template `debian13-linktest-2026.10` (copy of an image, for the linked-copy test); `secrets.vault.yml.bak-orphaned-*` in `~/LABaPe/` (the command-line vault before it was rebuilt); revoked test certificates (`zz-cert-test…`, `zz-attr-test…`, `zz-req-test…`); the destroyed environment `zz-t1`; the files `node-v24…msi`, `gpg4win-5.1.1.exe` copied from the Director; a Notepad window open in `l2win1`'s RDP session; the earlier `zz-` catalog drafts |

## 5. Gotchas learned this session

1. **The auto-mode classifier** refused: merges without the user's say-so (it let `gh pr merge` through after an explicit "merge them"), creating Authentik groups without an explicit grant (allowed once the user said so), a read of a container's environment. It does not stop `deploy-web.sh`, `scp` of a file into the lab clone or `install.sh` for the agent.
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
