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
ahead of DESIGN.md §18's original M3 schedule — and the long-standing
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
   — reverted to it after this round (docs/base-images.md §4,
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
Administrator (docs/credentials.md §4) — fully privileged, no
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
`scripts/deploy.sh`'s pre-flight network check (docs/networking.md §3)
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

The original `docs/directory-objects.md` (and the example manifest that
came with it) said a local object's `hosts:` field is "a role name
(`winws`, `linsrv`, …) — the same names DESIGN.md §9's `host_groups`
... already use." That's not what the inventory actually contains:
`scripts/generate-inventory.py` builds Ansible inventory groups from
each host's `roles` list (`domain_controller`, `windows_server`,
`windows_workstation`, `linux_server`, `linux_workstation` —
`ALL_ROLE_GROUPS`), never from a `host_groups` block's own `name:`
label (`winws`, `linsrv`, `dc`, …, which is only a hostname-generation
convenience — DESIGN.md §9 itself says "each *role* a host carries
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

## M5 (workstation support, Ubuntu LTS half): five real bugs, one still open

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

### OPEN: guided storage configuration doesn't honor either `layout` or `config`, defaults to a mandatory encryption prompt

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

Stage 3 (Ubuntu LTS in isolation) is therefore **partially confirmed**:
templating, kernel/initrd boot, the serial-console prompt chain, and
apt/mirror configuration are all confirmed working unattended; disk
provisioning is not, so no Ubuntu LTS host has yet completed a full
unattended install through to a bootable, SSH-reachable final system.
