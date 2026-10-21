# Manual checks for v0.5.5

> **Stage: Testing.** Step-by-step checks for the things that unit tests and the lab runs could not cover: pages that were only type-checked, and the Kerberos and Director steps that only you can do. Written 2026-10-20 for the tester (Jesse). Each check says what to click, what you should see, and how to clean up. Record the result in the table at the end. Background: [Planning_Handoff-2026-10-19.md](Planning_Handoff-2026-10-19.md) §3, design §58-§61.

## Before you start

1. Use the lab's web interface: `https://oz.jlm.abq` (or `https://172.21.20.11`). Sign in as an **admin** (the lab's `labape-admin`; its password is in `~/LABaPe-web/tools/authentik-test/.env` on the lab host, never in a document or a chat).
2. Check **v0.5.5** is installed: the admin dashboard's version card shows the installed release. If it shows an older one, stop: the checks below are for v0.5.5.
3. Use only throwaway data. Name test items `zz-...` so they are easy to find and remove. Where a password is needed in an example, the placeholder is `ThisIsMy_FAKE_Password6!`.
4. If something fails, note the time, the page, what you clicked and the exact message. The same moment is in **Admin → User errors** (what the browser reported) and **Admin → Audit log**. Add the finding to [Testing_Troubleshooting-Log.md](Testing_Troubleshooting-Log.md) or tell Claude.

Time needed: about 2 hours for checks 1-8, plus the Kerberos and Director checks (9, 10).

---

## 1. API tokens: the console options (about 10 minutes)

**Why:** the New token dialog gained the console role and its limits (design §59.1); it was only type-checked.

1. Open the user menu (top right) → **API tokens** → **New token**.
2. Enter the name `zz-console-test`. Leave the kind as *Personal*.
3. Tick the role **console**. A box *Console access (commands and text sessions on VMs)* should appear. Untick it: the box disappears. Tick it again.
4. In that box check, one by one:
   1. **Only chosen environments**: ticking it shows an *Environments* list; pick `lab2`. Unticking it shows "Every environment the owner can use".
   2. **The call must name an account to run as**: ticked by default.
   3. **May open KVM host consoles (needs the admin role too)**: unticked by default; visible because you are an admin.
   4. Three number boxes: *Calls per minute* (30), *Session minutes* (30), *Output KB* (256). Type `0` in *Calls per minute* and press **Create**: it should be refused with a message (the limit is 1-600). Put 30 back.
5. Press **Create**. A dialog shows the token once. Do **not** copy it anywhere that keeps it (you only need to see that it appears); press **Done**.
6. In the list, the new row's Roles column shows `console` and, under it, a grey line like "console: lab2; needs an account; 30/min, 30 min sessions, 256 KB output".
7. Press **Revoke** on the row and confirm. The row should turn to *revoked*.

**Pass if:** every box behaves as above, the limit of 0 is refused, and the grey summary is correct.

---

## 2. Catalog: New package with the Contents picker (about 15 minutes)

**Why:** a package is now built from a picker instead of JSON (design §60).

1. **Catalog** → the **Packages** tab → **New package**.
2. Name `zz-test-pkg`, display name `ZZ test package`, a short description.
3. The **Contents** box: open it. Options are grouped under *Services*, *Applications* and *Packages*, each with its platforms (for example "windows, linux") beside it.
4. Press the **Windows** toggle above the box: only items that install on Windows remain. Press **Linux**: only Linux ones. Press **All OS**.
5. Pick one service and two applications (type to search). They appear as chips such as `application: 7-Zip`. Remove one chip with its `x`.
6. Press **Edit as JSON**: the definition shows `{"items": [{"kind": ..., "name": ...}]}` matching the chips. Edit nothing.
7. Press **Save as draft** and confirm. The page reloads for the saved item, status *draft*.
8. Back in **Catalog → Packages**: the item is listed with a *Platforms* column that is the combination of its members' platforms (for example a Windows-only service plus a cross-platform application gives "linux, windows").
9. Open the item and press **Publish**. It should publish if all its members are published. To test the rule: edit the package, add a member that is still a *draft* (an admin sees drafts in the picker with "(draft)"), save, and press **Publish** again: it should be refused with a message that everything in a package must be published. Remove the draft member and save.
10. Clean-up: **Retire** the test package (items cannot be deleted).

