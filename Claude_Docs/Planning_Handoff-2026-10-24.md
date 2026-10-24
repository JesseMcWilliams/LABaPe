# Handoff, 2026-10-24

> **Stage: Planning.** Where LABaPe stands at the end of the 2026-10-23/24 session, for whoever picks it up next (a new Claude Code session or a developer). Read this first, then [Planning_Handoff-2026-10-23.md](Planning_Handoff-2026-10-23.md) (its gotchas in §5 still apply) and [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) (the lab's access). It replaces the 2026-10-23 file as the starting point. LABaPe only.

## 1. State

**Latest release:** v0.7.0 (2026-10-24)

The release v0.7.0 is being cut from this state (see §3.1). `main` has everything below merged. pytest: **597 passed, 2 skipped** (Windows scratch venv, about 6 minutes). `npm run build` passes (Node is installed on the Windows box now). Playwright against OZ: **49 passed, 7 skipped**.

The session was a project review of security, code, features, tooltips and the guides, then building what it decided, then running it live.

| PR | What |
|---|---|
| #249, #250, #251 | The SMART flag on Drives and volumes; handoffs; the review itself |
| #252 | **Review quick fixes**: browser security headers, CSV formula cells, tooltips on the remaining buttons, router list one per line, dead code in `hostio.py`, guide and Help gaps; the review doc and questions 196-202 |
| #254 | **196** a personal API token stops working when its owner is deactivated or removed in Authentik (`userstatus.py`, a sweep every 15 minutes, migration 0036); **197** `breakglass revoke` ends open sessions; **198** SSH host keys and the WinRM certificate fingerprint are recorded after a deploy and checked on later connections (`engine/vmidentity.py`, migration 0037, `labape vm-identity`) |
| #255 | **199** Download from URL refuses loopback, localhost, single-label names and the container network; **200** grants and role bindings tied to the account (migration 0038); **201** a Sharing panel and hand-over (`api/env_sharing.py`); **202** Admin → Users (`api/users.py`) |
| #256 | The four bugs the live run found (§2) and the live-test scripts |
| (release PR) | v0.7.0: the guides brought up to date from the backlog, this handoff, the `.docx` guides |

Questions 196-202 are answered and built; the next free question is **203**. The review is in [Planning_Project-Review-2026-10-23.md](Planning_Project-Review-2026-10-23.md); the live results are in the log ("Live run of the review's decisions").

## 2. How much of it has been seen

| Thing | Checked how |
|---|---|
| 196-202, the Caddy headers | **Live on OZ** (2026-10-24): scripts in `tools/helpers/live/` (listed in its README), the browser spec `web/e2e/tests/review-ui.spec.ts`, and two test environments (Debian, Windows Server 2022) deployed and destroyed |
| Found and fixed by the live run | The headers were only in `Caddyfile`, not in `Caddyfile.labape-ca`/`.names` (the lab uses those); the Directory token's role could not view users, so the check was inert (the Fix and the blueprint now grant `authentik_core.view_user`; **an existing stack runs Fix on the directory check once**); guacd reads exactly one known_hosts entry and negotiates ECDSA first; a key learned at first use belonged to no environment |
| **Never run for real** | Ansible's deploy-time host-key and certificate checks (unchanged, still accept what they first see); RDP; an environment on a remote KVM host (its VMs are learned at first connection); a VM rebuilt outside LABaPe under the same address; the Users page's Disable with a user who has open remote sessions |

## 3. What's left (in order)

1. **Release v0.7.0** (this session): CHANGELOG, versions, the SBOM from a worker image built from clean `main`, the `.docx` guides (`release.sh --docx`, needs pandoc), signed tag, GitHub release.
2. **The Director (the user):** update to v0.7.0 and run **Fix** on the dashboard's directory check; redeploy or destroy the half-made `Single1` VM on `garagepc` once its ISO is there.
3. **Reset the lab clone and reinstall the agent** (the user runs them): `git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main`, then `~/LABaPe-web/deploy/update-agent/install.sh`. OZ runs a working tree (`-dirty`); after the release, update it from the tag so it reads the release.
4. **Delete the test template** `debian13-linktest-2026.10` from OZ when the linked-template tests are done (handoff 2026-10-23 §3.3).
5. **Folder check on `garagepc`**, the drive flag for `kvm1: /dev/sdc` in a browser, **replace the failing disk**, the Kerberos click-through (handoff 2026-10-23 §3).
6. **Still open from the review:** items 8-14 and 18 (small security items such as the unchecked tus `Upload-Length`, the public `/api/docs`, floating image tags and the root API image; the files over 1,000 lines; imports inside functions; Hyper-V remnants; a Postgres test run; a session that expires mid-use; help text on the token form and Settings fields).
7. **Later:** moving the Windows WinRM listener to a platform-CA certificate (so WinRM can be checked against the CA, not pinned); Ansible's own host-key checks; closing open remote sessions when a user is disabled; the older queue in the 2026-10-19 handoff §3.

## 4. Lab state (2026-10-24)

| Thing | State |
|---|---|
| kvm1 / OZ | Runs the tree of PR #256 (`v0.6.2-20-…-dirty`), migrations through **0038**; the Caddy web server was restarted for the new headers; every check green. `~/LABaPe-web` is dirty (the deploy overlays files) |
| Test Authentik | Its blueprint was re-applied (the directory role views users); the test users' passwords were reset by it, as the blueprint does |
| Test data left behind | The template `debian13-linktest-2026.10`; the earlier `zz-` items and revoked test certificates; the destroyed environments `zz-rv1`, `zz-rv2` (no VM records are left); `web/e2e/state/admin.json` holds a `labape-admin` test session |

## 5. Gotchas learned this session

1. **The lab uses `Caddyfile.labape-ca` or `Caddyfile.names`** (`LABAPE_CADDYFILE` in `container/.env`): a web-server change goes into all three files; it is a bind mount, so `docker compose restart caddy`.
2. **guacd's `host-key` is one known_hosts entry**, and guacd negotiates ECDSA first; several lines, or an ed25519 or RSA line when ECDSA is offered, abort the session ("Aborted. See logs.").
3. **`deploy-web.sh` while a deploy runs kills it** (it recreates the worker): check the jobs first.
4. **A heredoc or Python string with `\n` inside breaks edits** (a literal newline lands in the code): use the Edit tool for such lines, and run the file's tests before deploying.
5. **A stacked PR closes when its base branch is deleted** after the first merges: merge `main` into the second branch and open a new PR.
6. **The Directory token's role decides what the provider check can see**: with a 403 the sweep logs a warning and changes nothing (safe, but no protection).
7. **A token's owner marked disabled has the tokens revoked at that moment**; the sweep clears only its own mark (`disabled_reason = provider`), never an admin's.

## 6. Where things are

| Need | Look at |
|---|---|
| Tokens ending with their owner, Users | `app/labape/userstatus.py`, `api/users.py`, `web/src/pages/Users.tsx` |
| VM identity (SSH keys, WinRM certificate) | `app/labape/engine/vmidentity.py`; used by `engine/vmexec.py`, `engine/vmadmin.py`, `remote.py`, `engine/runner.py` (record after a deploy, clear on deploy and destroy) |
| Grants tied to the account | `security.py` (`subject_key`, `adopt_subject`, `resolve_roles`), `permissions.py` |
| Sharing and hand-over | `api/env_sharing.py`, `web/src/components/Sharing.tsx` |
| Live checks | `tools/helpers/live/` (README table), `web/e2e/tests/review-ui.spec.ts`, the log's "Live run of the review's decisions" |
| Everything else | the 2026-10-23, 2026-10-22, 2026-10-21 and 2026-10-19 handoffs §6-§7 |
