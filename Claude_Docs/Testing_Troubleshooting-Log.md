# Troubleshooting log

Detailed, blow-by-blow investigation history for bugs that took real
back-and-forth against live infrastructure to run down — kept for the
record because the ruled-out alternatives are as useful as the fixes.
[README.md](../README.md)'s Known Gaps section links here for anything
beyond a one-line summary.

## M1 (libvirt): bugs found getting the first end-to-end run working

The libvirt/Linux/bridged/iso_direct path (M1) has been run end-to-end
against real infrastructure — see README's Status. Getting there
surfaced and fixed several real bugs the original scaffold couldn't
have caught without a real `tofu apply`/`virt-install`/kickstart run:
an invalid OpenTofu precondition, `virt-install` called with an
incompatible flag combination, a libvirt-internal race when creating
multiple VMs concurrently, a kickstart `network` line using an option
this Anaconda version doesn't accept (silently left installs hanging
indefinitely rather than failing fast), no DNS resolver for static
addressing, a missing EPEL repo dependency, a timing race between a VM
finishing its post-install reboot and Ansible's first connection
attempt, and a pre-flight network check that couldn't tell its own
already-running VMs apart from a real address conflict. None of this
was reachable by static review alone.

## Windows answer-file deserialization failure (libvirt backend)

Windows Server support (2019/2022/2025) exists on the libvirt backend —
ahead of Claude_Docs/Design_System-Overview.md §18's original M3 schedule — and the long-standing
intermittent-failure bug below is now **RESOLVED (round 10)**. All
three versions use the same `autounattend.xml`-based unattended install
as Linux's kickstart path (`iso/answer-files/windows/`): partitioning,
static/DHCP addressing, WinRM bootstrap (HTTPS listener, self-signed
cert, Basic auth), and a working `windows_common` Ansible role
(Chocolatey packages).

**Root cause and fix:** `create-iso-direct.sh` now strips XML comments
from the answer-file copy it writes to the CD-ROM
(`iso/answer-files/windows/autounattend-windows-server.xml.tpl` keeps
its full documentation — only the copy Setup actually reads is
stripped). Confirmed end-to-end post-fix: full unattended install in
4m17s (previously either a fast success or an hour-long timeout with no
middle ground) with Ansible/WinRM connecting and installing packages
cleanly. See round 10 below for how this was actually found — pulled
Windows Setup's own log via a WinPE shell rather than more behavioral
guessing — and rounds 1-9 for the full history of ruled-out
alternatives.

Ten rounds of follow-up investigation, each tested against real
infrastructure rather than just proposed (round 10 found the actual fix
— the rest are kept for the record, since they're what narrowed it down
and ruled out a lot of plausible-looking dead ends):

1. A real historical virt-install bug — multiple CD-ROMs getting a
   non-deterministic *boot order* — was found and confirmed already
   fixed upstream years before the version in use here. Doesn't match
   our symptom anyway: the primary Windows ISO always boots and shows
   Setup's UI reliably; it's specifically post-boot answer-file
   *detection* on the secondary CD that fails intermittently.
2. Rebuilding the secondary answer-file ISO with
   `-iso-level 4 -untranslated-filenames` (avoiding Joliet/short-name
   ambiguity, per a real-world "automate Windows install in a VM"
   guide) made things *worse* — the guest crashed within under a minute
   instead of idling at Setup's language screen.
3. Injecting `autounattend.xml` directly into `boot.wim` (both WinPE
   images, via `wimupdate`) and rebuilding the full install ISO around
   it — mirroring how the Linux kickstart path injects straight into
   what's booting, rather than relying on a separately-scanned
   secondary device at all. This produced a **genuinely different
   failure signature**: sustained ~100% CPU for 6+ minutes with the
   screen never advancing past `Booting from DVD/CD...`, vs. every
   previous failure's near-0% CPU idle — real evidence the original
   failures actually were "Setup never finds the file," since this
   approach bypasses that detection step entirely and still fails, just
   differently. `xorrisofs -iso-level 4 -untranslated-filenames` broke
   the boot catalog outright ("Couldn't find BOOTMGR"); `-iso-level 3 -J
   -rock` fixed that specific error but still hung.
4. Tried to fix (3) surgically — patch just `boot.wim` inside the
   *original*, still-bootable ISO via `xorriso -indev/-map/-boot_image
   any replay` instead of having `xorrisofs` regenerate the whole image
   — but `xorriso`'s reader genuinely cannot parse the UDF filesystem
   these ISOs use (confirmed: `-indev` on the untouched original ISO
   sees only one file, `/README.TXT`, at the top level). Any `-outdev`
   write from that state silently discards everything it couldn't see —
   produced a 56KB "ISO" from an 8GB source. Real tooling dead end, not
   a config mistake.
5. Reviewed Microsoft's official
   [Windows Setup Automation Overview](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-setup-automation-overview?view=windows-11)
   for the actual implicit answer-file search order. Confirms read-only
   removable media (a second CD-ROM) genuinely is searched (position 5
   of 8), just after read/write removable media (position 4) — so the
   mechanism *should* work, matching that it does succeed some of the
   time. Also surfaced an untried location — the `\Sources` directory
   of the Windows distribution itself (position 6, windowsPE/
   offlineServicing passes) — so tried adding `sources\autounattend.xml`
   (and, for good measure, one at the ISO root too) to a rebuilt primary
   ISO. Hit the exact same "high CPU, screen never advances" hang as
   (3). A **control test — rebuilding the ISO via the identical
   `xorrisofs` recipe with zero modifications at all — hung
   identically**, which conclusively isolates that hang to `xorrisofs`
   not correctly reconstructing this specific Windows ISO's boot
   structure, completely unrelated to autounattend.xml placement. So
   the "modify the primary boot media" family of approaches is blocked
   by this separate, real tooling limitation, not by anything about the
   answer-file mechanism itself — and the original secondary-CD-ROM/
   floppy mystery remains exactly that, a mystery, now with the added
   confirmation that it *shouldn't* be failing per Microsoft's own
   documented behavior.
