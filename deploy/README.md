# LABaPe installer

`install-labape.py` deploys LABaPe on a Debian KVM host from scratch. It sets up the host prerequisites, the VM network bridges, storage, the toolchain, the vault and configuration, and the web interface's container stack, then registers the host and its networks in the web interface.

It follows the BlueTrack installer's model:
- **Steps:** named steps grouped into phases; you can run all of them, one, or a range.
- **Answers:** saved to a reusable file; only the answers the selected steps need are asked for.
- **Progress:** recorded per step, so a failed run resumes where it stopped.
- **Dry run:** shows every action without changing anything.

Python 3 standard library only, plus `python3-yaml`, which the first steps install.

## Usage

Run as root from a checkout of this repository. The checkout is what gets configured: `tofu/environment.yml`, `secrets.vault.yml` and `container/` are written into it, owned by the service user.

```bash
sudo deploy/install-labape.py                                   # prompts for what it needs
sudo deploy/install-labape.py --config-file deploy/answers.lab.json   # unattended
sudo deploy/install-labape.py --dry-run --config-file deploy/answers.lab.json
sudo deploy/install-labape.py --list-steps                      # where did it get to?
sudo deploy/install-labape.py --resume                          # carry on after a failure
sudo deploy/install-labape.py --step 'Stack.*'                  # (re)run some steps (wildcards)
sudo deploy/install-labape.py --start-at Stack.Setup            # this step and everything after it
sudo deploy/install-labape.py --set InstallCli=false            # override one answer
```

| Option | Meaning |
|---|---|
| `--config-file` | Answer file (JSON). Start from `answers.sample.json`, and copy it to `answers.<name>.json` (git-ignored). |
| `--set NAME=VALUE` | One answer, overriding every file. JSON values are allowed (`true`, `[...]`). |
| `--instance` | Installation name; state is kept per name. Defaults to the only saved one, otherwise `labape`. |
| `--step` / `--start-at` / `--resume` | Run a subset; these three are alternatives. They read the saved answers. |
| `--list-steps` | Each step and its saved status. |
| `--dry-run` | Print every change instead of making it. Read-only checks still run. |
| `--force` | Redo work a step would otherwise skip: rewrite `environment.yml` or the vault, reinstall tools. |
| `--non-interactive` | Never prompt. A missing answer is an error. |

Answers are resolved in this order: `--set`, then `--config-file`, then the saved answers (on `--resume`, `--step`, `--start-at` and `--list-steps`), then a prompt or the default. A plain run, without any of the three, starts a fresh progress record.

## State

Kept in `deploy/state/` (git-ignored), one pair per instance:
- **`answers.<instance>.json`:** every answer given, in the same shape as `answers.sample.json`, so it works as a `--config-file`. Mode 0600. **It never holds a password or secret.**
- **`progress.<instance>.json`:** each step's last status: `Completed`, `Failed` (with the error) or `NotApplicable` (turned off by an answer).

Each run is logged to `deploy/logs/install-<instance>-<time>.log`.

## Secrets

Nothing secret is ever written to the answers file.
- **Vault:** `Config.Secrets` creates the vault password file (`~<user>/.labape-vault-pass`), an SSH key pair (`~<user>/.ssh/labape_bootstrap`) and `secrets.vault.yml`. It generates 24-character random passwords for the Windows bootstrap admin, the domain admin, DSRM and default users. They exist only inside the encrypted vault; read them with `ansible-vault view secrets.vault.yml --vault-password-file ~/.labape-vault-pass`.
- **Web interface:** `container/setup.sh` generates the database password, session key and connection strings into `container/secrets/`.
- **OIDC:** with `AuthMode=existing`, the client secret is prompted for (never echoed) and written to `container/secrets/oidc_client_secret`.

## Steps

