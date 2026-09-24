# LABaPe — Lab Automation & Provisioning Engine

Automates deployment of self-hosted test/lab environments (mixed Windows +
Linux servers and workstations) on either Hyper-V or KVM (Rocky/Debian),
using OpenTofu for provisioning and Ansible for configuration/software
installation.

See [DESIGN.md](./DESIGN.md) for scope, architecture, and open decisions,
and `docs/` for the detailed design of base images, networking,
credentials, the software manifest, and directory objects.

## Status

M1 is implemented and has passed a real end-to-end smoke test: the
libvirt backend, Linux-only, bridged networking, direct-ISO-boot only
(DESIGN.md §18). `scripts/deploy.sh libvirt lab1 small` was run against
a real Debian 13 libvirt host — two Rocky 9 VMs built from a real ISO
via kickstart, bridged onto the physical LAN, bootstrapped over SSH,
and configured by `ansible-playbook` (software manifest packages
installed, EPEL repo added) — with 0 Ansible failures on the final run.
See **Known gaps** below for what M1 still doesn't cover.

M2 (Hyper-V backend, Linux-only parity with M1) is **now confirmed
working end-to-end**: `tofu apply` against the test host below creates
a network switch, VHD, and `hyperv_machine_instance`; the VM completes
a real unattended Rocky 9 kickstart install from the boot-media
approach (`tofu/modules/vm/hyperv/scripts/prepare-boot-iso.sh` edits
the vendor ISO's isolinux boot menu to default to
`inst.ks=cdrom:/ks.cfg` — Hyper-V has no equivalent to libvirt's
kernel-arg injection); reboots into the freshly installed OS; and is
reachable over SSH as the `labape` bootstrap user. Getting there
surfaced a real, hard-to-diagnose bug — Generation 1 Hyper-V VMs
default to booting DVD media before the hard disk, so every completed
install just re-ran itself forever, which looked identical to an
install *stall* from the outside — plus a separate `taliesins/hyperv`
provider bug that had to be root-caused and pinned around. See
**Known gaps** below for both, and for the debug-disk diagnostic aid
built (and then shelved) while tracking this down.

**Recommendation:** given the number of real bugs it took to get here —
a Hyper-V-specific BIOS default, several distinct `taliesins/hyperv`
provider crashes each needing its own precise workaround, and WinRM's
general fragility compared to SSH for remote execution — **Linux KVM
(the libvirt backend) is the preferred backend for this project.** M1
had a clean end-to-end run with comparatively few surprises, and
libvirt supports both Linux and Windows guests. Use the Hyper-V backend
when KVM genuinely isn't an option; budget for more operational care if
so.

Test infrastructure for M2: `hvhost1`, a nested Windows Server 2022 VM
(on the M1 libvirt host, which has nested virtualization enabled) with
the Hyper-V role installed — confirmed genuinely functional (not just
"the feature installed") by creating, starting, and stopping an actual
nested VM inside it.

## M1 quickstart (libvirt backend)

Prerequisites, none of which this repo automates yet:

1. A libvirt host (Rocky/Debian) reachable via `qemu+ssh://` from your
   control machine, with a bridge device already set up
   (docs/networking.md §1 — e.g. `nmcli connection add type bridge
   ifname br0`, physical NIC enslaved to it).
2. A Rocky 9 ISO staged on that host's filesystem (path goes in
   `environment.yml`'s `os_iso_paths`, docs/base-images.md).
