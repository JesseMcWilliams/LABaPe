# LABaPe: Claude Code project notes

LABaPe automates end-to-end provisioning and configuration of ephemeral,
self-hosted lab/test environments (mixed Windows + Linux servers and
workstations) on Hyper-V or libvirt/KVM, using OpenTofu to provision
infrastructure and Ansible to configure the OS, join Active Directory, and
install software. It's a polyglot repo — OpenTofu/HCL, Bash, Python helpers,
Ansible YAML/Jinja2, and some PowerShell — not a single-language target.

## Folder map
- `README.md` — overview: milestone status, quickstart, known gaps. Start here.
- `Claude_Docs/Design_System-Overview.md` (~870 lines, §1-§20) — architecture, backends, OS support, credentials, networking, testing, milestones. Grep for `^## ` for a section, read that range only.
- `Claude_Docs/` — everything else Claude works from (see Documentation layout below).
- `User_Docs/` — end-user guides: the web interface (users, admins), the command line, and OpenTofu/Ansible install (with WSL variants).
- `HANDOFF.local.md` — session-scratch handoff notes, gitignored (`*.local.md`). Treat as ephemeral, not a source of truth.
- `Live-Testing.local.md` — gitignored; live-lab connection details, read only for live-testing tasks.
- `secrets.vault.example.yml` — template shape only; the real `secrets.vault.yml` is gitignored and never committed.
- `tofu/` — OpenTofu: `backends/{hyperv,libvirt}`, `modules/{vm,network}/{hyperv,libvirt}`, `environments/*.tfvars.example`. Real `.tfvars`/`environment.yml`/state are gitignored.
- `ansible/` — `playbooks/site.yml`, `roles/`, `package_catalog.yml` (committed catalog); real per-run manifests are gitignored.
- `packer/{windows,linux}/` — base-image templates (Claude_Docs/Design_Base-Images.md).
- `iso/answer-files/` — unattended-install answer files shared by Packer/direct-ISO-boot/template promotion.
- `scripts/` — `deploy.sh`/`destroy.sh` (entry points), `bootstrap-secrets.sh`, `check-network.sh`, `generate-inventory.py`, `lib/`, `test/` (see Tests below).
- `app/` — web interface backend (Python package `app/labape/`: FastAPI API, auth provider plugins, job runner in `engine/runner.py`, `cli.py`), tests in `app/tests/`.
- `web/` — React + TypeScript + Vite + MUI single-page app; `package-lock.json` committed, `node_modules/`/`dist/` gitignored.
- `deploy/` — from-scratch installer: `install-labape.py` (entry, step table) and `labape_deploy/` (answers, state, steps); `update-agent/` (the host-side update and rotation agent, a user systemd unit), `labape-rollback.sh`, `allowed_signers` (release signers). `state/`, `logs/` and `answers.*.json` (except `answers.sample.json`) are gitignored; the answers files never hold secrets.
- `container/` — Dockerfile, compose.yaml, Caddyfile, Quadlet units, `setup.sh`, `ca/` (platform CA scripts), README. Generated `.env`, `secrets/`, `config/`, `engine-secrets/`, `update/` are gitignored; never read or commit them.
- `tools/authentik-test/` — throwaway Authentik (compose + blueprint) for testing sign-in; its `.env` holds generated secrets, gitignored.
- The throwaway test stores (`tools/secrets-test/`) were retired on 2026-10-12: the stack's own OpenBao and Conjur (`container/stores/`, design §26) replaced them.
- `tools/helpers/` — helper scripts used more than once (shipping a change, deploying a working tree to the lab stack, live tests); see its README. Put any reused helper here, never lab details: they come from environment variables.
- `software-store/` — gitignored except `.gitkeep`; local installer binaries, don't read or commit.
- Code files stay under ~1,000 lines (largest: `app/labape/engine/runner.py`, ~730). `Planning_Web-Interface-Design.md` (~1,050) is over; grep for a section and read that range.

## Tests
- `scripts/test/run-all.sh` — runs every check (tools/collections installed, Ansible syntax, `tofu validate`, libvirt/WinRM connectivity, vault+SSH key consistency), prints a PASS/FAIL summary.
- Individual checks: `scripts/test/test-opentofu-config.sh`, `scripts/test/test-ansible-playbook-syntax.sh`, `python3 scripts/test/test-winrm-connectivity.py`.
- Web backend: `pip install -e app pytest` in a venv, then `python -m pytest app/tests` (SQLite, no services needed). Web UI type-check/build: `npm ci && npm run build` in `web/` (no Node on the Windows box; build in a `node:22` container on the lab host).
- Redirect test output to a file and read only the summary or failures. Don't stream full test output into the conversation.
<!-- TODO: pytest covers only the web backend (app/tests); no Molecule for roles yet. tflint/ansible-lint/Molecule are Claude_Docs/Design_System-Overview.md §16 goals, not implemented. -->

## Code rules (details in the linked sections, not repeated here)
- Backend implementations (`tofu/modules/{vm,network}/*`) must expose identical inputs/outputs so environments stay backend-agnostic — Claude_Docs/Design_System-Overview.md §6.1/§6.2.
- A module `source` must be a literal string — no dynamic backend selection — Claude_Docs/Design_System-Overview.md §6.3.
- Multi-role hosts need an explicit `group_names` union; Ansible doesn't merge `group_vars` lists by default — Claude_Docs/Design_Software-Manifest.md.
- Domain-join credential format is platform-specific (bare username on Linux, `DOMAIN\user` on Windows) — Claude_Docs/Design_System-Overview.md §8.
- Validate before assuming it works: `tofu validate`, `ansible-lint`, `scripts/test/run-all.sh` — Claude_Docs/Reference_Validate-Setup.md.

