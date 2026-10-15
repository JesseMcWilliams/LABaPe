# Handoff, 2026-10-15

> **Stage: Planning.** Where LABaPe stands after v0.4.0 and V2, for whoever picks it up next (a new Claude Code
> session or a developer). Read this first, then the documents it points to. LABaPe only: aPeDiscovery,
> LogaPe, aPeSecrets and LABaPe are separate projects with their own handoffs.

## 1. State in one paragraph

Everything planned is built and merged: `main` at `f05ad62` (PR #131), no open PRs. **v0.4.0** is released
(signed tag on `67a6697`, PR #130). V2 (VMs reading their own secrets) merged after the release, so the next
release starts with it in `CHANGELOG.md`'s `[Unreleased]`. Tests: 323 passing (`pytest app/tests`). No batch
is in progress and no question waits on the user.

## 2. What was built recently (since v0.3.0)

Design sections in [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md); plans in the
`Planning_*` files named below.

| Round | Batches | Design |
|---|---|---|
| Backlog (Planning_Backlog.md) | P1b spreading over hosts, P2 live migration, F1 sign-on between stacks, R1 release script | §45-§47 |
| [Planning_Lists-Secrets-Settings.md](Planning_Lists-Secrets-Settings.md) | L1/L2 paging, filters, exports; N1 names and SANs; S1 secret health; S2 secrets sections; S3 settings sections; D1 loop devices; A1 recovery pairs; A2 a password per VM; V1a environment secrets page and OpenBao folders; V1b OpenBao UI with OIDC; V2 VMs' certificate sign-in | §48, §49 |
| [Planning_Access-Mail-Updates-Files.md](Planning_Access-Mail-Updates-Files.md) | H1 monitoring tools; E1 Secrets paging; F1 files path per host and free-space alert; E2 connect as an account; E3 xrdp/VNC on Linux; M1 Mailpit; U1 Catalog → Updates; P1 .NET, Chrome, KeePassXC, PuTTY, Claude Code | §50 |

## 3. What's left (all "later" by the user's choice)

| Item | Where | Note |
|---|---|---|
| Kerberos sign-on | Planning_Backlog.md row 16; questions 29, 154 | Needs domain-joined clients and an SPN for the web address. |
| Hyper-V parity | Planning_Backlog.md row 17; questions 11-14, 155 | Order in [Reference_Backend-Parity.md](Reference_Backend-Parity.md); the nested `hvhost1` VM on kvm1 is shut off. |
| Published `.docx` guides | question 24 | `tools/helpers/release.sh --docx` makes them (needs pandoc); not used yet. |
| Next release | `CHANGELOG.md` `[Unreleased]` | Only when the user says (releases at milestones). |

Small follow-ups noticed, not asked for:
1. Chrome's version in Catalog → Updates is an ETag fingerprint (its link has no version); reading the MSI's ProductVersion would be friendlier.
2. The lab mail relay (M1) hasn't been tried against a real outside SMTP account.
3. `tools/secrets-test/deploy.sh`'s Conjur smoke message says the key "no longer signs in" even when it was only removed from `.env`.
4. ~90 old merged branches remain locally and on GitHub; `origin/HEAD` once pointed at an old branch.

## 4. How the work is done here

- **Read first:** `CLAUDE.md` (layout, tests, doc rules), then this file, [Planning_Questions.md](Planning_Questions.md) (numbered; new ones continue from **174**), [Testing_Troubleshooting-Log.md](Testing_Troubleshooting-Log.md) (what broke and why).
- **Shipping:** a `YYYY-MM-DD-<topic>` branch before editing; pytest; live check on the lab; docs (design section, CHANGELOG `[Unreleased]`, user guides, plan row); `tools/helpers/ship.sh <title> <commit-msg-file> <pr-body-file>` commits, pushes, opens the PR and merges it with `--delete-branch`, and leaves you on `main`. Releases with `tools/helpers/release.sh` only when the user asks.
- **Lab:** details are in the gitignored `Live-Testing.local.md` (read it only for live work; never copy it into tracked files). `tools/helpers/deploy-web.sh` puts the working tree on the lab stack (run the tests first: a syntax error once crash-looped the worker); `tools/helpers/lab-live.sh <script>` runs `tools/helpers/live/*` there. The helpers' README lists every script.
- **Root on lab hosts is the user's.** Give exact commands with full paths, run as `sudo -u AutomationUser ...`; ask before anything destructive. `deploy/update-agent/install.sh` needs a user session (systemd user bus): run it over SSH as AutomationUser, not through `sudo -u`.
- **Secrets:** never print, log or commit a value; the placeholder password is `ThisIsMy_FAKE_Password6!`.

## 5. Lab state

| Thing | State |
|---|---|
| kvm1 (host 1) | Runs the main stack at `main` (v0.4.0 + V2); clone `~/LABaPe-web` clean. `.env`: `LABAPE_OPENBAO_PEOPLE=yes`, `LABAPE_OPENBAO_BIND=0.0.0.0` (OpenBao UI on 8200 with OIDC, VMs on 8210). Default store for new environments: OpenBao. Catalog → Updates has downloaded ~1 GB into installers. `/dev/sdc` is failing (reported earlier). |
| nkvm1 (host 2) | Nested KVM VM (environment `nestkvm`, id 22), service user `kvmsvc`, files path `/data/Files`. Keep (user). |
| Raspberry Pi stack (`director`) | Its own LABaPe stack, linked to kvm1 with sign-on both ways; at v0.3.0-era code (not rebuilt since); its sleep targets weren't masked. garagepc (host 4) is the Pi's KVM host; disabled in kvm1's stack. |
| lab2 (environment 3) | DC `l2dc1`, member `l2win1`, Linux `l2lin1`. Its secrets are in OpenBao (`env/lab2/`); each member has its own passwords; domain account `jesse` (Domain Admins), local account `deskuser` on `linux_server`; xrdp and VNC on group `l2lin`; `l2win1` has .NET 8 runtime, PuTTY and Claude Code from the P1 test; the VM helpers (`labape-secret`, `Get-LabapeSecret`) installed. Keep (user). |
| syslog1 (environment 11) | The lab's syslog collector VM. Keep (user). |
| Destroyed | Test environments `mailtest` and `p1lin` (2026-10-15), `accttest1` earlier (one stale account password row from before the destroy fix). |

## 6. Gotchas worth knowing before touching these areas

1. **Shell escapes:** in this environment the Bash tool turns `\n` inside commands and heredocs into real line breaks; write Python edit scripts with the Write tool, not inline (Testing_Troubleshooting-Log.md has the cases).
2. **WinRM password changes:** never in a detached (async) task: the process loses the account database once the shell closes. Change in the same shell and let the next sign-in decide (design §48.9).
3. **OpenBao privileges:** LABaPe must never write or attach OpenBao policies. People's (§49.2) and VMs' (§49.3) policies come from `container/stores/stores.sh` templates run with the root token by the update agent; an `allowed_parameters` glob can't restrict a `policies` list (a comma string passes it).
4. **OpenBao bind:** `LABAPE_OPENBAO_BIND` must be `0.0.0.0` (or loopback); one host address drops the loopback publish the host's own tools use, and OpenBao stays sealed and unreachable.
5. **Environment names are OpenBao folders** (`env/<name>/`), and a new environment secret joins the store its environment already uses.
6. **Authentik:** each application has its own issuer (`/application/o/<slug>/`); an application without a launch URL opens its first redirect URI.

## 7. Where things are

| Need | Look at |
|---|---|
| How a feature works | Planning_Web-Interface-Design.md, the § in §2 above |
| Why something broke before | Testing_Troubleshooting-Log.md |
| Open and answered questions | Planning_Questions.md |
| Credentials and where they live | Reference_Credentials.md, Reference_Secrets-Backend-Requirements.md |
| User-facing behaviour | User_Docs/Web-Interface-Guide.md, Web-Interface-Admin.md, Command-Line.md |
| Release history | CHANGELOG.md, GitHub releases |