**Pass if:** the picker groups, filters and removes items correctly, the JSON matches, the platforms column is right, and the draft-member rule refuses.

---

## 3. New environment form: OS filtering, one role, group names, disk floor (about 15 minutes)

**Why:** design §60 and §60.1, and the 64 GB floor for Windows 11 (§61). **Nothing is created: do not press Create.**

1. **Environments → New environment**.
2. In the first host group set **OS** to `rocky9`. Under *Added on top of the template* open **Services**, **Applications** and **Packages**: they should list only items that install on Linux. Choose one item in each.
3. Change **OS** to `windows_server_2022`. Items that cannot go on Windows should disappear from the chosen lists; open the three boxes and check that only Windows-capable items are offered. (A package that mixes Linux and Windows items is offered for both.)
4. Look at **Role**: it is a single-choice drop-down labelled "Role" with the hint "One role per host group" (not a multi-select).
5. **Group name** checks (type each and look at the message under the box):
   1. `Web` is accepted (mixed case allowed).
   2. `web1x2y3z4w5v6` (13 characters) and `1web` and `we-b` show: "Use 1-12 letters and digits, starting with a letter."
6. *(Optional.)* Press **Add host group** and name the second group `web` while the first is `Web`. The form does not check this; the server does when you press **Create**, which is safe here: it refuses before anything is made. Give the environment the name `zz-dup-test`, fill the other required boxes, press **Create** and confirm: you should get a message that the group name is also used by host group 1 and that names are compared without case. Check afterwards that no `zz-dup-test` appears in the environments list.
7. Set the first group's **OS** to `windows_11` and **Disk (GB)** to `60`: the box shows "Use a whole number of GB from 64 to 2048." Set `64`: the message goes away.
8. Check the **Time zone** field exists and offers zones.
9. Leave the page without creating anything (apart from the refused attempt in step 6).

**Pass if:** the OS filter and the pruning on change work, Role is single-choice, the name rules appear, the 64 GB floor shows, and (if you did step 6) the duplicate-name message appears and nothing is created.

---

## 4. Environment accounts: "Sudo, no password" (about 10 minutes)

**Why:** a local account on a Linux VM can have passwordless sudo (design §61). **Do not save changes** on `lab2`.

1. Open the environment `lab2` (a deployed one). Find the **Accounts** section → **Edit**.
2. The table has the columns *Name, Kind, Hosts (local), Groups* and **Sudo, no password**. Existing accounts show their current state.
3. Press **Add account**. Kind *local*, name `zzsudo`, hosts: `l2lin1` (the Linux VM). The **Sudo, no password** checkbox is available; tick it.
4. Change the host from `l2lin1` to `l2win1` (Windows only) and press **Save**: it should be refused with a message that passwordless sudo needs a Linux host. If it saves, report it as a failure and remove the account at once.
5. Leave without saving: reload the page. Check the account `zzsudo` is not listed.

**Pass if:** the Sudo column is there, ticking works for a Linux host, and a Windows-only account with sudo is refused.

---

## 5. Delete a destroyed environment's record (about 10 minutes)

**Why:** a destroyed environment's record, secrets and grants can now be removed (design §61). **This is permanent for the throwaway `sudotest` (environment 32, destroyed 2026-10-20).**

1. Open `https://oz.jlm.abq/environments/32` (or find `sudotest` in the list; if destroyed environments are hidden by a filter, clear it).
2. Next to **Destroy** (greyed, because it is already destroyed) there is a red **Delete**. It shows only to an owner of a destroyed or never-deployed environment. Press it.
3. The dialog says "Delete sudotest?" and explains that the record, stored secrets, password rotations and snapshot records go, jobs and the audit log are kept. Press **Delete**.
4. You should be returned to the environments list, and `sudotest` is gone.
5. Open **Admin → Audit log** and filter for `environment.delete`: one row for the deletion, with counts.
6. Create-again check (optional): New environment with the name `sudotest` is accepted as a free name (cancel before creating).
7. Negative check: open `lab2` (deployed): there is **no** Delete button.