6. Gave Windows guests a permanent VNC display (`--graphics vnc` instead
   of `none`) so failures are actually watchable live rather than only
   via post-mortem forensics, then used that to add two new data points
   instead of more theorizing. First: reproduced the failure live, then
   opened a WinPE command shell (`Shift+F10`) and confirmed with `wmic
   logicaldisk`/`dir` that `autounattend.xml` was present, correctly
   labeled, and byte-for-byte readable at `E:\` (a second SATA CD-ROM)
   at the exact moment Setup was idling at the language screen — not a
   placement or format problem. Repeated the same live check with the
   answer file on a virtual floppy instead (`A:\`, confirmed present and
   readable the same way) — identical failure. Two structurally
   different delivery mechanisms, both independently confirmed
   present-but-unused, which rules out media-format/placement and
   narrows this to *something about Setup's search itself* not finding
   a file that's genuinely there. Second: retried the "modify the
   primary boot media" family once more with a fresh, previously-untried
   `mkisofs`/`xorrisofs` recipe from an independent source
   ([palant.info](https://palant.info/2023/02/13/automating-windows-installation-in-a-vm/))
   that reportedly works for that author. It re-hit exactly the two
   failure modes (3) and (5) above had already isolated — no
   `-boot-info-table` → "Couldn't find BOOTMGR"; with it → the guest
   boots past that but the host's `qemu-system-x86_64` process pins at
   ~108% CPU indefinitely with the screen frozen at
   `Booting from DVD/CD...`. Confirms (3)-(5)'s conclusion rather than
   finding a way around it: this is a real `xorrisofs`/toolchain
   incompatibility with this specific Windows ISO's boot structure on
   this host, not a flag combination nobody had tried yet. Net result:
   the original secondary-CD-ROM approach remains the least-bad option
   — reverted to it after this round (Claude_Docs/Design_Base-Images.md §4,
   `create-iso-direct.sh`) — and the underlying mechanism is now more
   thoroughly ruled *in* to Windows Setup's own search logic than ruled
   out of this repo's control, without a fix in hand.
7. Tried renaming the file on the secondary CD-ROM from
   `autounattend.xml` to `unattend.xml`, on the chance Setup's search
   was keying on the wrong name for this position. Identical failure
   (idle at the language screen). Expected in hindsight — Microsoft's
   documented search order uses `autounattend.xml` specifically for the
   pre-install/windowsPE pass on removable media, `unattend.xml` is for
   later passes against an already-installed system — but worth ruling
   out directly rather than assuming. Reverted to `autounattend.xml`.
8. Checked whether [CVE-2026-0386](https://support.microsoft.com/en-us/servicing/os/windows/2025/12/windows-deployment-services-wds-hands-free-deployment-hardening-guidance-related-to-cve-2026-0386)
   — Microsoft's fix disabling "hands-free" answer-file deployment —
   could explain this. It doesn't apply here: the hardening is scoped
   specifically to Windows Deployment Services (WDS) network/PXE
   deployment, where an `unattend.xml` is exposed over an unauthenticated
   RPC channel via the `RemoteInstall` share; Microsoft's own guidance
   states it applies "only to native... WDS scenarios," and the patch
   lands on the *WDS server role*, not client-side Setup.exe/WinPE. This
   repo has no WDS/PXE involved anywhere — Setup boots straight from a
   locally-attached ISO — and the install media itself predates the fix
   by years regardless. Tested anyway in case of an undocumented
   interaction: a direct `virt-install` smoke test against the
   **Windows Server 2019** ISO (untouched by any prior round) with the
   same secondary-CD-ROM mechanism hit the identical language-screen
   failure. Confirms both that this CVE isn't the cause and that the bug
   is version-independent across 2019/2022/2025, not something specific
   to the 2022 media used for every prior round.
9. Tried a third, structurally different delivery mechanism: the answer
   file on a 64MB FAT32 image attached as removable USB storage
   (`virt-install --disk ...,bus=usb,removable=on`) rather than a second
   CD-ROM or a floppy — real-world unattended-Windows guides often
   specifically recommend USB for this. Windows *did* treat it
   differently: WinPE enumerated it as `C:\`, "Removable Disk" (a
   distinct category from "CD-ROM Disc"), rather than the CD-ROM/
   floppy's drive letters. Identical outcome anyway — idle at the
   language screen, and a WinPE shell again confirmed `autounattend.xml`
   present and byte-correct at `C:\` the moment it happened. Three
   structurally unrelated delivery mechanisms (CD-ROM, floppy, USB) have
   now each independently confirmed the same signature: the file is
   genuinely there and readable, Setup just doesn't act on it, roughly 3
   times out of 5. That pattern is hard to square with a per-media-type
   detection bug and points increasingly at something in Setup's own
   answer-file validation/application step being racy — independent of
   how the file arrives — though this remains inference from
   elimination, not a confirmed root cause.
10. **Root cause, found directly instead of inferred — and fixed.**
    Rounds 1-9 were all behavioral inference from the outside (screen
    state, drive contents) because the one thing never actually checked
    was Windows Setup's own account of what it did. Reproduced the
    failure live, opened a WinPE shell (`Shift+F10`), and searched
    `X:\Windows\setupact.log` (`cmd`'s built-in `find`, not `findstr` —
    not present in this WinPE build) for "nattend". It was all there:
    `Determining if we are in WDS/Unattend mode` followed by
    `[setup.exe] UnattendSearchExplicitPath: Found unattend file at
    [E:\autounattend.xml] but unable to deserialize it; status = 0x1,
    hrResult = 0x800705b9`. Not a detection problem at all — Setup finds
    the file *every single time* — a generic XML-parse failure. Ruled
    out one candidate directly: an explicit `sync` on the answer-file
    ISO before `virt-install` ever opens it (in case an un-flushed write
    was racing the guest's very early read) made no difference
    whatsoever — identical error, byte-identical file. What actually
    explained it: this repo's answer-file `.tpl` has several multi-line
    XML comments (`<!-- ... -->` spanning many physical lines)
    documenting the file for developers — and round 1's own tenforums
    research had *already* surfaced a forum thread describing this exact
    "unable to deserialize" failure with the exact same root cause
    (Windows Setup's XML deserializer chokes on multi-line comments
    despite them being perfectly valid XML) — that thread just hadn't
    been connected to this bug until the log line made the connection
    obvious. Fix: `create-iso-direct.sh` now strips XML comments
    (`perl -0777 -pe 's/<!--.*?-->//gs'`) from the copy it writes to the
    CD-ROM, leaving the `.tpl` source's own documentation untouched.
    First real end-to-end test after the fix completed the unattended
    install in 4m17s (previous "successes" were the same ballpark;
    previous failures were the full 3600s timeout with no middle
    ground) with Ansible/WinRM connecting and installing packages
    cleanly. This also retroactively explains why it was *intermittent*
    rather than a flat 100% failure: whatever about Setup's deserializer
    makes multi-line comments sometimes tolerable and sometimes not was
    never identified, and doesn't need to be now that the comments are
    simply gone from the file Setup actually reads.

## M2 (Hyper-V): the "SSH unreachable" / "install stalled" investigation

**Resolved. Root cause was never sshd or networking — the VM was never
actually booting the installed OS.** Three logging/capture channels
(below) were built to chase what looked like "install completes, guest
is healthy (`Heartbeat` OK, VHD grew, uptime counter reset), but SSH is
unreachable for 20+ minutes." All three came up completely empty — and
retrospectively, that's because they were investigating a false
premise: a real, fully-installed guest was never actually failing to
answer SSH.

**Generation 1 Hyper-V VMs default their BIOS boot order to DVD before
hard disk** (`Get-VMBios`'s `StartupOrder`:
`{CD, IDE, LegacyNetworkAdapter, Floppy}`), and this module leaves the
boot ISO and kickstart ISO permanently attached. So the kickstart
install genuinely *did* complete successfully every time (confirmed:
matches this module's real install duration, ~20 minutes) and reboot —
straight back into the same boot ISO, re-running the unattended install
from scratch, forever. Every symptom (VHD growth stopping, "connection
refused" then timeouts, `Heartbeat` sometimes OK/sometimes not) was a
snapshot of *some* point in that infinite loop, not a stalled or
unhealthy guest.

Found by direct observation rather than more inference:
`vmconnect.exe <hyperv-host> <vm-name>` (the actual Hyper-V console —
see below) showed the installer's own summary screen mid-loop ("Not
enough free space on selected disks" from an earlier, unrelated
debug-disk-related kickstart bug — see below), and once that was fixed,
showed the boot menu itself defaulting back to "unattended install"
instead of the newly installed OS after a completed install.

**Fix**: `Set-VMBios -StartupOrder` with `IDE` before `CD`
(`tofu/modules/vm/hyperv/scripts/set-boot-order.sh`). Confirmed
end-to-end after the fix: the guest boots the installed OS,
`Heartbeat`/`Shutdown`/`Time Synchronization`/`VSS` integration services
all report `OK`, and `ssh labape@<ip>` succeeds. (`Key-Value Pair
Exchange` still shows `No Contact` — minor, likely just the
`hyperv-daemons` package/`hv_kvp_daemon` not being installed by default;
doesn't block anything observed so far.)

### Why this isn't a Terraform resource

Tried wiring `set-boot-order.sh` in as a `null_resource` with
`depends_on` on the VM, and hit a real, reproducible `taliesins/hyperv`
provider crash every time ("Unable to remove resource pool from dvd
drive") whenever anything caused Terraform to reconcile the existing
`hyperv_machine_instance` — including just the VM being stopped/started
externally.

Root-caused precisely: the provider computes
`dvd_drives[].resource_pool_name` (`"Primordial"`, Hyper-V's default)
and a `vm_processor` block that this module's config never declared, so
every `tofu apply` against an existing VM saw permanent drift and tried
(and crashed) reconciling it. Fixed *that* by pinning both explicitly in
`tofu/modules/vm/hyperv/main.tf` — `tofu plan` now reports zero drift —
but the boot-order fix itself still can't safely run as a resource (it
would hit the same crash on a *fresh* VM's very first apply, before any
drift exists to have pinned yet). Run `scripts/set-boot-order.sh` by
hand once after `tofu apply` until this is better understood.

### The debug-disk detour

One casualty of this investigation: a debug-disk diagnostic aid
(`var.debug_disk`, off by default) was built — a second small
FAT-formatted VHD the guest's `%post` mounts and writes a diagnostic
dump to, readable from the Windows side via `Mount-VHD` without needing
network or serial console cooperation. Attaching it exposed a *second*,
independent bug: without an explicit `ignoredisk`, Anaconda considers
every attached disk during partitioning, and Hyper-V's guest
disk-enumeration order for `sda`/`sdb` was confirmed unstable across
boots (one run saw the 64MB debug disk come up as `sda`, the 40GB OS
disk as `sdb` — the opposite of the naive assumption), so a hardcoded
`ignoredisk --only-use=sda` silently pinned the wrong disk.

A kickstart `%pre`-generates-then-`%include`s-a-file approach to pick
the disk dynamically by size was tried and found to not work reliably
in this Anaconda version (`%include` appears to resolve before `%pre`
has actually run). Given the console access this whole investigation
produced made the debug-disk's original motivation moot, it was
reverted to its simple, twice-proven-working single-disk form rather
than chasing a third fix; `var.debug_disk` still exists and its `%post`
dump script is written defensively (finds the debug disk by exact size,
never by assumed device name — see the script for why), but needs the
attach-after-install redesign noted in code comments before it's
something to actually rely on.

### The three capture channels (kept for the record)

Built while the real cause was still unknown — all legitimate
infrastructure, none of them wrong to have tried; the premise they were
testing just turned out to be false:

1. **Install-time syslog** — kickstart's native
   `logging --host=... --port=1514` command
   (`scripts/test/syslog-capture.py`, a small UDP listener). Confirmed
   the network path itself works (a test packet sent directly from the
   Hyper-V host arrived fine) — but zero bytes ever arrived from the
   guest across two full install cycles.
2. **Post-install rsyslog forwarding** — `%post` installs and enables
   `rsyslog` with `*.* @<host>:1514`, gated the same way, meant to keep
   logs flowing *after* reboot too. Also zero bytes.
3. **Serial console** — added `console=ttyS0,115200n8 console=tty0` to
   the shared kickstart's `bootloader --append` and a Hyper-V
   COM1-to-named-pipe redirect, read via a small PowerShell client.
   Zero bytes even with the reader connected before `Start-VM`.

In hindsight, all three were plausibly reading a guest stuck back at
Anaconda's boot menu / early installer environment rather than a booted
final system — which the install-time `logging` command and serial
console *should* have covered but didn't; that gap (why install-time
capture also came up empty) wasn't separately root-caused and is worth
another look if these channels matter again.

## M4 (domain services): AD DS promotion and domain join bugs

M4 (`domain_controller` role, Windows/Linux domain join) is now
confirmed working end-to-end against real infrastructure (libvirt
backend: a `dc` + `linsrv` + `winsrv` small profile) — a single,
unmodified `ansible-playbook site.yml` run takes bare VMs all the way
through AD DS promotion, both platforms' domain join, and software
install with zero failures. Three real, non-obvious bugs had to be
found first, none of them reachable by static review or the
implementation plan alone.

### `become: true` on Windows WinRM plays

`site.yml`'s `domain_controller` and `domain_directory` plays both had
`become: true` left over from scaffolding, never exercised while those
roles were still `debug`-only placeholders. The moment real tasks ran
against them: `[ERROR]: Task failed: Become plugin sudo is not
supported by the Windows exec wrapper. Make sure to set the become
method to runas.` WinRM connections already run as the local
Administrator (Claude_Docs/Reference_Credentials.md §4) — fully privileged, no
escalation needed — and nothing in this design sets
`ansible_become_user`/`ansible_become_pass` for the `runas` method
Windows actually requires. Fix: drop `become: true` from both plays,
matching `windows_common`'s (which never had it). Found the first
occurrence (`domain_controller`) by reasoning about it up front; found
the second (`domain_directory`) only by hitting it live — it directly
blocked every subsequent play from running during that test, since a
play failure removes the failed host from the rest of that
`ansible-playbook` invocation.

### Username format is platform-specific, and in *opposite* directions

Both new join roles (`windows_domain_join`, `linux_domain_join`)
originally defaulted `domain_admin_user` to a UPN,
`"Administrator@{{ domain_name }}"`. Both failed — with completely
different, both misleading, error messages — and each platform turned
out to need a *different* format, discovered only by reproducing each
failure outside Ansible entirely to rule out a module-layer bug:

- **Linux (`realm join`/`adcli`) needs a bare username** (`Administrator`,
  no realm suffix). The UPN form failed with `realm: Couldn't
  authenticate as Administrator@labape.test: KDC reply did not match
  expectations` — which reads like a Kerberos/auth problem but isn't:
  `kinit administrator@LABAPE.TEST` with the identical password against
  the identical realm succeeded cleanly (valid TGT issued), and DNS/SRV
  discovery (`realm discover`) and clock sync (sub-2-second skew between
  client and KDC) were both independently confirmed fine. What actually
  fixed it: a direct `adcli join -U Administrator --stdin-password`
  (bare username) succeeded completely — full computer-account creation
  and keytab population — while `-U 'Administrator@labape.test'` did
  not. `dns_domain_name` is already passed as its own parameter, so `-U`
  apparently doesn't need (and actively mishandles) the realm appended.
- **Windows (`Add-Computer`/`microsoft.ad.membership`) needs the
  opposite: a NetBIOS-qualified credential** (`LABAPE\Administrator`).
  Both bare `Administrator` and the UPN form failed identically:
  `Unable to update the password. The value provided as the current
  password is incorrect.` — despite the same password authenticating
  fine over WinRM Basic auth to the DC moments earlier, which proved the
  *password* itself was never the problem. Reproduced with raw
  `Add-Computer -Credential` outside Ansible/`microsoft.ad.membership`
  entirely, isolating it to Windows' own join API rejecting the
  credential's *form* — a workgroup computer has no domain context yet
  to resolve a bare username against, so (unlike `adcli`) it needs
  explicit domain qualification. Switching to
  `"{{ netbios_name }}\\Administrator"` fixed it immediately (confirmed
  first via a manual `Add-Computer` test, then via the real Ansible
  role).

Net: two structurally similar-looking roles, doing conceptually the
same job (join a domain), needed genuinely opposite credential formats
for their respective platforms' join mechanisms — not a case where one
was "right" and the other just hadn't caught up.

### A smaller, related lesson: `for_each` map ordering, not list position

Unrelated to the Ansible roles above, but hit while setting up the real
test environment: inserting a new `dc` host group into `host_groups`
(wherever in the list) unexpectedly renumbered `linsrv1`/`winsrv1`'s
IPs and collided with the still-running VMs from a prior test, which
`scripts/deploy.sh`'s pre-flight network check (Claude_Docs/Reference_Networking.md §3)
correctly refused to proceed past. Reordering `host_groups` (`dc` first
vs. last) made no difference — because OpenTofu's `for_each` over the
flattened host map iterates in **sorted key order**, not list-insertion
order, so `"dc1"` always sorts before `"linsrv1"`/`"winsrv1"`
regardless of where `dc` appears in the `.tfvars` list. Not a bug, just
a non-obvious mechanic worth knowing before assuming list order
controls IP assignment. Worked around by fully destroying the old
environment before applying the new topology, since two of the three
target hosts needed replacing either way once `dc` was added.

## M5 (directory objects): three bugs found getting OUs/groups/users/membership working

M5's directory-objects piece (`domain_directory` role, local-account
tasks folded into `windows_common`/`linux_common`,
`directory-manifest.yml`) is now confirmed working end-to-end against
real infrastructure: OUs, domain groups, domain users with inline group
membership, explicit domain-group nesting, local groups, local users,
and a domain principal added to a local group all created successfully
in one test pass, zero failures. Three real bugs along the way, none
guessable without running it:

### Custom filter plugin not discovered

`ansible/filter_plugins/directory_objects.py` (two small helpers —
`ou_dns` for OU ancestor-DN computation, `for_host_group` for matching
manifest entries against a host's role — both awkward to express in
pure Jinja2, hence a filter plugin) went completely undiscovered:
`Syntax error in template: No filter named 'ou_dns'`. Ansible's default
filter-plugin search path is relative to the *playbook file's own
directory* (`ansible/playbooks/filter_plugins/`), not the `ansible/`
directory these actually live in — a mismatch with how this project
already places things (`ansible/roles/`, configured explicitly via
`roles_path = ./roles` in `ansible.cfg`, rather than relying on that
same "relative to playbook" default). Fixed by adding an explicit
`filter_plugins = ./filter_plugins` line to `ansible.cfg`, matching the
existing `roles_path` convention instead of fighting Ansible's default
discovery rules.

### `microsoft.ad`'s list-valued parameters actually want `{add:/remove:/set:}`

Both `microsoft.ad.user`'s `groups` parameter and `microsoft.ad.group`'s
`members` parameter look like they take a plain list (that's what a
flat YAML list under either key visually suggests, and it's what
`ansible-doc`'s one-line description implies: "the members of the group
to set"). They don't — passing a plain list to `groups` failed with
`argument for groups is of type System.Object[] and we were unable to
convert to dict: System.Object[] cannot be converted to a dict`. Both
parameters actually want a dict with `add`/`remove`/`set` sub-keys.
Fixed by wrapping the list: `{{ {'add': item.groups} }}` — `add` chosen
deliberately over `set` since the manifest's inline `groups:`/explicit
`members:` are meant as *additive* membership, not the account's sole
authoritative group list.

### `directory-manifest.yml`'s `hosts:` field: the documented convention was wrong

The original `Claude_Docs/Design_Directory-Objects.md` (and the example manifest that
came with it) said a local object's `hosts:` field is "a role name
(`winws`, `linsrv`, …) — the same names Claude_Docs/Design_System-Overview.md §9's `host_groups`
... already use." That's not what the inventory actually contains:
`scripts/generate-inventory.py` builds Ansible inventory groups from
each host's `roles` list (`domain_controller`, `windows_server`,
`windows_workstation`, `linux_server`, `linux_workstation` —
`ALL_ROLE_GROUPS`), never from a `host_groups` block's own `name:`
label (`winws`, `linsrv`, `dc`, …, which is only a hostname-generation
convenience — Claude_Docs/Design_System-Overview.md §9 itself says "each *role* a host carries
becomes an Ansible inventory group membership," not each host-group
name). Every local-object task silently no-op'd — no error, just an
empty `for_host_group` match on every host — until the manifest and doc
were both corrected to use real role names (`windows_server` instead of
`winsrv`, etc.), which immediately matches
`software-manifest.yml`'s own `roles:` key convention. A quiet
"0 items matched" is a much easier bug to miss than a hard failure —
worth specifically checking task recap output for unexpected
all-`skipping` local-object tasks rather than just "no errors, must be
fine."

### Known minor gap, not chased down

`microsoft.ad.user`'s `{add: [...]}` form of `groups` reports `changed`
on a second run even when the user's group membership hasn't actually
changed — an idempotency wrinkle in the module (or in how this role
calls it) worth revisiting, but not blocking: it's cosmetic (a spurious
`changed` in the run summary), not a correctness problem.

## M5 (workstation support, Ubuntu LTS half): five real bugs, all resolved

First-ever Debian-family (subiquity/cloud-init) install attempt, tested
in isolation per the M5 workstation-support plan. Real infrastructure
testing surfaced five distinct bugs — four fixed and confirmed, one
(disk/storage configuration) still open at the end of this round. This
took roughly the same order of iteration the original Windows
answer-file saga did (troubleshooting-log's own M1 Windows section,
round 10) — expected, not a surprise, per that plan's own stated
expectations.

### `virt-install` couldn't find the kernel on Ubuntu's live-server ISO

`virt-install --location <iso>` failed outright: `ERROR Couldn't find
kernel for install tree.` — `--debug` showed it probing a hardcoded
legacy path (`hasFile(/install/vmlinuz) returning False`) rather than
consulting osinfo-db's own declared kernel path for this OS. Root
cause: osinfo-db's `ubuntu24.04` entry only declares `<media>` entries
(`installer-script="false"`, with the entry's own comment noting
subiquity's autoinstall style "isn't supported yet in libosinfo and
associated tools") — no `<tree>` entry at all, so virt-install's
install-tree kernel/initrd auto-detection has nothing to consult and
falls back to a short list of legacy paths, none of which exist on a
casper-based live ISO (real path: `casper/vmlinuz` /
`casper/initrd`, confirmed via `xorriso -indev <iso> -find /`). Fixed
by passing the paths explicitly on `--location`, bypassing detection
entirely: `--location "<iso>,kernel=casper/vmlinuz,initrd=casper/initrd"`
(`tofu/modules/vm/libvirt/scripts/create-iso-direct.sh`'s `debian)`
case).

### Several one-time subiquity screens block forever over a serial console, even with `interactive-sections: []`

Despite `interactive-sections: []`, subiquity still requires one Enter
keypress each on a chain of screens before an install actually becomes
hands-off: a "Serial console started in basic mode" splash, a
welcome/language picker, an installer-self-update check, a
network-configuration review, and a proxy-configuration screen were all
hit in testing — none suppressed by the autoinstall config, all
requiring real input, none documented anywhere as unavoidable. Fixed by
adding `dismiss_subiquity_prompts()` to `create-iso-direct.sh`'s
`debian)` case: it runs `virt-install ... --wait -1` backgrounded (not
foreground-blocking) so this function can poll concurrently, detects
"idle" generically (the console log only grows while the TUI is
actively redrawing — two consecutive polls with no size change means
something is very likely waiting on input) and injects a carriage
return into the domain's console pty via `sudo -n tee` when idle,
rather than hardcoding a marker string per screen (fragile — the exact
set of unavoidable pre-autoinstall screens isn't documented and a
naive marker-based first attempt was overtaken by a screen it hadn't
accounted for). Two sharp edges hit building this:
- Reading/writing the console log and pty requires root (both are
  0600, a libvirt/QEMU default unrelated to this repo) — this repo's
  test account only has narrowly-scoped passwordless sudo for specific
  binaries (`cp`, `tee`, …), not a blanket allowance, so the function
  is built entirely out of `sudo -n /usr/bin/cp <path> /dev/stdout`
  (stream a root-owned file to an unprivileged reader) and
  `printf '\r' | sudo -n tee <pty>` (write to a root-owned device) —
  worth confirming the real deploy account has the same two commands
  allowed, wherever this ends up actually running.
- `set -euo pipefail` (this script's own top line) plus a `grep -oP`
  with **no match yet** (routine — the console element doesn't exist
  in `virsh dumpxml` until the domain actually starts) makes the whole
  pipeline "fail," which `set -e` then treats as the *entire script*
  failing — silently orphaning the backgrounded `virt-install` (visibly
  reparented to PID 1) and leaving OpenTofu's `local-exec` hung
  reading its now-parentless-but-still-open output pipe (`tofu apply`
  sat on "Still creating..." indefinitely, well past when the actual
  work had already died). Needed an explicit `|| true` on that specific
  pipeline — not defensive boilerplate, load-bearing.
- The idle-detection cap (`max_sends`) was first set to a cautious 10
  and immediately exhausted by ordinary boot noise (disk-allocation
  progress ticks, kernel/udev messages each produce an idle gap of a
  few seconds) well before subiquity's TUI ever appeared, silently
  disabling the function for the rest of the install. Raised to 50 — a
  stray Enter during boot has nothing focused/actionable to
  accidentally trigger, so a generous cap is safe; `install_timeout_seconds`
  is still the real ceiling.

### apt mirror-testing hangs forever on "The mirror location is being tested"

With no `apt:` section at all, subiquity defaults to a GeoIP lookup to
pick a country mirror before testing it — and hung indefinitely on that
step even though `archive.ubuntu.com` itself was directly reachable
(confirmed via `curl` from an already-deployed guest VM on the same
network, `HTTP:200`). The GeoIP lookup service itself, not the mirror,
is the part this network can't reach. Fixed by pinning a known mirror
and skipping GeoIP entirely: `apt: {geoip: false, primary: [{arches:
[amd64], uri: "http://archive.ubuntu.com/ubuntu/"}]}`.

### RESOLVED: the autoinstall seed was never found (misdiagnosed as a storage bug)

**Root cause, found in the follow-on pass below:** the boot line was
`autoinstall ds=nocloud;s=file:///cdrom/`. Booting via `--location`,
`/cdrom` in the live environment is the Ubuntu ISO itself, not the
attached `CIDATA` seed ISO, so cloud-init never found `user-data` and
subiquity ran **fully interactive**. That one mistake explains every
symptom in this section: the welcome/network/proxy screens the dismiss
loop was pressing Enter through, the ignored `storage:` directives, and
the guided-storage/LUKS screens (the interactive defaults, not our
config). Subiquity's docs say an autoinstall shows no screens at all;
that was the tell. Fixed by dropping the `s=` path (`autoinstall
ds=nocloud`), which lets cloud-init find the seed by its `CIDATA`
label. Once the config was really applied, the hand-written `storage:
config:` list failed properly ("autoinstall config did not create needed
bootloader partition"), so the template went back to the documented
`storage: {layout: {name: direct}}`. Ubuntu 24.04.3 then installed
end-to-end unattended (SSH, sudo, `vda1` bios_grub + `vda2` root) and
joined the domain. The original investigation notes follow, kept for
the record.

#### Original notes (superseded)

Neither of subiquity's two documented storage directives produced a
non-interactive result on this Ubuntu 24.04.3 build:
- `storage: {layout: {name: direct}}` (the documented "whole disk, no
  LVM, no encryption" shorthand) was still followed by an unskippable
  **"Passphrase must be set"** LUKS screen with no way to leave it
  blank — contradicting the documented behavior that `direct` doesn't
  support encryption at all.
- Replacing it with an explicit action list (`storage: {config: [{type:
  disk, ...}, {type: partition, ...}, {type: format, ...}, {type:
  mount, ...}]}`, confirmed correctly rendered into the final
  `user-data` file actually used) got further — subiquity's guided
  screen did show "**(X) Custom storage layout**" pre-selected, meaning
  the directive was at least partially recognized — but the actual
  device editor underneath showed **"No used devices"**: none of the
  declared disk/partition/format/mount actions were actually applied,
  leaving an empty manual editor. A subsequent guided pass (same
  install, different attempt) showed a *different* symptom again: an
  LVM-guided screen with an unchecked-by-default "Encrypt the LVM group
  with LUKS" checkbox — inconsistent behavior across otherwise-identical
  attempts.

Manually completing the interactive storage editor via injected
keystrokes (the same pty-injection mechanism `dismiss_subiquity_prompts`
uses) proved too fragile to drive blind — urwid's on-screen state
doesn't map predictably enough to a fixed keystroke sequence
(dropdowns not opening on the expected key, focus landing on an
unrelated top-right help menu, near-identical screen redraws that
looked unchanged across genuinely different underlying state) to be
worth continuing without live visual feedback. **Not yet resolved** —
next steps, in rough order of promise: (a) check whether an `apt`-side
or `storage`-side `version:` key is required specifically for the
`config:` form on this subiquity release (both attempts used the same
top-level `version: 1`); (b) check subiquity's own logs from inside
the live installer (it advertises SSH access into the running install
environment — `/var/log/installer/subiquity-*-debug.log` — for the
actual reason the declared actions weren't applied, rather than
inferring from the TUI alone); (c) as a fallback, a real (test-only,
clearly-labeled-insecure) LUKS passphrase plus a `late-commands` step
that stores it somewhere Ansible's first connection can retrieve and
unlock with — undesirable (breaks unattended reboot without extra
plumbing) but would at least unblock forward progress on Windows 11
testing (Stage 4) while this is revisited.

(Superseded by the resolution above.) Stage 3 (Ubuntu LTS in isolation) was at this point **partially confirmed**:
templating, kernel/initrd boot, the serial-console prompt chain, and
apt/mirror configuration are all confirmed working unattended; disk
provisioning is not, so no Ubuntu LTS host has yet completed a full
unattended install through to a bootable, SSH-reachable final system.

## M5 (workstation support, Ubuntu 26 / Debian / Windows client): OS-matrix pass

Follow-on to the section above: after Ubuntu 24.04.3 blocked on storage,
the remaining workstation candidates were each tried in isolation
(single-host throwaway workspace, `test-<name>.tfvars`), then the two
Windows clients were domain-joined against the existing `lab1` DC.

| OS (`os` key) | Result |
|---|---|
| Ubuntu 26.04.1 (`ubuntu_26`) | Same storage/LUKS passphrase screen as 24.04.3, at the same point. Later traced to the seed-discovery bug (resolved above), not a subiquity bug. |
| Debian 13 (`debian_latest`, new `debian_preseed` os_family) | Fully unattended install; SSH as `labape` and passwordless sudo confirmed. |
| Windows 11 24H2 (`windows_11`) | Fully unattended install (~9 min), Windows 11 Pro, joined `labape.test`, `site.yml` clean. |
| Windows 10 22H2 (`windows_10`) | Fully unattended install (~8 min), Windows 10 Pro, joined `labape.test`, `site.yml` clean. |

This also closes M4's "not yet separately exercised" note: the
`windows_workstation` join path works with the unchanged
`windows_domain_join` role.

### `--os-variant win11` silently switches the VM to UEFI + TPM

libosinfo's `win11` profile makes `virt-install` define the domain with
`<os firmware="efi">` and a `tpm-crb` device (Server's `win2k22` gets
plain SeaBIOS). Under OVMF, the Windows ISO's "Press any key to boot
from CD or DVD..." prompt times out unattended and drops into the OVMF
Boot Manager, so the install never starts without a human. Two follow-on
effects: `virsh undefine` refuses a UEFI domain without `--nvram`
("cannot undefine domain with nvram"), and the manual workaround used
during testing, `--remove-all-storage`, deleted the shared Windows 11
ISO (see the next entry but one).

The explicit fix, `--boot firmware=bios`, passes `--print-xml` but fails
at define time: "Unable to find 'bios' firmware that is compatible with
the current configuration". libvirt then wants a SeaBIOS firmware
descriptor, and this Debian host's qemu packages only ship edk2 ones in
`/usr/share/qemu/firmware/`. Fixed instead by cataloging `windows_11`
with `os_variant = "win10"` (same device model, no firmware attribute,
so plain SeaBIOS; `tofu/backends/libvirt/main.tf`). Windows 11 Setup's
own TPM/Secure Boot/CPU/RAM checks are skipped by the existing
`HKLM\SYSTEM\Setup\LabConfig` `Bypass*Check` keys in the client
template's windowsPE `RunSynchronous`, the documented mechanism tools
like Rufus automate. `safe_undefine` now passes `--nvram` anyway
(harmless on BIOS domains, verified) so a UEFI leftover can't wedge a
destroy.

### Client media prompts for a product key despite `AcceptEula`

Retail/multi-edition client ISOs stop on "Activate Windows" even with
`<UserData><AcceptEula>true</AcceptEula></UserData>`; Server eval media
doesn't. Fixed with an empty key under `UserData` in both client
templates: `<ProductKey><Key></Key><WillShowUI>Never</WillShowUI></ProductKey>`.
Edition selection still comes from `/IMAGE/NAME` (`Windows 11 Pro` in
`install.wim`, `Windows 10 Pro` in `install.esd`, both confirmed with
`wiminfo`; extract without mounting via `7z e -o<dir> <iso> sources/install.wim`).

### Windows 11 needs a bigger disk than the repo default

24H2 Setup stops with "The system drive needs to be at least 52 GB or
larger" on the 40 GB `disk_gb` default. The catalog now carries an
optional `min_disk_gb` (64 for `windows_11`, Microsoft's published
minimum), and `validate_os` fails the plan with a clear message instead
of letting Setup stall mid-install. Set `disk_gb` (80 used in testing)
on Windows 11 host groups; `medium.tfvars.example` does.

### Operational: `virsh undefine --remove-all-storage` deleted a shared ISO

During manual cleanup of the first (UEFI) Windows 11 attempt,
`virsh undefine winws1 --nvram --remove-all-storage` deleted
`/data/OS_Images/Windows_11_24H2_2025_03.iso`: that flag removes every
attached volume, including the read-only `--cdrom` install media. The
ISO was restored by hand. The repo's own teardown path (`safe_undefine`
in `tofu/modules/vm/libvirt/scripts/lib/safe-undefine.sh`) already avoids the flag and deletes
only the VM's disk and generated answer ISO by path; confirmed with a
real `tofu destroy` of both Windows client VMs, which left the shared
ISOs intact. Never use `--remove-all-storage` on LABaPe domains.

### CRLF shell scripts from a Windows checkout

Copying `create-iso-direct.sh` from a Windows working copy
(`core.autocrlf=true`) to the libvirt host failed on line 5:
`set: pipefail: invalid option name`. `.gitattributes` now pins `*.sh`
and `*.py` to LF in every working copy.

## M5 (workstation support, completion pass): Ubuntu fixed, Linux workstation join, medium profile

### Ubuntu's autoinstall seed was never found

See the RESOLVED entry in the Ubuntu LTS section above: `ds=nocloud;s=file:///cdrom/`
pointed at the install ISO, not the seed, so every Ubuntu attempt so far
had been an interactive install. Fixed with `autoinstall ds=nocloud`
(find the seed by its `CIDATA` label) plus `storage: layout: direct`.

### `safe_undefine` left the Ubuntu seed ISO behind

Cleaning up an interrupted Ubuntu install removed the disk but not
`<vm>-seed.iso` (the path match only knew Windows' `-autounattend.iso`).
The libvirt-owned leftover then made the rebuild's `xorrisofs` fail:
"libburn: Failed to open device (a pseudo-drive): Permission denied".
The match now covers `-(autounattend|seed)\.iso`; confirmed by a real
destroy that removed both files.

### Two environments with the same host-group names shared VMs

libvirt domain names are host-global, but tofu workspaces aren't. A
medium-profile workspace whose groups were named `dc`/`winsrv`/`linsrv`
(like `lab1`'s) got `dc1`, `winsrv1`, `linsrv1`: `create-iso-direct.sh`
saw them already running and "skipped (idempotent no-op)", so the new
workspace's state adopted `lab1`'s VMs, and destroying it would have
deleted them. Caught before any destroy; the adopted entries were
removed with `tofu state rm`. Every VM is now created with
`--metadata description=labape-workspace=<workspace>`, and an existing
VM without this workspace's tag makes the create fail loudly instead of
being adopted. VMs built before the tag existed need a one-time
`virsh desc <vm> 'labape-workspace=<workspace>'`. Rendered answer files
now live in `modules/vm/*/.rendered/<workspace>/` (they used to share one
directory keyed by VM name, so environments overwrote each other's);
unique host-group names are still required because libvirt VM names are
host-global.

### Linux domain join assumed NetworkManager

`linux_domain_join` set the DC as resolver with `nmcli`, which only
exists on the Rocky hosts. Ubuntu server (netplan + systemd-resolved) and
Debian preseed installs (ifupdown, plain `/etc/resolv.conf`) have no
NetworkManager. The role now picks by what's running: nmcli
(NetworkManager), a `resolved.conf.d` drop-in with `Domains=~<domain>`
(systemd-resolved), or a written `/etc/resolv.conf`. It also runs
`pam-auth-update --enable mkhomedir` on Debian-family hosts, the
counterpart of RHEL's oddjobd. Passing the task file to `include_tasks`
as a folded multi-line expression in free form failed ("Invalid options
for ansible.builtin.include_tasks"); it has to go under `file:`.

### Rotating the Windows bootstrap password would have reinstalled every Windows VM

The VM reinstall trigger is an md5 of the rendered answer file, and the
Windows answer file embeds `windows_bootstrap_admin_password`, so a
vault rotation changed the trigger for every Windows VM. The trigger now
hashes the answer file rendered with the password masked; template or
addressing changes still force a reinstall. Changing the formula changes
existing VMs' trigger values once: a `lab1` plan after this change wants
to replace `dc1` and `winsrv1`. To keep an existing environment, write
the planned `answer_file_md5` values into those `null_resource` entries'
`triggers` in state (`tofu state pull`, edit, bump `serial`, `tofu state
push`) before its next deploy. `lab1` also shows older drift: commit
`f94bb8d` renamed the rendered kickstart (`<vm>-ks.cfg` to
`<vm>-answer.cfg`) and changed its content, so `linsrv1` would be rebuilt
on its next deploy too, independent of this change. Done for `lab1` (2026-10-09): after a targeted apply of
the three answer-file resources, each to-be-replaced `null_resource`'s
planned `triggers` map was copied into state; `tofu plan` then reported
no changes.

### ISO renames make existing VMs look changed

`/data/OS_Images/` was renamed (`Linux_`/`Windows_` prefixes) after
`lab1` was built, but `tofu/environment.yml` still has the old names. It
keeps working only because `lab1`'s VMs already exist; `iso_host_path`
is also a reinstall trigger, so correcting the paths makes `lab1`'s next
deploy rebuild `dc1`/`winsrv1`/`linsrv1`, and a fresh environment can't
use those entries at all. The medium-profile run used a copy of
`environment.yml` with corrected paths. Fixed on 2026-10-09: the real
`environment.yml` now has the current names, and `lab1`'s state
`iso_host_path` triggers were aligned in the same pass as the answer-file
triggers above, so the fix didn't rebuild anything.

### Dual-role DC failed on local accounts

The medium profile's `dc` group is `["domain_controller", "windows_server"]`,
so `windows_common` applied `local_users`/`local_groups` entries aimed at
`windows_server` to the DC. A DC has no local account database:
`win_group` there creates a domain group, and `win_user` failed. The
three local-scope tasks now skip hosts in `domain_controller` and print
a note instead. First run of the dual-role profile, so this path had
never executed before.

### Medium profile, end to end

Ten VMs (dual-role DC, 2 Windows Server 2022, 3 Rocky 9, 2 Windows 11,
Ubuntu 24.04 and Debian 13 workstations) in their own `medium.test`
domain: all installed unattended, all joined (10 computer objects in AD),
directory objects created, `site.yml` clean on all ten after the fixes
above. Two test-procedure problems, not repo bugs: the run used a
scratch helper that skipped `deploy.sh`'s pre-flight IP check, and one
planned address (`.117`) belonged to another device on the LAN (the VM
came up behind it; moved by hand). `lab1`'s `directory-manifest.yml`
hard-codes `DC=labape,DC=test`, so a second domain needs its own manifest
(passed here as an extra-vars `directory_manifest` override).

### Changing the connected account's password reports failure

Rotating the Windows bootstrap password (Claude_Docs/Reference_Credentials.md §9):
`microsoft.ad.user` on the DC and `ansible.windows.win_user` on the
member server and Hyper-V host all came back FAILED, yet each had
changed the password. The task changes the password of the very account
the WinRM session authenticates as, so the module's result can't be
returned over that session. The run looked like a failure with nothing
changed; the next attempt with the old password got "credentials
rejected". Afterwards, test the new password before retrying anything,
and only then update the vault.

### Follow-ups after M5

- The Ubuntu `dismiss_subiquity_prompts` loop (Enter into the serial
  console whenever it went idle) is removed: it only existed because the
  autoinstall seed was never found. It also kept running after
  virt-install had finished, until it had sent 50 Enters or hit the
  install timeout, and it needed passwordless sudo for `cp`/`tee` on the
  console log and pty. The `debian)` branch now runs virt-install in the
  foreground like every other branch.
- `scripts/deploy.sh --test` / `destroy.sh --test` replace the scratch
  helper used during M5 testing: test inventory goes to
  `ansible/inventory/<instance>/`, the pre-flight IP check always runs
  (skipping it is what let the `.117` conflict through), and teardown
  removes the workspace, inventory and rendered files. `--env-file` and
  `--directory-manifest` cover a second domain.

## M6 phase A (templates on libvirt): promote and clone

Rocky 9 and Windows Server 2022 VMs built from ISO were promoted with
`scripts/promote-to-template.sh` and cloned via
`image_source = "packer_template"` (Claude_Docs/Design_Base-Images.md §5).
Cloning a VM takes 2-4 seconds; the clones then boot and personalize
themselves. Bugs found on the way:

### Windows ignored `Autounattend.xml` on the clone's CD

The first Windows clone came up at "Enter new credentials for
Administrator": none of the answer file had been applied. Setup only
reads `Autounattend.xml` from removable media for the windowsPE and
offlineServicing passes; the specialize and oobeSystem passes a
sysprepped image runs look for **`unattend.xml`** (Microsoft, "Windows
Setup Automation Overview"). The file on the clone CD is now
`unattend.xml`. Related: the cached `C:\Windows\Panther\unattend.xml`
must be deleted before sysprep, or the image keeps using the install-time
answer file instead of any supplied later (and it contains the
Administrator password in plain text).

### A Windows template kept its source VM's static IP

The same clone held `172.21.20.106`, the template source VM's address,
which collided with another planned clone. Static IP settings survive
`sysprep /generalize`. Finalize now resets every NIC to DHCP first, so a
clone boots on DHCP until its FirstLogonCommands set its own address.

### cloud-init rejected `to: default`

Rocky's cloud-init 24.4 failed its whole network stage on
`routes: [{to: default, ...}]` ("Address default is not a valid ip
address"); netplan accepts it, cloud-init's own v2 parser doesn't. The
NIC fell back to DHCP. Now `to: 0.0.0.0/0`.

### cloud-init's sysconfig renderer can't match a name glob

With the route fixed, the network config was "applied" but the NIC
still took DHCP: on Rocky, cloud-init picks its `sysconfig` renderer,
which wrote a profile for a device literally named after the config key
(`primary`) instead of honouring `match: {name: "e*"}`. Clones now get a
deterministic MAC (from environment + VM name) passed to virt-install,
and the network config matches `macaddress` and sets the name `eth0`,
which every renderer handles.

### LVM root didn't grow into a bigger clone disk

cloud-init's `growpart`/`resize_rootfs` only handle a filesystem on a
plain partition; Rocky's root is an LVM logical volume, so a 60 GB clone
of a 40 GB template kept a 35 GB root. The clone user-data now runs
growpart on the PV's partition, `pvresize`, and `lvextend -r`. First
attempt failed because `lsblk -no pkname <pv>` also prints the LVs inside
the partition (`vda vda2 vda2`); `-d` limits it to the device.

### Smaller things

- The ISO-path locals (`coalesce()` over the ISO answer-file resources)
  error when every argument is empty, which is the case for a cloned VM;
  wrapped in `try(..., "")`.
- `extract_planned_ips.py` only looked at `vm_iso_direct`, so clones
  skipped the pre-flight IP-conflict check; it now includes
  `vm_from_template`.
- `windows_common` failed on lab1's directory manifest in a test
  environment without a DC (a `LABAPE\Sales-Users` local membership).
  Domain-qualified members are now skipped when the environment has no
  domain controller.
- One run's `dnf` install (firefox, from the software manifest) on a
  fresh Rocky clone sat for 17 minutes with its packages already
  downloaded, the module sleeping while holding dnf's own download lock.
  Killed and re-run, the same playbook finished in 2 minutes; the
  identical task had also passed on the previous clone run. Not
  template-related, recorded in case it recurs.
- After clones of a template have run, libvirt's dynamic ownership leaves
  the template file owned by `libvirt-qemu` (mode stays 0444, so it's
  still read-only). Harmless; noted so it isn't mistaken for tampering.

## M6 phase B (Packer builds)

`scripts/build-template.sh <os-key> <template-name>` builds a template
from ISO with Packer's QEMU builder, rendering the same answer-file
templates as the iso_direct path (Packer's `templatefile()` has the same
syntax) and generalizing with the same `template_finalize` role, then
moves the qcow2 into the template library. Built and clone-tested:
Rocky 9 (~10 min), Windows Server 2022 (~10 min), Ubuntu 24.04 (~17 min,
mostly security updates), Debian 13 (~18 min). Bugs found:

### QEMU's default CPU model can't run EL9

The first Rocky build kernel-panicked ("Attempted to kill init!"); the
serial log had "Fatal glibc error: CPU does not support x86-64-v2".
Packer's QEMU builder uses QEMU's default `qemu64` CPU model, and EL9 is
built for x86-64-v2. libvirt picks a modern model for the iso_direct
path, which is why that never showed up there. All builds pass
`-cpu host`.

### Packer's Ansible inventory broke the WinRM connection

With `use_proxy = false`, the Ansible provisioner's generated inventory
sets a shell type the WinRM connection plugin rejects ("should have the
shell type of cmd"), followed by "-EncodedCommand is not properly
encoded". The Windows build supplies its own `inventory_file_template`
with the same connection settings as `scripts/generate-inventory.py`.
The WinRM password reaches Packer via `PKR_VAR_admin_password` and
Ansible via a 0600 extra-vars file, never on a command line.

### A Packer-built Windows template's clones saw the NIC as "Ethernet 2"

The clone answer file set its static IP with
`netsh ... name="Ethernet"`. In a template built on Packer's QEMU VM, the
NIC sat on a different PCI address than libvirt gives clones, so the
clone's NIC is a new device named "Ethernet 2", and the template's old
NIC survives as a hidden device still holding "Ethernet" (a
`Rename-NetAdapter` to "Ethernet" was tried and silently didn't happen).
The clone answer file now sets IP, gateway and DNS by interface index.
Promoted templates were unaffected (same virtual hardware as their
clones).

That still failed on Server 2019 clones, silently: run by hand the same
command worked. On a fresh clone Windows may still be installing the
"new" NIC when first logon starts, so the command found no adapter. The
network step is now `firstboot.ps1` on the clone's CD
(`iso/answer-files/windows/clone-firstboot.ps1.tpl`, run as the first
FirstLogonCommand): it waits up to 5 minutes for the adapter, sets the
address, verifies it stuck and retries, and logs to
`C:\Windows\Temp\labape-firstboot.log`.

### Debian's default partitioning blocked root growth

Debian clones kept a 38 GB root on a 60 GB disk: the preseed's stock
`atomic` recipe puts swap in an extended partition after root, so
growpart has nowhere to grow it. The preseed now uses an explicit recipe
with one ext4 root partition and no swap partition (iso_direct Debian
installs get the same layout).

### Operational: `pkill -x packer` stops every build

Stopping one broken build with `pkill -x packer` also cancelled a
parallel Ubuntu build, since both are `packer` processes. Stop a single
build by its PID.

### Ubuntu 26.04 clones ignored their static address

cloud-init couldn't rename the NIC to `eth0` ("[busy] Error renaming"):
26.04's dracut-based initramfs brings the NIC up first. Netplan's
generated `.network` file then matched the name `eth0`, which never
existed, and dracut's catch-all `zzzz-dracut-default.network` gave the
NIC DHCP. Netplan matches by MAC natively, so the clone network config
now renders `set-name: eth0` only for the non-netplan families (Rocky's
sysconfig and Debian's eni renderers need a device name, and the rename
works there).

### Windows 11 sysprep refused to generalize

The Windows 11 build sat at its desktop until Packer's shutdown timeout:
`C:\Windows\System32\Sysprep\Panther\setuperr.log` had "SYSPRP
Package Microsoft.Copilot_... was installed for a user, but not
provisioned for all users" (0x80073cf2). With internet access during the
build, the Store installs apps for the logged-on user. The finalize
script now sets the Store `AutoDownload=2` and
`DisableWindowsConsumerFeatures` policies and removes every app package
that isn't provisioned for all users before running sysprep. (Packer VMs
aren't libvirt domains, so `virsh screenshot` can't see them; a
`vncdotool` venv on the host, `vncdo -s 127.0.0.1::<port> capture`,
can, using the VNC port Packer logs.)

### .NET Framework 4.8 missing on Windows Server 2019

`site.yml` on a Server 2019 host failed every Chocolatey package:
"Chocolatey 2.0.0 requires .NET Framework 4.8"; 2019 ships 4.7.2.
`windows_common` now installs .NET Framework 4.8 (Microsoft's offline
installer, then a reboot) on any Windows host whose 4.x release is older
(release key < 528040); 2022/2025/10/11 already have 4.8 or newer and
skip it. Verified on a Server 2019 clone: release 528049 after the
reboot, Chocolatey 2.7.4 bootstrapped itself, packages installed. (A
first attempt pinned Chocolatey 1.4.0 instead; replaced, since 4.8 is the
supported update for any host still on an older 4.x.) First time a
Server 2019 host went through `site.yml`; not a template issue.

### Known gap: Firefox doesn't install on Windows Server 2019 Core

Windows Server used to install Server Core (image index 1) everywhere.
Firefox's installer, from Chocolatey 1.x or 2.x alike, runs indefinitely
on Server 2019 Core (it needs desktop components 2019 Core doesn't have;
it does install on 2022/2025 Core). Server now installs the Desktop
Experience by default (index 2; `windows_core = true` or
`build-template.sh --core` for Core), so this only applies to Server
2019 hosts that ask for Core: leave Firefox out of their software
manifest. Verified: a Server 2019 Desktop Experience clone got .NET 4.8
and Firefox through `site.yml` with no failures.

### Windows Server: Desktop Experience by default

Server installs (ISO and Packer) now use the Desktop Experience unless
Core is requested. Existing Core VMs keep working: lab1's two Windows
host groups set `windows_core = true`, so their answer files render as
before. The VM reinstall trigger now hashes the answer file with XML
comments stripped as well (they're stripped from the copy Setup reads
anyway), so documentation edits in a template never reinstall VMs; that
formula change needed one more `lab1` state-trigger alignment (no VM
rebuilt). `site.yml`'s first play now waits up to 30 minutes for hosts
to answer: a Desktop Experience install was still at "Getting ready"
after 5.

### Operational: a cancelled build's cleanup deleted its successor's disk

A rebuilt Windows 11 template failed at the very end ("Could not open
.../win11-packer-2026.10.qcow2: No such file or directory") after a
successful sysprep. The previous, cancelled build of the same name was
still cleaning up its output directory, which is the same path. Let a
cancelled build exit fully (or use a new template name) before
rebuilding.

## M6 phase C (template refresh)

`scripts/refresh-template.sh` (Claude_Docs/Design_Base-Images.md §6 Option B)
verified on Rocky: `rocky9-packer-2026.10` cloned into a throwaway
`test-refresh-<id>` environment, all packages upgraded (rebooting when
`dnf needs-restarting -r` asked), promoted as `rocky9-packer-2026.10.1`
(a flattened, self-contained copy), environment destroyed; about 18
minutes. A clone of the refreshed template came up normally. Windows:
`win2022-desktop-2026.10` refreshed to `win2022-desktop-2026.10.1`
(Windows security/critical updates and rollups, including the month's
cumulative update, then sysprep) in about 90 minutes, most of it
Windows' own servicing; a clone came up with the new updates installed
and `site.yml` clean. A transient `registry.opentofu.org` timeout during
`tofu init` once failed two runs at the same moment, so `deploy.sh` now
retries `tofu init` three times.

### Scripts committed from Windows lost their executable bit

Build and promote failed on the lab host with "Permission denied"
(`nohup: failed to run command 'scripts/build-template.sh'`) after the
host checkout was reset to `main`. Files created in this Windows checkout
were committed as mode 100644 — Git on Windows doesn't pick up `chmod` —
and earlier runs had only worked because of a manual `chmod` on the
host, which the reset undid. All entry-point scripts are now 100755 in
Git (`git update-index --chmod=+x`); the sourced `lib/*.sh` files stay
100644. When adding a script from Windows, run
`git update-index --chmod=+x <file>` before committing.

## M10 phase 10a (web interface)

The container stack (`container/`) ran on the lab host under Docker with
a throwaway Authentik (`tools/authentik-test/`). Verified there:
- **Sign-in:** OIDC sign-in for the three test users, with roles taken
  from their Authentik groups.
- **Permissions:** a viewer is refused when creating an environment, and
  can't see another user's environment or its credentials.
- **Host registration:** a KVM host registered through the API.
- **Jobs:** a Rocky template clone deployed and destroyed through the
  API, with OpenTofu state in PostgreSQL, the live log streamed over SSE,
  and cancel through the API. The deploy got through plan, the network
  check, apply, inventory generation and Ansible's connection to the
  clone. Ansible then failed at `linux_common`'s package step, because of
  the bridged-traffic problem below.
- **Break-glass, live through Caddy:**
  - A one-time credential signed in once, and its reuse was refused.
  - A `--local-only` credential was refused on the public listener, even
    with a spoofed `X-LABaPe-Local` header, and accepted on
    `https://localhost:8443`.

### Installing Docker cut bridged VMs off from the LAN (2026-10-10)

The UI's test clone could ping the KVM host but not the LAN gateway,
and DNS queries to the gateway timed out, so `dnf` couldn't resolve the
Rocky mirrors. lab1's long-lived `linsrv1` has the same problem. This
was first blamed on the router, wrongly.

The cause is Docker, installed on the host the same day. The Docker
daemon loads `br_netfilter`, which sets
`net.bridge.bridge-nf-call-iptables = 1`, so frames crossing a Linux
bridge go through iptables. Docker also sets the FORWARD policy to DROP.
A VM on `br0` reaching the host is INPUT and passes; a VM reaching the
gateway is bridged FORWARD traffic and is dropped.

Fix on any KVM host that also runs Docker, as root: stop filtering
bridged traffic, and load `br_netfilter` at boot so the setting applies
after it (Claude_Docs/Planning_Web-Interface-Design.md §20.6). The
alternative, `iptables -I DOCKER-USER -i br0 -o br0 -j ACCEPT` per bridge,
doesn't survive a reboot without extra tooling. Applied on the lab host
the same day (by hand: it needs root, beyond the automation user's scoped
sudo). lab1's `linsrv1` reached its gateway and resolved names again right
away. `container/README.md` lists it as a host prerequisite.

Separately, `rocky9-packer-2026.10.1`'s `/etc/resolv.conf` still lists
`nameserver 10.0.2.3`, QEMU's user-mode DNS from the Packer build, ahead
of the real server. NetworkManager runs with `dns = none`, so nothing
rewrites the file. That isn't the cause above, but it adds a timeout to
every lookup. `template_finalize` should clear the file.

### Authentik refused every grant for a blueprint-made provider

Authorize failed with `invalid_request` ("The request is otherwise
malformed"). Authentik's log said "Invalid grant_type for provider:
authorization_code". Authentik 2026.8's OAuth2 provider has a
`grant_types` list, and a provider created by a blueprint gets an empty
one, which allows no grant at all. The test blueprint now sets
`grant_types: [authorization_code, refresh_token]`. A provider made in the
Authentik UI doesn't hit this, because the form fills the list in.

### Caddy failed the TLS handshake when reached by IP address

With `LABAPE_HOSTNAME` set to an IP address, `curl https://<ip>/` got
"tlsv1 alert internal error". Clients send no SNI for IP addresses, so
Caddy had no name to pick a certificate by. The fix is the
`default_sni {$LABAPE_HOSTNAME}` global option in `container/caddy/Caddyfile`.

### virt-install couldn't import `gi` inside the image

`virt-install` starts with `#!/usr/bin/env python3`. In the image, the
app's venv comes first on `PATH`, so virt-install ran under the venv's
Python and failed with "No module named 'gi'". The venv is now created
with `--system-site-packages`, so it sees Debian's `python3-gi` and
`python3-libvirt`.

### The vault's SSH key path belongs to the CLI host

`deploy.sh` reads `ansible_ssh_private_key_path` from the vault, and that
names a path under the CLI user's home, which doesn't exist in the
container. `deploy.sh` and `destroy.sh` now honor
`LABAPE_SSH_PRIVATE_KEY_PATH` ahead of the vault. The job runner sets it
to its copy of the key pair from `container/engine-secrets/ssh/`.

### `safe-undefine.sh` needed sudo, which the container doesn't have

The destroy job undefined the VM, then failed with "sudo: command not
found" while removing its disk and seed ISO. On the CLI host,
`sudo -n rm` is needed because the files sit in a root-owned directory.
The job runner is root in its container, so the script now runs plain
`rm` when it's already root.

### A disk smaller than the template broke the clone

A host group asked for `disk_gb = 20` from a template with a 40 GB
virtual size. The clone dropped to a dracut emergency shell ("/dev/mapper/
rlm_template-root does not exist"): the overlay truncated the disk, so the
root LV's tail was missing. `create-from-template.sh` now raises a smaller
request to the template's size and prints a warning.

### Operational: cancelled jobs left zombie processes

Cancelling a job (SIGTERM to its process group) worked, but the killed
tools' children became zombies under the worker. As PID 1, the worker
doesn't reap orphans. The app containers now run an init process:
`init: true` in compose, `RunInit=true` in the Quadlets.

### Operational: syncing a Windows checkout gave CRLF line endings

Copying this Windows working tree to the host with `tar` shipped CRLF
endings from `core.autocrlf`: "env: 'bash\r': No such file". Sync from a
git tree instead. Build a temporary index with `git add -A`, write it
with `git write-tree`, and pipe
`git -c core.autocrlf=false archive <tree>` to the host. This needs no
commit.

The `-c core.autocrlf=false` matters. `git archive` applies
`core.autocrlf` to its output, so without it every text file lacking an
explicit `eol=lf` attribute (`.tf`, `.tpl`, `.yml`) arrived with CRLF
endings. That changed the rendered answer files, and a lab1 plan from
that copy wanted to replace all three VMs. With the flag, the plan
matched the host's own checkout (no infrastructure changes). The phase
10a image built earlier that day came from such a copy; images built
from a Linux checkout aren't affected.

### Other setup notes

- **PostgreSQL password file:** PostgreSQL reads its password file as
  uid 70, so `container/setup.sh` makes the files in `container/secrets/`
  0644 inside a 0700 directory.
- **pydantic patterns:** pydantic's Rust regex engine has no look-ahead,
  so the environment name's `test-` exclusion is a validator, not part of
  the pattern.
- **Host-only break-glass:** this can't rely on a loopback client IP,
  because behind the runtime's port publishing a host-local browser
  arrives from the bridge gateway. Caddy's loopback-only listener
  (`127.0.0.1:8443`) adds an `X-LABaPe-Local` header instead, and the
  public listener strips it.

## Networks and IP address management (2026-10-10)

Built per Claude_Docs/Planning_Web-Interface-Design.md §20, and tested on
the new lab VLAN (`br1`, DHCP scope and static pool as in §20.6):

- **lab1 unchanged:** lab1 planned no infrastructure changes under the
  new engine. Its only diff was the same sensitivity marking a plan from
  the host's own `main` shows. It also passed the policy check as the
  legacy one-network catalog.
- **Policy refusal:** a profile whose offset landed inside the DHCP
  scope was refused by `check_ip_policy.py` before anything was created
  ("172.21.48.100 is inside lab-vlan48's DHCP scope").
- **CLI deploy** (`deploy.sh --test`, two Rocky clones):
  - the static one took 172.21.50.8 from the pool (per-network offset 520);
  - the DHCP one's lease (172.21.48.58) was found through the host's ARP
    table;
  - `site.yml` ran clean on both, including EPEL and package installs;
  - destroy left nothing behind.
- **Web UI deploy:**
  - the `lab-vlan48` catalog entry was limited to `labape-deployers` and
    attached to `kvm1` on `br1` as the default;
  - a domain controller on DHCP was refused;
  - the static VM was allocated 172.21.50.1 and the DHCP VM was
    discovered at 172.21.48.120;
  - Ansible was clean, and destroy released the allocation (0 left on
    the network).
