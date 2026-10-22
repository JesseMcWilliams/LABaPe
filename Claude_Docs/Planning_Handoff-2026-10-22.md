# Handoff, 2026-10-22

> **Stage: Planning.** Where LABaPe stands at the end of the 2026-10-22 session, for whoever picks it up next (a new Claude Code session or a developer). Read this first, then [Planning_Handoff-2026-10-21.md](Planning_Handoff-2026-10-21.md) (its section 5 gotchas still apply) and [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) (the lab's access). It replaces the 2026-10-21 file as the starting point. LABaPe only.

## 1. State

`main` has everything below merged; **no PR is open**. The release **v0.5.6** was cut at the end of this session (see the CHANGELOG; if its tag is missing, `release.sh --resume`, see section 3). pytest: **486 passed, 1 skipped**. Playwright (`web/e2e`, run against OZ): **all pass** apart from the tests that skip themselves (nothing overdue to rotate, the three writes-only tests unless `npm run test:writes`). Questions continue from **196**; **D184, D185 and D186 are answered** (agreed as recommended and built); no question is open.

The user asked for the open questions and the backlog to be built batch by batch ("commit, push, PR and merge after each batch, don't wait"), then the user docs, this handoff and the release.

| PR | What | Design |
|---|---|---|
| #200 | **Playwright browser tests** (`web/e2e/`): every page plus lab-exchange requests 10-16; `package-lock.json` committed; `cross-env` makes `test:writes` work on Windows | `web/e2e/README.md` |
| #201 | One rule for the overdue warning and **Rotate overdue now** (`rotation.rotatable()`, `rotatable` per policy in the API) | log |
| #202 | Certificates **Issued** column; the Dashboard marks the stack you are on (**you are here**) | §66 |
| #203 | **Domain member or standalone** per host group (question 184): `domain_member`, `labape_domain_member` host variable, the join plays skip standalone VMs | §66 |
| #204 | **Save as draft, Edit draft, Duplicate** (question 185): status `new` is the draft, no quota, names or addresses until deployed | §66 |
| #205 | **Local accounts on chosen VMs** (question 186): `vms` on an account; the `for_host_group` filter takes the hostname | §66 |
| #206 | **Certificate attributes** (extended key usages, Signing, Encryption): off until a CA admin ticks the switch after `container/ca/enable-attributes.sh`; the issued certificate is checked and revoked if the CA did not apply them | §66 |
| #207, #209 | **Help**: the guides served by the stack (`api/help_docs.py`, `pages/Help.tsx`, a Help link on every page) | §66 |
| #208 | **Linked stacks' templates and files**: peer library and download with Range, Files → Linked stacks with Copy here, linked templates in the New environment drop-down copied at deploy | §66 |
| (docs) | `User_Docs/` brought up to date for all of the above (the user-docs backlog is empty) | |

## 2. How much of it has been seen

| Thing | Checked how |
|---|---|
| Everything | Unit tests; the web type-check; the Playwright run on OZ after the deploys (the Help link position was fixed after a failing run) |
| Domain choice, drafts, Duplicate, account VMs | Unit tests (`test_z4_domain_member.py`, `test_z5_draft.py`, `test_z6_account_vms.py`); the Ansible changes are one-line conditions (`site.yml`, `directory_objects.py` filter) and were **not** run through Ansible (no Ansible on the Windows box): a redeploy of an environment with a standalone group or a narrowed account is the real test |
| **Certificate attributes** | Unit tests with a fake CA only. `container/ca/leaf.tpl`, `step ca provisioner update --x509-template-file` and `step ca sign --set-file` were **never run against the real step-ca**; the steps to try are in `Testing_Troubleshooting-Log.md` ("Certificate attributes: not yet run live") |
| **Linked templates and files** | Unit tests with a fake fetch only; never run between two real stacks (log: "Linked stacks' templates and files") |
| Help, Issued column, you-are-here | Seen through the Playwright run on OZ |
| Still never run for real | Rotate overdue now (nothing was overdue at the end of the session: the user rotated the three), a real Add host, a certificate request by a plain `ca_requester` |

## 3. What's left (in order)

1. **Check the release.** `git tag -l v0.5.6` and `gh release view v0.5.6`; a run that stopped after the PR merged finishes with `tools/helpers/release.sh 0.5.6 <summary file> --resume` from clean `main`.
2. **Reset the lab clone and reinstall the agent** (every `deploy-web.sh` leaves `~/LABaPe-web` dirty): `git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main` then `~/LABaPe-web/deploy/update-agent/install.sh` (the auto-mode classifier refuses these over SSH from a session: the user runs them). Then **Director (`pi1`)**: Admin → Updates once v0.5.6 shows.
3. **Try the two unverified features** on the lab: certificate attributes (log entry gives the steps; undo is `step ca provisioner update labape-runner --x509-template ""` and unticking the switch) and a linked copy of an ISO and a template between OZ and the Director.
4. **Redeploy** an environment with a standalone host group and an account narrowed to chosen VMs, to see the Ansible conditions work.
5. **Rotate overdue now, for real** when something comes due; **a real Add host** on a throwaway environment; a **request by a plain `ca_requester`**.
6. **Replace the failing disk** `kvm1: /dev/sdc` (the user said to ignore it for now). **Authentik groups** `labape-console`, `labape-ca-admins`, `labape-ca-requesters` are not created yet. **Kerberos click-through** on `krbw1` (manual; Playwright would have to run on that machine).
7. **Later:** the published `.docx` guides (D24, `release.sh --docx`); the Kerberos domain side as an installer step; mail relays against a real outside SMTP account; the rest of the older queue in the 2026-10-19 handoff §3.

## 4. Lab state (2026-10-22)

| Thing | State |
|---|---|
| kvm1 / OZ | Runs the code of the release (deployed with `deploy-web.sh` from clean `main`), health ok. `~/LABaPe-web` is dirty until the reset in §3.2. Migrations: through `0032`; this session added **none** |
| Test data left behind | Catalog: `zz-draft-*` and `zz-pkg-*` draft items from the writes run (several), `zz-test-pkg`; revoked tokens `zz-console-test`; three revoked test certificates `zz-cert-test.auth.labape.test`; lab2's accounts were saved once by a test (no change). None can be deleted from the UI |

## 5. Gotchas learned this session

1. **The auto-mode classifier** blocks SSH commands that write on the lab host (a `git reset --hard` there) and also a read that followed in the same turn; `deploy-web.sh` and reading are allowed. Ask the user to run lab-side resets.
2. **Heredocs in the Bash tool** with many quotes sometimes fail to parse (`unexpected EOF`): write the script with the Write tool and run it. Python edits of CRLF files go through `open(p, newline='')`.
3. **MUI labels in Playwright:** a select's role is `combobox` and its accessible name includes its value, a `getByLabel("OS")` also matches "KVM host"; the sticky app bar hides what scrolls under it, so overlapping a link with the page header makes a click time out.
4. **`status "new"` is the draft** (it already meant "never deployed" and was already deletable and inactive); `quotas.usage` and the VM-name check leave it out.
5. **step-ca applies its provisioner's template and ignores a CSR's usages**, which is why attributes need the template and the after-signing check.
6. The helpers used for this session's batches are in the session scratchpad only (a doc-editing script and a ship script); `tools/helpers/` has the reusable ones.

## 6. Where things are

| Need | Look at |
|---|---|
| This session's work | design §66 (`Planning_Web-Interface-Design.md`); `domain_member.py`, `api/environments.py` (`_prepare`, `save_draft`), `env_accounts.py`, `certs.py` (`USES`), `container/ca/`, `api/help_docs.py`, `linked_library.py`, `engine/linked_copy.py`; web: `pages/Help.tsx`, `components/HelpHere.tsx`, `components/Markdown.tsx`, `pages/Files.tsx` (Linked stacks) |
| The browser tests | `web/e2e/README.md`; run with `LABAPE_URL=https://oz.jlm.abq npm run test` after `npm run login` |
| Everything else | the 2026-10-21, 2026-10-19 and 2026-10-16 handoffs §6-§7 |