**Pass if:** Delete shows only for the destroyed environment, removes it, leaves an audit row, and `lab2` has no Delete.

---

## 6. Claude Desktop and the MCP server (about 25 minutes)

**Why:** the bundle and the console API are live; this checks them end to end, including the `session` fix (#180) and transcripts (#184).

**Setup:** Claude Desktop with the LABaPe extension (bundle `labape-mcp-0.5.4.mcpb` or newer, import steps in [Reference_Console-API-And-MCP.md](Reference_Console-API-And-MCP.md)) and a console token. If a tool call fails, read `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\Claude\logs\mcp-server-LABaPe.log`.

1. In a new Claude Desktop chat ask: *"Using LABaPe, list the environments I can open consoles on."* It should call `console_targets` and list `lab2` with `l2dc1`, `l2lin1`, `l2win1` (the token's environment list limits this).
2. *"Run `hostname` on l2lin1 in environment lab2."* Expect `l2lin1`. (If the token requires an account, name one, for example the local account you created for tests.)
3. *"Open a shell session on l2lin1, run `uname -a`, show me the output, then close the session."* This exercises `open_session`, `session_input`, `session_output` and `close_session` (the `session` argument). It must not say "missing argument(s)".
4. **Audit:** in the web interface open **Admin → Remote sessions**: new rows with protocol `exec` and `text`, your user, the VM, state *closed*. **Admin → Audit log**: `console.exec`, `console.session.open`, `console.session.input` (a byte count only, never the text you typed) and `console.session.close`.
5. **Recording:** open **Admin → Settings → Remote access**, note whether **Record every session** is ticked, tick it and save. Repeat step 3. In **Remote sessions** the new text row has **Download** and no **Play**; the downloaded `.txt` is the transcript. Put **Record every session** back as it was.
6. **Limits (optional):** make a second token with *Calls per minute* = 2 and ask Claude to run `hostname` four times quickly: later calls should report a rate-limit message. Revoke it afterwards.

**Pass if:** steps 1-4 work without argument errors, the audit and session rows are right, and the transcript downloads as text (step 5).

---

## 7. Remote session log: Play and Download (about 5 minutes)

**Why:** PR #184 (Play only for browser recordings).

1. Turn **Record every session** on (as in check 6.5) and open a browser SSH session to `l2lin1`: on the environment's **Secrets** page use the **Connect** icon on an account that can sign in to `l2lin1` and choose SSH. Run `ls`, then close the session.
2. **Admin → Remote sessions**: that row has **Play** and **Download**. The text rows from check 6 have only **Download**.
3. Press **Play** on the browser row: the recording plays (no "can't access property 'size'" error).
4. Set **Record every session** back as it was.

**Pass if:** Play appears only for the browser recording and plays.

---

## 8. Stack health after the update (about 5 minutes)

1. **Admin → Secrets → Health**: the OIDC client secret shows **by rotation** (not "not in use").
2. **Environment `lab2` → Preview redeploy**: three VMs, all *Kept*.
3. Dashboard: no red items, and the version card reports v0.5.5.

---

## 9. Director update to v0.5.5 (about 20 minutes, yours to click)

**Why:** Director (`pi1`) is still on v0.5.1 and has not had the last four releases; the click is yours.

**Risk:** this moves four releases and several database migrations in one step. The rollback script is `deploy/labape-rollback.sh` on the Director host.

1. Sign in to Director's web interface (the Director stack, not OZ). Open **Admin → Settings → Backups** and press **Back up now**; wait for it to show a new backup. (A copy of the data exists before you start.)
2. On Director's admin dashboard find the version card and press **Check now**. A dialog lists the choices (release, pre-release, roll back). It should offer **Update to v0.5.5**.
3. Choose it and confirm. The page may go unavailable for a minute or two while the stack restarts: wait; do not repeat the click.
4. Sign in again. The dashboard shows v0.5.5. Open **Admin → Secrets**: the elevated account `authentik-rotator` is still present and its health is not red.
5. Open **Admin → Audit log**: the update is recorded. Open the host list: OZ and Director are both listed and healthy.
6. If anything is wrong: stop, note the message, and tell Claude before trying the rollback script.

**Pass if:** Director reports v0.5.5, signs you in, and its secrets and hosts are healthy.

---

## 10. Windows sign-on (Kerberos) click-through (about 30 minutes, yours)

**Why:** Kerberos sign-in has been proved from scripts, never from a real browser on a Windows machine (design §58). The test machine is `krbw1` (Windows 11) in environment `krbwin11` (31), joined to `labape.test`.

**Before:** the domain controller `l2dc1` and `krbw1` are running; the clocks agree (Kerberos fails past 5 minutes of difference).

1. Open environment `krbwin11` → **Secrets**. Find the row for the domain account `jesse`; its *Sign in as* column shows `LABAPE\jesse`. The eye icon (**Show**) reveals the password and the copy icon copies it (both are audited).
2. On the same row press the **Connect** icon and choose RDP (it opens a browser session on `krbw1`; the `.rdp` file is the alternative). Sign in as `LABAPE\jesse` with that password.
3. On `krbw1` open **Edge** and go to `https://auth.labape.test:9445/`. The page should load with no certificate warning (the platform CA is trusted there).
4. Now go to LABaPe on the same name: `https://auth.labape.test/` and press the sign-in button. On Authentik's page choose **Sign in with Windows**. It should **not ask for a password**: you land in LABaPe signed in as `jesse`.
5. Open the user menu: the user name is `jesse` and the roles include `viewer`, and `admin` if `jesse` is in `lab2-admins`.
6. **Negative check:** in the same browser open `https://172.21.20.11/` (the IP, not the name) and sign in: Kerberos does not apply to an IP, so it asks for a password (or offers the normal sign-in). That is expected.
7. If step 4 asks for a password or fails, note the exact message and check, in this order: DNS for `auth.labape.test` (should be 172.21.49.100), the name in Edge's *Local intranet* zone (set by `krb_client_setup.ps1`), the clock skew, the certificate. Tell Claude what you saw.
8. **Clean-up** (after the check, whatever the result):
   1. In LABaPe: environment `krbwin11` → **Destroy** and confirm; when it shows *destroyed*, press **Delete** (record).
   2. On `l2dc1` (a console or RDP session as the domain admin) run in PowerShell: `Remove-ADComputer -Identity KRBW1 -Confirm:$false`. Then `Get-ADComputer -Filter "Name -eq 'KRBW1'"` should return nothing.

**Pass if:** step 4 signs in without a password, step 5 shows the right user, and the clean-up leaves no `KRBW1`.

---

## Findings from the first run (checks 1-5, 2026-10-20)

Fixed in the next release (details in [Testing_Troubleshooting-Log.md](Testing_Troubleshooting-Log.md)): revoked tokens hidden and purged (check 1); a published package refuses a draft item on save (check 2); the group-name and disk-floor messages show while typing, and the host-load bars line up (check 3); VM names shown beside roles in the accounts dialog (check 4); filter drop-downs apply on selection (check 5). To retest them, run the same steps once the next release is installed. Open as questions: domain member or standalone (D184), save as draft and duplicate an environment (D185), bind accounts to individual VMs (D186), add a host to an environment (D187). Checks 6-10 are still to do.

The fixes were then run in a browser by the browser agent (lab-exchange requests 03-09, results in `C:\Code\lab-exchange\results`): checks 1, 3, 4 and 8 passed; the rest of the findings are in the troubleshooting log under "Browser run of the fixes". The package check (2) is being rerun as request 10 and the second round of fixes retested as request 11.

## Results

| # | Check | Result (Pass / Fail / Skipped) | Date | Notes |
|---|---|---|---|---|
| 1 | API tokens: console options | | | |
| 2 | Catalog: New package picker | | | |
| 3 | New environment form | | | |
| 4 | Accounts: Sudo, no password | | | |
| 5 | Delete a destroyed environment | | | |
| 6 | Claude Desktop and MCP | | | |
| 7 | Remote sessions: Play and Download | | | |
| 8 | Stack health | | | |
| 9 | Director update | | | |
| 10 | Kerberos click-through | | | |

When all rows are done, give the table (and any failures) to Claude to file in `Testing_Troubleshooting-Log.md` and the handoff.
