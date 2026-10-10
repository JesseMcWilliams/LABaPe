# Backend parity: libvirt/KVM vs Hyper-V

Audit of the two OpenTofu backends against each other (2026-10-10, after
M6 phase C), from the code: `tofu/backends/{libvirt,hyperv}`,
`tofu/modules/{vm,network}/{libvirt,hyperv}` and the scripts that drive
them. Claude_Docs/Design_System-Overview.md §6.1/§6.2 requires the two backends'
modules to expose the same inputs and outputs, so an environment
definition works on either. Both backends pass `tofu validate`; the
Hyper-V backend was last deployed end to end in M2 and wasn't re-deployed
for this audit.

**Bottom line:** the Ansible side is fully shared, so configuration
(domain, join, software, directory objects) is at parity by design. The
provisioning side isn't: Hyper-V provisions only RHEL-family Linux from
ISO, and everything since M2 (Windows, Debian family, templates, test
mode, the safety guards) exists only on libvirt.

## Feature matrix

| Area | libvirt/KVM | Hyper-V | Gap |
|---|---|---|---|
| RHEL family from ISO (kickstart) | Yes | Yes | — |
| Windows Server / client from ISO | Yes (2019/2022/2025, 10, 11; Desktop Experience default, `windows_core`) | No | Large |
| Ubuntu (subiquity) / Debian (preseed) from ISO | Yes | No | Large |
| Templates: clone (`image_source = "packer_template"`) | Yes (qcow2 overlay + cloud-init / unattend.xml) | No (`packer_template_not_implemented` fail-fast) | Large |
| Templates: Packer build (`build-template.sh`) | Yes (QEMU builder, all OS keys) | No (`hyperv-iso` builder planned) | Large |
| Templates: promote / refresh | Yes | No (scripts refuse non-libvirt) | Large |
| `scripts/deploy.sh` / `destroy.sh` (incl. `--test`) | Yes | No (both refuse `hyperv`; M2 used tofu directly) | Medium |
| Boot order after create | Automatic | Manual `scripts/set-boot-order.sh` after every apply (README Known Gaps) | Medium |
| Workspace ownership guard (VM tag) | Yes (`labape-workspace=` description) | No | Medium (safety) |
| `min_disk_gb` plan-time check | Yes | No (no OS that needs it yet) | Small |
| Rendered answer files per workspace | Yes | Yes | — |
| Reinstall trigger ignores password/comments | Yes (masked, comment-stripped md5) | N/A (kickstart only, no secret) | — |
| Static IP addressing | Yes | Yes | — |
| DHCP addressing | Not built on either (§17.4) | Not built | — |
| NAT network mode (M7) | Not built | Not built | — |
| Debug disk / syslog capture | No | Yes (`debug_disk`, `syslog_host`, M2 investigation aids) | Hyper-V-only, by design |
| Ansible configuration (`site.yml`, all roles) | Shared | Shared | — |

## Interface differences

`vm` module inputs present on one side only:

- libvirt only: `libvirt_uri`, `template_name`, `template_storage_path`,
  `windows_core`.
- Hyper-V only: `hyperv_host`, `hyperv_user`, `hyperv_password`,
  `iso_storage_path`, `debug_disk`.

The connection and storage inputs are inherently per-hypervisor and fine.
`template_name`, `template_storage_path` and `windows_core` describe the
*environment*, not the hypervisor; the Hyper-V module should accept them
(failing clearly where unimplemented) so the same host-group definition
plans on both. The backends' `host_groups` type also differs: Hyper-V
lacks the `template` and `windows_core` fields, so a host group using them
is a type error there rather than a clear "not implemented".

Outputs match (`name`, `roles`, `os_family`, `ip_address`), which is
what keeps inventory generation and Ansible backend-agnostic.

## Closing the gaps, suggested order

1. Interface parity first (cheap, prevents silent divergence): add
   `template`/`windows_core` to Hyper-V's `host_groups` and the vm
   module's inputs, failing fast where unimplemented; let
   `deploy.sh`/`destroy.sh` drive the Hyper-V backend.
2. Remove the manual boot-order step (set the boot order at create time).
3. Windows from ISO on Hyper-V (the answer files are already shared and
   hypervisor-neutral apart from the disk/NIC model).
4. Debian family from ISO on Hyper-V.
5. Templates on Hyper-V: VHDX differencing disks for clones, Packer's
   `hyperv-iso` builder reusing the same answer files and
   `template_finalize`, promote/refresh support.
6. Workspace ownership guard for Hyper-V VMs (VM notes field).

Whether to do this at all, and in what order, is a scope decision: see
Claude_Docs/Planning_Questions.md.
