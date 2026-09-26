# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Windows 10 and Windows 11 workstations (`windows_10`, `windows_11`) install unattended and domain-join; Windows 11 host groups must set `disk_gb` >= 64 or the plan fails.
- `debian_latest` (Debian 13, preseed) is a working Linux workstation OS; `ubuntu_lts`/`ubuntu_26` still stop at an interactive storage screen.
- Windows VMs on the libvirt backend use legacy BIOS, so Secure Boot/TPM scenarios aren't supported.