| Step | What it does | Needs root for |
|---|---|---|
| `Prereq.Os` | Root, Debian, `/dev/kvm` present | |
| `Prereq.User` | Creates the service user if missing | useradd |
| `Prereq.Packages` | QEMU/KVM, libvirt, virt-install, xorriso, Python, NetworkManager | apt |
| `Prereq.Libvirt` | libvirtd enabled; service user in `libvirt` and `kvm` | systemctl, usermod |
| `Net.Validate` | Checks the network answers: a default exists, ranges are inside their networks, static addressing has a pool | |
| `Net.BridgeNetfilter` | Stops iptables filtering bridged VM traffic; otherwise Docker drops it (Claude_Docs/Testing_Troubleshooting-Log.md) | sysctl |
| `Net.Bridges` | Creates each missing VM bridge on its NIC with NetworkManager. The host takes a DHCP address on it but never its default route. Refuses the NIC carrying the default route. | nmcli |
| `Storage.Dirs` | VM, template and ISO directories | |
| `Storage.TemplatePool` | libvirt pool `labape-templates` | |
| `Tools.Ansible` | ansible-core, pywinrm and the four collections in `/opt/labape/venv`, linked into `/usr/local/bin`. Every install needs it, for the vault. | |
| `Tools.OpenTofu`, `Tools.Packer` | Checksum-verified downloads into `/usr/local/bin`, plus the Packer plugins (`InstallCli`) | |
| `Config.Secrets` | Vault, vault password, SSH key (see above). Kept if present. | |
| `Config.Environment` | `tofu/environment.yml`: domain, the network catalog, storage paths, and ISOs found in `IsoPath` by pattern. Kept if present. | |
| `Config.Manifests` | Software and directory manifests from the examples, if missing | |
| `Stack.Runtime` | Docker (or Podman) with compose; service user in `docker` (`InstallWebUi`) | apt |
| `Stack.Setup` | `container/setup.sh`, then copies the config and engine secrets into `container/` | |
| `Stack.Auth` | Existing OIDC provider (issuer, client ID, prompted secret), a throwaway Authentik (`tools/authentik-test`), or none (break-glass only) | |
| `Stack.Build`, `Stack.Up` | Builds the image and starts the stack; waits for `/api/health` | |
| `Stack.Bootstrap` | `labape bootstrap`: registers this KVM host, the network catalog, the host's network attachments (the default network marked) and admin role bindings. Idempotent. | |
| `Templates.Build` | `scripts/build-template.sh` for each `TemplatesToBuild` entry (`os-key:name`, or `os-key:name:core`). Skips existing templates. | |
| `Smoke.Cli` | As the service user: libvirt, tofu, ansible, and `environment.yml` rendering | |
| `Smoke.Web` | Web interface health and sign-in provider health | |

## Answers

`answers.sample.json` shows every setting.

| Answer | Meaning | Default |
|---|---|---|
| `InstanceName` | Name for this installation's state files | `labape` |
| `ServiceUser` | Owns the checkout, vault and SSH key; runs the CLI | the `sudo` user |
| `InstallCli` / `InstallWebUi` | Which halves to install | both |
| `KvmHostName` | This host's name in the web interface | short hostname |
| `VmStoragePath`, `TemplateStoragePath`, `IsoPath` | Storage | `/data/VMs/LABaPe`, `<that>/templates`, `/data/OS_Images` |
| `DomainName`, `NetbiosName` | AD domain for lab environments | `labape.test`, `LABAPE` |
| `ManagementSource` | IP/CIDR allowed to SSH to lab VMs | this host's IP |
| `Networks` | The VM network catalog. Each entry: `Name`, `Cidr`, `Gateway`, `DnsServers`, `Bridge`, `Nic` (to build the bridge on; empty if it exists), `HostAddress` (`dhcp`/`none`), `Addressing`, `StaticPools`, `DhcpRanges`, `Reserved`, `AllowedGroups` | prompted |
| `DefaultNetwork` | For host groups that don't name one | the first network |
| `WebHostname`, `Tls` | Web interface address; `internal` or an ACME email | this host's IP, `internal` |
| `ContainerRuntime` | `docker` or `podman` | `docker` |
| `AuthMode` | `existing`, `test-authentik` or `none` | `existing` |
| `OidcIssuer`, `OidcClientId` | For `AuthMode=existing` | (prompted), `labape` |
| `AdminGroups` | Extra groups mapped to admin | none |
| `TemplatesToBuild` | e.g. `["rocky9:rocky9-base-2026.10", "windows_server_2022:win2022-core-2026.10:core"]` | none |
| `TofuVersion`, `PackerVersion`, `AnsibleCoreVersion` | Toolchain versions (match `container/Dockerfile`) | pinned |

## Not done by the installer

- **No NIC carrying the default route is bridged.** Moving management onto a bridge is done by hand, because a mistake there cuts off the host.
- **ISOs aren't downloaded.** Copy them into `IsoPath`; `Config.Environment` reports the ones it didn't find.
- **No group membership applies to a running session.** The service user picks up `libvirt`, `kvm` and `docker` at their next login.
- **Existing configuration is kept.** An existing `tofu/environment.yml` and vault are left alone unless you pass `--force`.

## Status

On the lab host, the full plan was checked with `--dry-run`. The steps that don't need root were run for real in a scratch checkout:
- `Net.Validate`;
- `Config.Environment` (it found every ISO by pattern);
- `Config.Manifests`;
- `Config.Secrets` (vault encrypted, passwords generated).

The root steps haven't run for real yet: the automation account's sudo is scoped to a few commands.