3. `virt-install`/`virsh` reachable from wherever OpenTofu runs against
   that same `qemu+ssh://` URI (tofu/modules/vm/libvirt shells out to
   `virt-install` directly — see that module's comments for why).
4. OpenTofu, Ansible, and Python 3 with PyYAML on the control machine —
   [docs/install-opentofu.md](./docs/install-opentofu.md) and
   [docs/install-ansible.md](./docs/install-ansible.md) for a dedicated
   Debian 13 control machine, or
   [docs/install-opentofu-windows-wsl.md](./docs/install-opentofu-windows-wsl.md) /
   [docs/install-ansible-windows-wsl.md](./docs/install-ansible-windows-wsl.md)
   if the control machine is WSL2 on the same Windows/Hyper-V box.
5. An SSH keypair for the Ansible bootstrap user (docs/credentials.md §5)
   — `ssh-keygen -f ~/.ssh/labape_bootstrap`.

Setup:

```
cp secrets.vault.example.yml secrets.vault.yml     # fill in libvirt_uri,
                                                    # ansible_ssh_private_key_path
ansible-vault encrypt secrets.vault.yml
echo "your-vault-password" > ~/.labape-vault-pass  # or set LABAPE_VAULT_PASS_FILE

cp tofu/environment.example.yml tofu/environment.yml       # fill in network.*, os_iso_paths
cp tofu/environments/small.tfvars.example tofu/environments/small.tfvars
cp ansible/software-manifest.example.yml ansible/software-manifest.yml
```

Deploy / destroy:

```
scripts/deploy.sh libvirt lab1 small
scripts/destroy.sh libvirt lab1 small
```

`lab1` is the environment instance name (an OpenTofu workspace,
DESIGN.md §6.3) — pick anything; running the same name again re-applies
against that same environment instead of creating a new one.

## Validating your setup

Before the first real `scripts/deploy.sh` run, or any time something's
not working and it's unclear whether it's this repo or the underlying
tooling: [docs/validate-setup.md](./docs/validate-setup.md) plus
`scripts/test/run-all.sh` check that OpenTofu/Ansible are actually
installed correctly, the playbook/HCL actually parse, and (whichever
backend you're using) the libvirt or WinRM connection actually works —
independently of running a full deploy.

```
scripts/test/run-all.sh
```

## Known gaps in this scaffold

- **The libvirt/Linux/bridged/iso_direct path (M1) has been run
  end-to-end against real infrastructure** — see Status above. Getting
  there surfaced and fixed several real bugs the original scaffold
  couldn't have caught without a real `tofu apply`/`virt-install`/
  kickstart run: an invalid OpenTofu precondition, `virt-install`
  called with an incompatible flag combination, a libvirt-internal race
  when creating multiple VMs concurrently, a kickstart `network` line
  using an option this Anaconda version doesn't accept (silently left
  installs hanging indefinitely rather than failing fast), no DNS
  resolver for static addressing, a missing EPEL repo dependency, a
  timing race between a VM finishing its post-install reboot and
  Ansible's first connection attempt, and a pre-flight network check
  that couldn't tell its own already-running VMs apart from a real
  address conflict. None of this was reachable by static review alone.
- **Windows Server support (2019/2022/2025) exists on the libvirt
  backend — ahead of DESIGN.md §18's original M3 schedule — and the
  long-standing intermittent-failure bug below is now RESOLVED (round
  10).** All three versions use the same `autounattend.xml`-based
  unattended install as Linux's kickstart path
  (`iso/answer-files/windows/`): partitioning, static/DHCP addressing,
  WinRM bootstrap (HTTPS listener, self-signed cert, Basic auth), and a
  working `windows_common` Ansible role (Chocolatey packages). Root
  cause and fix: `create-iso-direct.sh` now strips XML comments from
  the answer-file copy it writes to the CD-ROM (`iso/answer-files/
  windows/autounattend-windows-server.xml.tpl` keeps its full
  documentation — only the copy Setup actually reads is stripped).
  Confirmed end-to-end post-fix: full unattended install in 4m17s
  (previously either a fast success or an hour-long timeout with no
  middle ground) with Ansible/WinRM connecting and installing packages
  cleanly. See round 10 below for how this was actually found — pulled
  Windows Setup's own log via a WinPE shell rather than more
  behavioral guessing — and rounds 1-9 for the full history of
  ruled-out alternatives kept for the record.

  Ten rounds of follow-up investigation, each tested against real
  infrastructure rather than just proposed (round 10 found the actual
  fix — the rest are kept for the record, since they're what narrowed
  it down and ruled out a lot of plausible-looking dead ends):
  1. A real historical virt-install bug — multiple CD-ROMs getting a
     non-deterministic *boot order* — was found and confirmed already
     fixed upstream years before the version in use here. Doesn't match
     our symptom anyway: the primary Windows ISO always boots and shows
     Setup's UI reliably; it's specifically post-boot answer-file
     *detection* on the secondary CD that fails intermittently.
  2. Rebuilding the secondary answer-file ISO with
     `-iso-level 4 -untranslated-filenames` (avoiding Joliet/short-name
     ambiguity, per a real-world "automate Windows install in a VM"
     guide) made things *worse* — the guest crashed within under a
     minute instead of idling at Setup's language screen.
  3. Injecting `autounattend.xml` directly into `boot.wim` (both WinPE
     images, via `wimupdate`) and rebuilding the full install ISO around
     it — mirroring how the Linux kickstart path injects straight into
     what's booting, rather than relying on a separately-scanned
     secondary device at all. This produced a **genuinely different
     failure signature**: sustained ~100% CPU for 6+ minutes with the
     screen never advancing past `Booting from DVD/CD...`, vs. every
     previous failure's near-0% CPU idle — real evidence the original
     failures actually were "Setup never finds the file," since this
     approach bypasses that detection step entirely and still fails,
     just differently. `xorrisofs -iso-level 4 -untranslated-filenames`
     broke the boot catalog outright ("Couldn't find BOOTMGR");
     `-iso-level 3 -J -rock` fixed that specific error but still hung.
  4. Tried to fix (3) surgically — patch just `boot.wim` inside the
     *original*, still-bootable ISO via `xorriso -indev/-map/-boot_image
     any replay` instead of having `xorrisofs` regenerate the whole
     image — but `xorriso`'s reader genuinely cannot parse the UDF
     filesystem these ISOs use (confirmed: `-indev` on the untouched
     original ISO sees only one file, `/README.TXT`, at the top level).
     Any `-outdev` write from that state silently discards everything
     it couldn't see — produced a 56KB "ISO" from an 8GB source. Real
     tooling dead end, not a config mistake.
  5. Reviewed Microsoft's official
     [Windows Setup Automation Overview](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-setup-automation-overview?view=windows-11)
     for the actual implicit answer-file search order. Confirms
     read-only removable media (a second CD-ROM) genuinely is searched
     (position 5 of 8), just after read/write removable media (position
     4) — so the mechanism *should* work, matching that it does succeed
     some of the time. Also surfaced an untried location — the
     `\Sources` directory of the Windows distribution itself (position
     6, windowsPE/offlineServicing passes) — so tried adding
     `sources\autounattend.xml` (and, for good measure, one at the ISO
     root too) to a rebuilt primary ISO. Hit the exact same "high CPU,
     screen never advances" hang as (3). A **control test — rebuilding
     the ISO via the identical `xorrisofs` recipe with zero
     modifications at all — hung identically**, which conclusively
     isolates that hang to `xorrisofs` not correctly reconstructing
     this specific Windows ISO's boot structure, completely unrelated to
     autounattend.xml placement. So the "modify the primary boot media"
     family of approaches is blocked by this separate, real tooling
     limitation, not by anything about the answer-file mechanism itself
     — and the original secondary-CD-ROM/floppy mystery remains exactly
     that, a mystery, now with the added confirmation that it
     *shouldn't* be failing per Microsoft's own documented behavior.
  6. Gave Windows guests a permanent VNC display (`--graphics vnc`
     instead of `none`) so failures are actually watchable live rather
     than only via post-mortem forensics, then used that to add two new
     data points instead of more theorizing. First: reproduced the
     failure live, then opened a WinPE command shell (`Shift+F10`) and
     confirmed with `wmic logicaldisk`/`dir` that `autounattend.xml` was
     present, correctly labeled, and byte-for-byte readable at `E:\` (a
     second SATA CD-ROM) at the exact moment Setup was idling at the
     language screen — not a placement or format problem. Repeated the
     same live check with the answer file on a virtual floppy instead
     (`A:\`, confirmed present and readable the same way) — identical
     failure. Two structurally different delivery mechanisms, both
     independently confirmed present-but-unused, which rules out
     media-format/placement and narrows this to *something about
     Setup's search itself* not finding a file that's genuinely there.
     Second: retried the "modify the primary boot media" family once
     more with a fresh, previously-untried `mkisofs`/`xorrisofs` recipe
     from an independent source
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
     thoroughly ruled *in* to Windows Setup's own search logic than
     ruled out of this repo's control, without a fix in hand.
  7. Tried renaming the file on the secondary CD-ROM from
     `autounattend.xml` to `unattend.xml`, on the chance Setup's search
     was keying on the wrong name for this position. Identical failure
     (idle at the language screen). Expected in hindsight — Microsoft's
     documented search order uses `autounattend.xml` specifically for
     the pre-install/windowsPE pass on removable media, `unattend.xml`
     is for later passes against an already-installed system — but worth
     ruling out directly rather than assuming. Reverted to
     `autounattend.xml`.
  8. Checked whether [CVE-2026-0386](https://support.microsoft.com/en-us/servicing/os/windows/2025/12/windows-deployment-services-wds-hands-free-deployment-hardening-guidance-related-to-cve-2026-0386)
     — Microsoft's fix disabling "hands-free" answer-file deployment —
     could explain this. It doesn't apply here: the hardening is scoped
     specifically to Windows Deployment Services (WDS) network/PXE
     deployment, where an `unattend.xml` is exposed over an
     unauthenticated RPC channel via the `RemoteInstall` share; Microsoft's
     own guidance states it applies "only to native... WDS scenarios,"
     and the patch lands on the *WDS server role*, not client-side
     Setup.exe/WinPE. This repo has no WDS/PXE involved anywhere — Setup
     boots straight from a locally-attached ISO — and the install media
     itself predates the fix by years regardless. Tested anyway in case
     of an undocumented interaction: a direct `virt-install` smoke test
     against the **Windows Server 2019** ISO (untouched by any prior
     round) with the same secondary-CD-ROM mechanism hit the identical
     language-screen failure. Confirms both that this CVE isn't the
     cause and that the bug is version-independent across 2019/2022/2025,
     not something specific to the 2022 media used for every prior round.
  9. Tried a third, structurally different delivery mechanism: the
     answer file on a 64MB FAT32 image attached as removable USB storage
     (`virt-install --disk ...,bus=usb,removable=on`) rather than a
     second CD-ROM or a floppy — real-world unattended-Windows guides
     often specifically recommend USB for this. Windows *did* treat it
     differently: WinPE enumerated it as `C:\`, "Removable Disk" (a
     distinct category from "CD-ROM Disc"), rather than the CD-ROM/
     floppy's drive letters. Identical outcome anyway — idle at the
     language screen, and a WinPE shell again confirmed
     `autounattend.xml` present and byte-correct at `C:\` the moment it
     happened. Three structurally unrelated delivery mechanisms
     (CD-ROM, floppy, USB) have now each independently confirmed the
     same signature: the file is genuinely there and readable, Setup
     just doesn't act on it, roughly 3 times out of 5. That pattern is
     hard to square with a per-media-type detection bug and points
     increasingly at something in Setup's own answer-file
     validation/application step being racy — independent of how the
     file arrives — though this remains inference from elimination, not
     a confirmed root cause.
  10. **Root cause, found directly instead of inferred — and fixed.**
      Rounds 1-9 were all behavioral inference from the outside (screen
      state, drive contents) because the one thing never actually
      checked was Windows Setup's own account of what it did. Reproduced
      the failure live, opened a WinPE shell (`Shift+F10`), and searched
      `X:\Windows\setupact.log` (`cmd`'s built-in `find`, not `findstr`
      — not present in this WinPE build) for "nattend". It was all
      there: `Determining if we are in WDS/Unattend mode` followed by
      `[setup.exe] UnattendSearchExplicitPath: Found unattend file at
      [E:\autounattend.xml] but unable to deserialize it; status = 0x1,
      hrResult = 0x800705b9`. Not a detection problem at all — Setup
      finds the file *every single time* — a generic XML-parse failure.
      Ruled out one candidate directly: an explicit `sync` on the
      answer-file ISO before `virt-install` ever opens it (in case an
      un-flushed write was racing the guest's very early read) made no
      difference whatsoever — identical error, byte-identical file. What
      actually explained it: this repo's answer-file `.tpl` has several
      multi-line XML comments (`<!-- ... -->` spanning many physical
      lines) documenting the file for developers — and round 1's own
      tenforums research had *already* surfaced a forum thread
      describing this exact "unable to deserialize" failure with the
      exact same root cause (Windows Setup's XML deserializer chokes on
      multi-line comments despite them being perfectly valid XML) —
      that thread just hadn't been connected to this bug until the log
      line made the connection obvious. Fix: `create-iso-direct.sh` now
      strips XML comments (`perl -0777 -pe 's/<!--.*?-->//gs'`) from the
      copy it writes to the CD-ROM, leaving the `.tpl` source's own
      documentation untouched. First real end-to-end test after the fix
      completed the unattended install in 4m17s (previous "successes"
      were the same ballpark; previous failures were the full 3600s
      timeout with no middle ground) with Ansible/WinRM connecting and
      installing packages cleanly. This also retroactively explains why
      it was *intermittent* rather than a flat 100% failure: whatever
      about Setup's deserializer makes multi-line comments sometimes
      tolerable and sometimes not was never identified, and doesn't need
      to be now that the comments are simply gone from the file Setup
      actually reads.
- **M2 (Hyper-V) SSH-reachability mystery: RESOLVED. Root cause was
  never sshd or networking — the VM was never actually booting the
  installed OS.** The three logging/capture channels below (install-time
  syslog, post-install rsyslog forwarding, serial console) were built to
  chase what looked like "install completes, guest is healthy
  (`Heartbeat` OK, VHD grew, uptime counter reset), but SSH is
  unreachable for 20+ minutes." All three came up completely empty —
  and retrospectively, that's because they were investigating a false
  premise: a real, fully-installed guest was never actually failing to
  answer SSH. **Generation 1 Hyper-V VMs default their BIOS boot order
  to DVD before hard disk** (`Get-VMBios`'s `StartupOrder`:
  `{CD, IDE, LegacyNetworkAdapter, Floppy}`), and this module leaves the
  boot ISO and kickstart ISO permanently attached. So the kickstart
  install genuinely *did* complete successfully every time (confirmed:
  matches this module's real install duration, ~20 minutes) and reboot
  — straight back into the same boot ISO, re-running the unattended
  install from scratch, forever. Every symptom (VHD growth stopping,
  "connection refused" then timeouts, `Heartbeat` sometimes OK/sometimes
  not) was a snapshot of *some* point in that infinite loop, not a
  stalled or unhealthy guest. Found by direct observation rather than
  more inference: `vmconnect.exe <hyperv-host> <vm-name>` (the actual
  Hyper-V console — see below) showed the installer's own summary
  screen mid-loop ("Not enough free space on selected disks" from an
  earlier, unrelated debug-disk-related kickstart bug — see below), and
  once that was fixed, showed the boot menu itself defaulting back to
  "unattended install" instead of the newly installed OS after a
  completed install. **Fix**: `Set-VMBios -StartupOrder` with `IDE`
  before `CD` (`tofu/modules/vm/hyperv/scripts/set-boot-order.sh`).
  Confirmed end-to-end after the fix: the guest boots the installed OS,
  `Heartbeat`/`Shutdown`/`Time Synchronization`/`VSS` integration
  services all report `OK`, and `ssh labape@<ip>` succeeds. (`Key-Value
  Pair Exchange` still shows `No Contact` — minor, likely just the
  `hyperv-daemons` package/`hv_kvp_daemon` not being installed by
  default; doesn't block anything observed so far.)

  **Not automated as a Terraform resource** — tried wiring
  `set-boot-order.sh` in as a `null_resource` with `depends_on` on the
  VM, and hit a real, reproducible `taliesins/hyperv` provider crash
  every time ("Unable to remove resource pool from dvd drive") whenever
  anything caused Terraform to reconcile the existing
  `hyperv_machine_instance` — including just the VM being stopped/
  started externally. Root-caused precisely: the provider computes
  `dvd_drives[].resource_pool_name` (`"Primordial"`, Hyper-V's default)
  and a `vm_processor` block that this module's config never declared,
  so every `tofu apply` against an existing VM saw permanent drift and
  tried (and crashed) reconciling it. Fixed *that* by pinning both
  explicitly in `tofu/modules/vm/hyperv/main.tf` — `tofu plan` now
  reports zero drift — but the boot-order fix itself still can't safely
  run as a resource (it would hit the same crash on a *fresh* VM's very
  first apply, before any drift exists to have pinned yet). Run
  `scripts/set-boot-order.sh` by hand once after `tofu apply` until this
  is better understood.

  One casualty of this investigation: a debug-disk diagnostic aid
  (`var.debug_disk`, off by default) was built — a second small
  FAT-formatted VHD the guest's `%post` mounts and writes a diagnostic
  dump to, readable from the Windows side via `Mount-VHD` without
  needing network or serial console cooperation. Attaching it exposed a
  *second*, independent bug: without an explicit `ignoredisk`, Anaconda
  considers every attached disk during partitioning, and Hyper-V's
  guest disk-enumeration order for `sda`/`sdb` was confirmed unstable
  across boots (one run saw the 64MB debug disk come up as `sda`, the
  40GB OS disk as `sdb` — the opposite of the naive assumption), so a
  hardcoded `ignoredisk --only-use=sda` silently pinned the wrong disk.
  A kickstart `%pre`-generates-then-`%include`s-a-file approach to pick
  the disk dynamically by size was tried and found to not work reliably
  in this Anaconda version (`%include` appears to resolve before `%pre`
  has actually run). Given the console access this whole investigation
  produced made the debug-disk's original motivation moot, it was
  reverted to its simple, twice-proven-working single-disk form rather
  than chasing a third fix; `var.debug_disk` still exists and its
  `%post` dump script is written defensively (finds the debug disk by
  exact size, never by assumed device name — see the script for why),
  but needs the attach-after-install redesign noted in code comments
  before it's something to actually rely on.

  Kept for the record, the three capture channels that were built while
  the real cause was still unknown (all legitimate infrastructure, none
  of them wrong to have tried — the premise they were testing just
  turned out to be false):
  1. **Install-time syslog** — kickstart's native `logging --host=...
     --port=1514` command (`scripts/test/syslog-capture.py`, a small
     UDP listener). Confirmed the network path itself works (a test
     packet sent directly from the Hyper-V host arrived fine) — but
     zero bytes ever arrived from the guest across two full install
     cycles.
  2. **Post-install rsyslog forwarding** — `%post` installs and enables
     `rsyslog` with `*.* @<host>:1514`, gated the same way, meant to
     keep logs flowing *after* reboot too. Also zero bytes.
  3. **Serial console** — added `console=ttyS0,115200n8 console=tty0`
     to the shared kickstart's `bootloader --append` and a Hyper-V
     COM1-to-named-pipe redirect, read via a small PowerShell client.
     Zero bytes even with the reader connected before `Start-VM`.
  In hindsight, all three were plausibly reading a guest stuck back at
  Anaconda's boot menu / early installer environment rather than a
  booted final system — which the install-time `logging` command and
  serial console *should* have covered but didn't; that gap (why
  install-time capture also came up empty) wasn't separately
  root-caused and is worth another look if these channels matter again.
- DHCP-mode addressing, domain services, the full OS matrix, Packer
  templates, and the software/directory manifests beyond the simple
  package-manager case are all out of scope for M1/M2 — see DESIGN.md
  §18 for the milestone plan.
