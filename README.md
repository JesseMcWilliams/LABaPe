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
  backend — ahead of DESIGN.md §18's original M3 schedule — but is
  intermittently unreliable and not currently usable.** All three
  versions use the same `autounattend.xml`-based unattended install as
  Linux's kickstart path (`iso/answer-files/windows/`), and the
  mechanics are genuinely implemented and code-reviewed: partitioning,
  static/DHCP addressing, WinRM bootstrap (HTTPS listener, self-signed
  cert, Basic auth), and a working `windows_common` Ansible role
  (Chocolatey packages). It has fully succeeded twice — once each for
  Server 2022 and 2025, confirmed end-to-end including Ansible/WinRM
  connecting and installing packages. But repeated retries (including a
  full host reboot in between) fail the same way roughly 3 times out of
  5: Windows Setup silently never finds/uses the answer file at all
  (sits at its first interactive "Language to install" screen forever,
  confirmed via `virsh screenshot` — not a validation error, which
  would show a blocking dialog instead) rather than installing
  unattended. Ruled out via direct testing: host resource exhaustion
  (RAM/disk/network-interfaces/inotify all confirmed healthy), disk bus
  (SATA/floppy/IDE — IDE isn't even supported on this q35 machine
  type), answer-file delivery mechanism (CD-ROM vs. floppy — both fail
  identically), `EI.CFG` differences between the three ISOs (all
  identical), domain-name reuse, and `libvirtd`/host-level state
  (a full reboot didn't change the failure rate). The two successes and
  ~5 failures used byte-identical configuration, so this looks like a
  genuine timing race in Windows Setup's own media-scan logic rather
  than anything this repo's pipeline controls — but that's an inference
  from process of elimination, not a confirmed root cause. `small.tfvars`
  currently ships Linux-only for this reason; add a `windows_server_*`
  `host_group` back once this is resolved, or if you're willing to
  retry `scripts/deploy.sh` on failure (each attempt is a fresh
  unattended install, ~5-10 minutes, so retrying is cheap even if
  unsatisfying).

  Three follow-up fix attempts, each tested against real infrastructure
  rather than just proposed:
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
- DHCP-mode addressing, the Hyper-V backend, domain services, the full
  OS matrix, Packer templates, and the software/directory manifests
  beyond the simple package-manager case are all out of scope for M1 —
  see DESIGN.md §18 for the milestone plan.