## Documentation layout
- `README.md` (root): an **overview only**. It covers purpose, requirements, a quick start and a short status/feature list, and links to `User_Docs/` and `Claude_Docs/` for everything else. Put detail in a doc and link to it rather than adding it to the README.
- `Claude_Docs/` holds every doc Claude creates or works from, named `<Stage>_<Topic-With-Hyphens>.md`. Current files:
  - `Design_System-Overview.md`: the numbered-section (§1-§20) architecture/design hub — other docs and code comments cite it by section number (e.g. `§8`, `§13`). Keep current; keep the section numbers stable since so much else points at them.
  - `Design_Base-Images.md`, `Design_Directory-Objects.md`, `Design_Software-Manifest.md`: detail docs for the matching `Design_System-Overview.md` sections.
  - `Reference_Credentials.md`, `Reference_Networking.md`, `Reference_Validate-Setup.md`: interface contracts / stable checklists.
  - `Reference_Backend-Parity.md`: libvirt vs Hyper-V feature/interface matrix and the order to close gaps.
  - `Reference_Secrets-Backend-Requirements.md`: what a secret store and its LABaPe provider must offer (interface, paths, TLS, least privilege, rotation, failures, tests).
  - `Planning_Certificate-Authority.md`, `Planning_Environment-Templates.md`: proposals for the `certificate_authority` role and environment templates, not built yet (Design_System-Overview.md §19/§20). The web stack's own platform CA is built (Planning_Web-Interface-Design.md §22.5).
  - `Planning_User-Docs-Backlog.md`: one line per user-visible change not yet covered in `User_Docs/`.
  - `Planning_Backlog.md`: agreed but deferred work (Hyper-V parity, `.docx` guides, the v0.3.0 release), one line each with a pointer to the detail.
  - `Planning_Operations-And-Admin-UI.md`: plan for VM power and boot order, the Admin area, filters, KVM host and VM details pages, browser SSH/RDP and consoles (batches O1-O6, questions 107-120).
  - `Planning_Stack-Syslog-Installer-File-Host.md`: plan for the stack's syslog collector and Syslog tab, the installer's ask-first answer file and service choices, and the primary file host (batches Y1-Y3, questions 121-141).
  - `Planning_Web-Interface-Options.md`: M10 web interface decisions (custom app, Authentik, containers, remote libvirt) and the options considered.
  - `Planning_Questions.md`: numbered open questions awaiting decisions (answer by number).
  - `Planning_Web-Interface-Design.md`: M10 design and as-built record (components, data model, permissions, job runner, deployment, build phases 10a-10h; §19-§24 cover what's built, through secret rotation).
  - `Testing_Troubleshooting-Log.md`: open findings and blow-by-blow bug history from M10 on, filed by topic. When it grows past ~500 lines, move fully closed sections into an `Archive_Testing_...` file, as was done for M1-M6.
  - `Archive_<OriginalStage>_<Topic>.md`: finished or superseded material. **Don't read `Archive_*` unless the user asks or the task needs history.** Current: `Archive_Testing_Troubleshooting-Log-M1-M6.md` (closed M1-M6 troubleshooting history).
- `CHANGELOG.md` (root): release history, `[Unreleased]` at the top. Kept at the root (GitHub/tooling convention). A release is a `vX.Y.Z` tag plus a GitHub release; the image is stamped with `git describe --tags`, and the web dashboard compares it with the latest release.
- `User_Docs/`: end-user guides. `Web-Interface-Guide.md` (everyone who signs in), `Web-Interface-Admin.md` (admins), `Command-Line.md` (scripts without the web interface), and `Install-OpenTofu.md`/`Install-Ansible.md` with WSL variants. New user-visible changes go to the backlog first, then into the matching guide.
- `Published_Docs/`: `.docx` deliverables for end users. None exist yet.
- When you make a user-visible change, add one line for it to `Claude_Docs/Planning_User-Docs-Backlog.md` (create it if it doesn't exist yet).
- Rename docs with `git mv`, and update every link to them in the same change.
- If a doc is large, find the target with grep and read a narrow range. Keep table rows to one or two sentences.

## Docs: what to update for each kind of change
| Change | Update |
|---|---|
| New feature | `Claude_Docs/Design_System-Overview.md` (relevant §) + matching `Design_*`/`Reference_*` detail doc; update README Status if it changes what works end-to-end; add a line to `Claude_Docs/Planning_User-Docs-Backlog.md` if user-visible |
| Bug fix | `Claude_Docs/Testing_Troubleshooting-Log.md`; update README Known Gaps if applicable |
| Design decision | `Claude_Docs/Design_System-Overview.md`, relevant section, or §17 Open Questions |
| New gotcha | `Claude_Docs/Testing_Troubleshooting-Log.md`, cross-link from README Known Gaps if user-facing |

- For "verify the docs are updated", use a subagent to diff the branch against this checklist and report the gaps only.

## Git
- Don't work directly on `main`. Create a topic branch named `YYYY-MM-DD-<topic>` and open a PR into `main` with `gh`.
- Commit, push, open a PR or merge only when asked. "Commit and push" means both.

## Live testing
- Lab environment details are in `Live-Testing.local.md` in the project root. That file is gitignored. **Read it only when a task involves live testing.** Never copy its contents into tracked files, commit messages or PR descriptions.
- If `Live-Testing.local.md` is missing, ask for the details. Don't guess.
- Never write secrets into any file, log or commit message, including `Live-Testing.local.md`. That file names *where* the credentials live, not the credentials themselves.
- When an example, doc or test needs a password placeholder, use `ThisIsMy_FAKE_Password6!`. It's obviously fake, and it satisfies typical complexity rules.
