# Using LABaPe from the command line

This guide is for people who run LABaPe's scripts directly on the KVM host, without the web interface. It assumes the host is installed ([deploy/README.md](../deploy/README.md), or by hand with [Install-OpenTofu.md](Install-OpenTofu.md) and [Install-Ansible.md](Install-Ansible.md)) and that you work as the service user in the repository checkout.

Everything here also applies to what the web interface runs for you; the web interface adds address allocation, permissions and the catalog on top. Operating-system notes (Windows 11 disk size, Server Core, legacy BIOS) are in [Web-Interface-Guide.md §6](Web-Interface-Guide.md#6-operating-system-notes).

## Contents

1. [Deploying and destroying](#1-deploying-and-destroying)
2. [Networks](#2-networks)
3. [Templates](#3-templates)
4. [DHCP addresses](#4-dhcp-addresses)
5. [Passwords and keys](#5-passwords-and-keys)

## 1. Deploying and destroying

```bash
scripts/deploy.sh <backend> <instance> <profile> [options]
scripts/destroy.sh <backend> <instance> <profile> [options]
#   e.g. scripts/deploy.sh libvirt lab1 small
```

`<profile>` is `tofu/environments/<profile>.tfvars` (start from the `.tfvars.example` files). `<instance>` names the environment and its OpenTofu workspace.

| Option | Script | Meaning |
|---|---|---|
| `--test` | both | A throwaway test environment. The instance name must start with `test-`, and its inventory goes to `ansible/inventory/<instance>/`, so a long-lived environment's inventory isn't overwritten. On destroy it also deletes the workspace, that inventory and the rendered answer files, without asking. |
| `--env-file <path>` | both | Use this `environment.yml` instead of `tofu/environment.yml`, for example for a second domain. Destroy with the same file. |
| `--directory-manifest <path>` | deploy | Use this directory manifest instead of `ansible/directory-manifest.yml` (its DNs are domain-specific) |
| `--no-ansible` | deploy | Stop after OpenTofu and the inventory |
| `--yes` | destroy | Don't ask for confirmation |
| `--delete-workspace` | destroy | Delete the OpenTofu workspace afterwards |

For example, a throwaway environment next to a long-lived one, torn down unattended:

```bash
scripts/deploy.sh libvirt test-win11 test-win11 --test
scripts/destroy.sh libvirt test-win11 test-win11 --test
```

**Host-group names must be unique** across every environment on one KVM host: VM names are global on the host, and a create refuses a VM that belongs to another workspace.

## 2. Networks

`tofu/environment.yml` holds a `networks:` catalog; nothing outside it can be used. See `tofu/environment.example.yml`:

```yaml
networks:
  lab:
    cidr: 192.168.1.0/24
    gateway: 192.168.1.1          # default: the network's .1
    dns_servers: [192.168.1.1]    # default: the gateway
    bridge: br0                   # the host bridge
    addressing: [static, dhcp]    # allowed modes; default [static]
    static_pools: ["192.168.1.100-192.168.1.199"]
    dhcp_ranges: ["192.168.1.200-192.168.1.250"]   # the DHCP server's scope
    reserved: ["192.168.1.2-192.168.1.20"]         # never assigned
default_network: lab
```

In the profile's `.tfvars`, each host group can set `network` and `addressing` (`static` or `dhcp`). Static addresses start at the profile's `static_ip_offset_start`, or per network with `static_ip_offsets = { <network> = N }`, and must land in a static pool. Domain controllers are always static. A plan that breaks the catalog's rules is refused before anything is created.

## 3. Templates

Cloning a template takes minutes instead of an ISO install. A host group uses one with:

```hcl
image_source = "packer_template"
template     = "rocky9-base-2026.10"
```

Templates live in `template_storage_path` (in `environment.yml`; default `<vm_storage_path>/templates`). A clone's disk is raised to at least the template's size. Templates are never overwritten: give each one a new, dated name.

| Script | What it does |
|---|---|
| `scripts/build-template.sh <os-key> <name> [--core] [--disk-gb N]` | Installs the OS from its ISO with Packer (Packer and its plugins are in `~/.local/bin`, plugins via `packer init`). `--core` builds Windows Server Core. |
| `scripts/promote-to-template.sh <backend> <instance> <vm> <name>` | Generalizes a configured VM (sysprep, or cloud-init clean), shuts it down and copies its disk into the library. Destroy its environment afterwards. |
| `scripts/refresh-template.sh <backend> <template> <new-name> --os <os-key>` | Clones the template into a throwaway environment, applies every OS update, promotes the result under the new name, and removes the throwaway environment. `--network` and `--addressing` choose where the throwaway VM goes. |

For Windows Server, ISO installs get the Desktop Experience unless the host group sets `windows_core = true`.

## 4. DHCP addresses

DHCP VMs' addresses are found after they boot. If a lease changes later (a VM restarted onto a new address, or the DHCP server was reset):

```bash
scripts/refresh-addresses.sh <backend> <instance> [--test] [--env-file <path>] [--timeout <seconds>]
```

It finds each DHCP VM's current address and regenerates the inventory, without touching any VM.

## 5. Passwords and keys

The generated passwords live only in the encrypted vault: read them with `ansible-vault view secrets.vault.yml --vault-password-file ~/.labape-vault-pass`.

**Rotating the Windows bootstrap password on running VMs.** Change it on the running hosts first, then in the vault; changing only the vault locks Ansible out. VMs aren't reinstalled. The full procedure is [Reference_Credentials.md §9](../Claude_Docs/Reference_Credentials.md#9-rotating-the-windows-bootstrap-password-on-a-running-environment).

**When the web interface runs on the same host**, the vault password and the VM SSH key live in its secret store (OpenBao), and the web interface rotates both daily. The command line reads them from there on every run, with its own read-only login in `~/.config/labape/store.env` (written by `container/stores/stores.sh`):
- `scripts/lib/cli-secrets.sh` (called by `deploy.sh`, `destroy.sh` and the template scripts) writes the vault password and the VM key with its previous keys to `$XDG_RUNTIME_DIR/labape-cli/` (in memory, yours only) and points the scripts at them. A script started by another one, or by the web interface, uses what it was given.
- If `secrets.vault.yml` no longer opens with the current password (it was rotated since your last run), it's re-encrypted in place with it, using the previous passwords the store keeps.
- `~/.labape-vault-pass` and `~/.ssh/labape_bootstrap` are no longer used; after the move they're stale copies. Without `~/.config/labape/store.env` the scripts use them as before.

To read the vault by hand: `python3 scripts/lib/store_get.py vault-pass --vault secrets.vault.yml --out $XDG_RUNTIME_DIR/labape-cli/vault-pass`, then `ansible-vault view secrets.vault.yml --vault-password-file $XDG_RUNTIME_DIR/labape-cli/vault-pass`.
