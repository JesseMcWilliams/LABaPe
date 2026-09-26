# LABaPe: Claude Code project notes

LABaPe automates end-to-end provisioning and configuration of ephemeral,
self-hosted lab/test environments (mixed Windows + Linux servers and
workstations) on Hyper-V or libvirt/KVM, using OpenTofu to provision
infrastructure and Ansible to configure the OS, join Active Directory, and
install software. It's a polyglot repo — OpenTofu/HCL, Bash, Python helpers,
Ansible YAML/Jinja2, and some PowerShell — not a single-language target.

## Folder map
- `README.md` — overview: milestone status, quickstart, known gaps. Start here.
- `Claude_Docs/Design_System-Overview.md` (~800 lines, §1-§20) — architecture, backends, OS support, credentials, networking, testing, milestones. Grep for `^## ` for a section, read that range only.
- `Claude_Docs/` — everything else Claude works from (see Documentation layout below).
- `User_Docs/` — end-user setup guides (OpenTofu/Ansible install, WSL variants).
- `HANDOFF.md` — session-scratch handoff notes; its own header says it's not meant to be committed. Treat as ephemeral, not a source of truth.
- `Live-Testing.local.md` — gitignored; live-lab connection details, read only for live-testing tasks.
- `secrets.vault.example.yml` — template shape only; the real `secrets.vault.yml` is gitignored and never committed.
- `tofu/` — OpenTofu: `backends/{hyperv,libvirt}`, `modules/{vm,network}/{hyperv,libvirt}`, `environments/*.tfvars.example`. Real `.tfvars`/`environment.yml`/state are gitignored.
- `ansible/` — `playbooks/site.yml`, `roles/`, `package_catalog.yml` (committed catalog); real per-run manifests are gitignored.
- `packer/{windows,linux}/` — base-image templates (Claude_Docs/Design_Base-Images.md).
- `iso/answer-files/` — unattended-install answer files shared by Packer/direct-ISO-boot/template promotion.
- `scripts/` — `deploy.sh`/`destroy.sh` (entry points), `bootstrap-secrets.sh`, `check-network.sh`, `generate-inventory.py`, `lib/`, `test/` (see Tests below).
- `software-store/` — gitignored except `.gitkeep`; local installer binaries, don't read or commit.
- No file exceeds ~1,000 lines (largest: `create-iso-direct.sh`, 354 lines).

## Tests
- `scripts/test/run-all.sh` — runs every check (tools/collections installed, Ansible syntax, `tofu validate`, libvirt/WinRM connectivity, vault+SSH key consistency), prints a PASS/FAIL summary.
- Individual checks: `scripts/test/test-opentofu-config.sh`, `scripts/test/test-ansible-playbook-syntax.sh`, `python3 scripts/test/test-winrm-connectivity.py`.
- Redirect test output to a file and read only the summary or failures. Don't stream full test output into the conversation.
<!-- TODO: no unit-test framework (pytest/Molecule) exists yet. tflint/ansible-lint/Molecule are Claude_Docs/Design_System-Overview.md §16 goals, not implemented. -->

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
  - `Planning_Certificate-Authority.md`, `Planning_Environment-Templates.md`: proposals, not built yet (Design_System-Overview.md §19/§20).
  - `Planning_User-Docs-Backlog.md`: one line per user-visible change not yet covered in `User_Docs/`.
  - `Testing_Troubleshooting-Log.md`: open findings and blow-by-blow bug history, filed per milestone. Over the ~500-line guideline (636 lines) — left as one file since every entry is still actively linked; split fully-closed entries into an `Archive_Testing_...` file if it grows further.
  - `Archive_<OriginalStage>_<Topic>.md`: finished or superseded material. **Don't read `Archive_*` unless the user asks or the task needs history.** (None exist yet.)
- `User_Docs/`: end-user setup guides (`Install-OpenTofu.md`, `Install-Ansible.md`, and WSL variants of each).
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
