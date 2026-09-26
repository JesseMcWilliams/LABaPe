# LABaPe: Claude Code project notes

LABaPe automates end-to-end provisioning and configuration of ephemeral,
self-hosted lab/test environments (mixed Windows + Linux servers and
workstations) on Hyper-V or libvirt/KVM, using OpenTofu to provision
infrastructure and Ansible to configure the OS, join Active Directory, and
install software. It's a polyglot repo — OpenTofu/HCL, Bash, Python helpers,
Ansible YAML/Jinja2, and some PowerShell — not a single-language target.

## Folder map
- `README.md` — overview: milestone status (M1-M5), quickstart, known gaps. Start here.
- `DESIGN.md` (~800 lines, §1-§20) — architecture, backends, OS support, credentials, networking, testing, milestones. Grep for `^## ` for a section, read that range only.
- `HANDOFF.md` — session-scratch handoff notes; its own header says it's not meant to be committed. Treat as ephemeral, not a source of truth.
- `Live-Testing.local.md` — gitignored; live-lab connection details, read only for live-testing tasks.
- `secrets.vault.example.yml` — template shape only; the real `secrets.vault.yml` is gitignored and never committed.
- `tofu/` — OpenTofu: `backends/{hyperv,libvirt}`, `modules/{vm,network}/{hyperv,libvirt}`, `environments/*.tfvars.example`. Real `.tfvars`/`environment.yml`/state are gitignored.
- `ansible/` — `playbooks/site.yml`, `roles/`, `package_catalog.yml` (committed catalog); real per-run manifests are gitignored.
- `packer/{windows,linux}/` — base-image templates (docs/base-images.md).
- `iso/answer-files/` — unattended-install answer files shared by Packer/direct-ISO-boot/template promotion.
- `scripts/` — `deploy.sh`/`destroy.sh` (entry points), `bootstrap-secrets.sh`, `check-network.sh`, `generate-inventory.py`, `lib/`, `test/` (see Tests below).
- `software-store/` — gitignored except `.gitkeep`; local installer binaries, don't read or commit.
- `docs/` — 12 topic docs extending DESIGN.md sections plus setup guides (see Docs table).
- No file exceeds ~1,000 lines (largest: `create-iso-direct.sh`, 354 lines).

## Tests
- `scripts/test/run-all.sh` — runs every check (tools/collections installed, Ansible syntax, `tofu validate`, libvirt/WinRM connectivity, vault+SSH key consistency), prints a PASS/FAIL summary.
- Individual checks: `scripts/test/test-opentofu-config.sh`, `scripts/test/test-ansible-playbook-syntax.sh`, `python3 scripts/test/test-winrm-connectivity.py`.
- Redirect test output to a file and read only the summary or failures. Don't stream full test output into the conversation.
<!-- TODO: no unit-test framework (pytest/Molecule) exists yet. tflint/ansible-lint/Molecule are DESIGN.md §16 goals, not implemented. -->

## Code rules (details in the linked sections, not repeated here)
- Backend implementations (`tofu/modules/{vm,network}/*`) must expose identical inputs/outputs so environments stay backend-agnostic — DESIGN.md §6.1/§6.2.
- A module `source` must be a literal string — no dynamic backend selection — DESIGN.md §6.3.
- Multi-role hosts need an explicit `group_names` union; Ansible doesn't merge `group_vars` lists by default — docs/software-manifest.md.
- Domain-join credential format is platform-specific (bare username on Linux, `DOMAIN\user` on Windows) — DESIGN.md §8.
- Validate before assuming it works: `tofu validate`, `ansible-lint`, `scripts/test/run-all.sh` — docs/validate-setup.md.

## Documentation layout
This repo predates the Claude_Docs/User_Docs convention:
- `README.md` — overview: milestone status, quickstart, known gaps.
- `DESIGN.md` — architecture and design decisions, in numbered sections (§1-§20). Keep current.
- `docs/*.md` — topic docs extending specific DESIGN.md sections, or standalone setup guides: Design (`base-images`, `directory-objects`), Planning (`certificate-authority`, `environment-templates` — not yet built), Reference (`credentials`, `networking`, `software-manifest`, `validate-setup`), user-facing setup guides (`install-ansible*`, `install-opentofu*`), Archive-candidate (`troubleshooting-log` — closed historical entries, still linked live).
- Rename docs with `git mv`, and update every link to them in the same change.

## Docs: what to update for each kind of change
| Change | Update |
|---|---|
| New feature | Relevant `DESIGN.md` section + matching `docs/*.md` detail doc; update README Status if it changes what works end-to-end |
| Bug fix | `docs/troubleshooting-log.md`; update README Known Gaps if applicable |
| Design decision | `DESIGN.md` relevant section, or §17 Open Questions |
| New gotcha | `docs/troubleshooting-log.md`, cross-link from README Known Gaps if user-facing |

- For "verify the docs are updated", use a subagent to diff the branch against this checklist and report the gaps only.

## Git
- Don't work directly on `main`. Create a topic branch named `YYYY-MM-DD-<topic>` and open a PR into `main` with `gh`.
- Commit, push, open a PR or merge only when asked. "Commit and push" means both.

## Live testing
- Lab environment details are in `Live-Testing.local.md` in the project root. That file is gitignored. **Read it only when a task involves live testing.** Never copy its contents into tracked files, commit messages or PR descriptions.
- If `Live-Testing.local.md` is missing, ask for the details. Don't guess.
- Never write secrets into any file, log or commit message, including `Live-Testing.local.md`. That file names *where* the credentials live, not the credentials themselves.
- When an example, doc or test needs a password placeholder, use `ThisIsMy_FAKE_Password6!`. It's obviously fake, and it satisfies typical complexity rules.
