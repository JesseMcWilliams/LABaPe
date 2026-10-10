# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Windows 10 and Windows 11 workstations (`windows_10`, `windows_11`) install unattended and domain-join; Windows 11 host groups must set `disk_gb` >= 64 or the plan fails.
- `ubuntu_lts`, `ubuntu_26` and `debian_latest` all install unattended and join the domain as Linux workstations.
- Host-group names must be unique across environments sharing one libvirt host; a create refuses another workspace's VM.
- Rotating the Windows bootstrap password: change it on running hosts first, then the vault (Claude_Docs/Reference_Credentials.md §9); VMs aren't reinstalled.
- Windows VMs on the libvirt backend use legacy BIOS, so Secure Boot/TPM scenarios aren't supported.
- `deploy.sh`/`destroy.sh` options: `--test`, `--no-ansible`, `--env-file`, `--directory-manifest` (throwaway test environments next to a long-lived one).
- Templates: `scripts/promote-to-template.sh` and host-group `image_source = "packer_template"` + `template = "<name>"`; `template_storage_path` in environment.yml.
- `scripts/build-template.sh <os-key> <template-name>`: build a template with Packer (installed on the libvirt host in ~/.local/bin, plugins via `packer init`).
- Windows hosts get .NET Framework 4.8 automatically where an older 4.x is installed (Server 2019); Firefox can't be installed on Server 2019 Core.
