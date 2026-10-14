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
sudo deploy/install-labape.py --profile kvm --set WorkerPublicKey='ssh-ed25519 ...'   # KVM host only
sudo deploy/install-labape.py --profile web --set RemoteKvmUri=qemu+ssh://labape@kvm1/system  # web only
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
| `--profile` | `full` (default: KVM host and web interface on this machine), `kvm` (KVM host only, driven by a web interface elsewhere) or `web` (web interface only, driving a remote KVM host over SSH). Same as `--set Profile=...`. |
| `--config-file` | Answer file (JSON). Start from `answers.sample.json`, and copy it to `answers.<name>.json` (git-ignored). |
| `--set NAME=VALUE` | One answer, overriding every file. JSON values are allowed (`true`, `[...]`). |
| `--instance` | Installation name; state is kept per name. Defaults to the only saved one, otherwise `labape`. |
| `--step` / `--start-at` / `--resume` | Run a subset; these three are alternatives. |
| `--list-steps` | Each step and its saved status. |
| `--dry-run` | Print every change instead of making it. Read-only checks still run. |
| `--force` | Redo work a step would otherwise skip: rewrite `environment.yml` or the vault, reinstall tools. |
| `--non-interactive` | Never prompt. A missing answer is an error. |
| `--fresh` | Ignore the saved answers and ask everything again. |

Answers are resolved in this order: `--set`, then `--config-file`, then the saved answers, then a prompt or the default. **Every run reuses the saved answers** (`deploy/state/answers.<instance>.json`), so a re-run or an update asks only questions added since (question 141); `--fresh` asks again. All questions are asked before any step runs; when anything was asked, the run shows every answer (the new ones marked) and waits for a yes before it saves them and starts. A plain run, without `--resume`, `--step` or `--start-at`, starts a fresh progress record.

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
| `Prereq.Os` | Root, Debian (any profile) or Rocky/Alma 9+ (kvm profile only), `/dev/kvm` present | |
| `Prereq.User` | Creates the service user if missing | useradd |
| `Prereq.Packages` | QEMU/KVM, libvirt, virt-install, xorriso, Python, NetworkManager, smartmontools (apt; dnf on the RHEL family, plus SELinux tools, `dnf-utils` and, on 9.x, Python 3.12 for Ansible) | apt, dnf |
| `Prereq.Libvirt` | libvirt's modular daemons (sockets, where the system has them, e.g. RHEL 9+) or `libvirtd`; service user in `libvirt` and `kvm` | systemctl, usermod |
| `Net.Validate` | Checks the network answers: a default exists, ranges are inside their networks, static addressing has a pool | |
| `Net.BridgeNetfilter` | Stops iptables filtering bridged VM traffic; otherwise Docker drops it (Claude_Docs/Testing_Troubleshooting-Log.md) | sysctl |
| `Net.Bridges` | Creates each missing VM bridge on its NIC with NetworkManager. The host takes a DHCP address on it but never its default route. Refuses the NIC carrying the default route. | nmcli |
| `Storage.Dirs` | VM, template and ISO directories; with SELinux, labelled `virt_image_t` (VMs, templates) and `virt_content_t` (ISOs) | semanage |
| `Storage.TemplatePool` | libvirt pool `labape-templates` | |
| `Tools.Ansible` | ansible-core, pywinrm and the four collections in `/opt/labape/venv`, linked into `/usr/local/bin`. Every install needs it, for the vault. | |
| `Tools.OpenTofu`, `Tools.Packer` | Checksum-verified downloads into `/usr/local/bin`, plus the Packer plugins (`InstallCli`) | |
| `Config.Secrets` | Vault, vault password, SSH key (see above). Kept if present. Runs for the full and web profiles, and on a `kvm` host only with `CliCheckout` (jobs from the web interface bring their own). | |
| `Config.Environment` | `tofu/environment.yml`: domain, the network catalog, storage paths, and ISOs found in `IsoPath` by pattern. Kept if present. Same condition. | |
| `Config.Manifests` | Software and directory manifests from the examples, if missing. Same condition. | |
| `Stack.Runtime` | Docker (or Podman) with compose; service user in `docker` (`InstallWebUi`) | apt |
| `Stack.Setup` | `container/setup.sh`, then copies the config and engine secrets into `container/` | |
| `Stack.Services` | The optional services not chosen go into `container/compose.override.yaml` under a profile nobody enables (Compose reads the file by itself, so every `compose up`, the update agent's included, honours it); `LABAPE_CONJUR` and `LABAPE_SYSLOG_BIND` in `container/.env`. Without the file every service runs (installs from before this step). | |
| `Stack.Auth` | Authentik installed with LABaPe (`InstallAuthentik`), or else an existing OIDC provider (issuer, client ID, prompted secret), a throwaway Authentik for the first sign-in (`tools/authentik-test`), or none (break-glass only) | |
| `Stack.Build`, `Stack.Up` | Builds the image and starts the stack; waits for `/api/health` | |
| `Stack.Stores` | `container/stores/stores.sh setup` as the service user: OpenBao (always) and Conjur (`InstallConjur`) in the stack with certificates from the platform CA, initialized and unsealed, LABaPe's logins set in its store settings, and user units that unseal OpenBao at boot and renew the certificate daily. Re-run it on an existing install to add the stores. | loginctl |
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
| `Profile` | `full`, `kvm` or `web` (see `--profile`) | `full` |
| `InstallCli` | OpenTofu and Packer on this host (a KVM host's jobs run them) | yes, except the web profile |
| `CliCheckout` | `kvm` profile: also set up a usable command-line checkout (vault, `environment.yml`, manifests) and ask its questions | no on `kvm`, always on full and web |
| `InstallWebUi` | The web stack; follows the profile | per profile |
| `InstallConjur`, `InstallSyslog`, `InstallRemoteAccess`, `InstallUpdateAgent`, `InstallAuthentik` | Optional services of the web stack (question 140); OpenBao is always installed | yes (`InstallAuthentik`: no when an older answer file named another `AuthMode`) |
| `SyslogBind` | The address the syslog collector publishes 514 and 6514 on (`0.0.0.0`: every address) | this host's IP |
| `WorkerPublicKey` | `kvm` profile: the web interface's worker key (`labape worker-key`, or the host's page) to add to the service user's `authorized_keys` | empty (skip) |
| `RemoteKvmUri` | `web` profile: the KVM host to register, `qemu+ssh://user@host/system` | prompted |
| `KvmHostName` | This host's name in the web interface | short hostname |
| `VmStoragePath`, `TemplateStoragePath`, `IsoPath` | Storage | `/data/VMs/LABaPe`, `<that>/templates`, `/data/OS_Images` |
| `DomainName`, `NetbiosName` | AD domain for lab environments | `labape.test`, `LABAPE` |
| `ManagementSource` | IP/CIDR allowed to SSH to lab VMs | this host's IP |
| `Networks` | The VM network catalog. Each entry: `Name`, `Cidr`, `Gateway`, `DnsServers`, `Bridge`, `Nic` (to build the bridge on; empty if it exists), `HostAddress` (`dhcp`/`none`), `Addressing`, `StaticPools`, `DhcpRanges`, `Reserved`, `AllowedGroups` | prompted |
| `DefaultNetwork` | For host groups that don't name one | the first network |
| `WebHostname`, `Tls` | Web interface address (browsers must use exactly this name: the certificate is for it); `labape-ca` (a certificate from LABaPe's platform CA: browsers trust its root, downloadable from the dashboard, the same root the VMs use), `internal` (Caddy's own CA, a second root to trust) or an ACME email | this host's IP, `labape-ca` |
| `ContainerRuntime` | `docker` or `podman` | `docker` |
| `PlatformStore` | Which store holds LABaPe's own secrets: `openbao` or `conjur` (asked only when Conjur is installed). Written to `LABAPE_PLATFORM_STORE` in `container/.env`; change it there and re-run `container/stores/stores.sh setup` to move them. | `openbao` |
| `AuthMode` | Only when Authentik isn't installed with LABaPe: `existing`, `test-authentik` (a throwaway for the first sign-in) or `none` | `test-authentik` |
| `OidcIssuer`, `OidcClientId` | For `AuthMode=existing` | (prompted), `labape` |
| `AdminGroups` | Extra groups mapped to admin | none |
| `TemplatesToBuild` | Answer file only (templates are built from the web interface), e.g. `["rocky9:rocky9-base-2026.10", "windows_server_2022:win2022-core-2026.10:core"]` | none |
| `TofuVersion`, `PackerVersion`, `AnsibleCoreVersion` | Toolchain versions (match `container/Dockerfile`) | pinned |

## Profiles

- **full**: every step, as before.
- **kvm**: the KVM host side: packages, libvirt, bridges, storage, the template pool, Ansible, OpenTofu and Packer, the vault, `environment.yml`, and `Remote.AuthorizeWorker`, which adds `WorkerPublicKey` to the service user's `authorized_keys`. The `Stack.*` steps don't run.
- **web**: the web stack only. No `/dev/kvm` is needed, and only the base packages are installed. `Stack.Bootstrap` registers `RemoteKvmUri` as the host. The summary says how to print the worker's key (`labape worker-key`) for the KVM host's install.

Remote hosts work the way design §22.4 describes: jobs for a `qemu+ssh` host run on that host over SSH.

## Updates

Re-running the installer is how a host's own setup is updated: it reads the saved answers and asks only new questions. `Stack.UpdateAgent` (`InstallUpdateAgent`) sets up the host-side update agent for the service user (lingering, a user systemd path unit). The web interface's Check now then offers signed releases and a one-step rollback (design §23.2). Release signers are listed in `deploy/allowed_signers`, copied to `~/.config/labape/allowed_signers`. Until a key is listed there, every update is refused. To go back more than one version: `deploy/labape-rollback.sh --to vX.Y.Z [--restore-backup FILE]`.

## Full-stack backups

`deploy/stack-backup.sh` writes the whole stack (every database, OpenBao, the platform CA, the configuration and the bootstrap secrets) into one archive encrypted with the backup key, `~/.config/labape/backup.key`, which it makes on its first run. Keep a copy of that key somewhere other than the host: no archive opens without it. The update agent runs the script daily and on Settings → Full stack backups → Back up now; it can also be run by hand as the service user (`deploy/stack-backup.sh --help`). Design §32.

## Installing from a backup

`sudo deploy/install-labape.py --from-backup ARCHIVE --backup-key-file KEY` makes this machine the stack the archive was taken of: it installs with the answers saved in the archive (override any with `--set`, e.g. `--set SyslogBind=0.0.0.0` when the old machine's address isn't this one's), then `Stack.Restore` runs `deploy/stack-restore.sh` to put back the configuration, secrets, volumes and databases. Check out a version at least as new as the backup's. `--restore-drill` leaves maintenance mode on, for a test copy next to the real stack. Design §33.

## Changing the web interface's host name

To move from an address to a DNS name (or between names), set the new name and re-run the steps that use it:

```bash
sudo deploy/install-labape.py --set WebHostname=labape.example.lan --step Stack.Setup --step Stack.Auth --step Stack.Up --step Smoke.Web
```

`Stack.Setup` writes it to `container/.env` (engine secrets already in the platform store aren't copied back), `Stack.Auth` points the stack's Authentik at it (the provider's redirect URI included), `Stack.Up` recreates the containers: Caddy then serves a certificate for the new name, and the platform CA adds it to the names it answers to (`container/ca/init.sh`). Browsers that open the site by a name other than `WebHostname` get a TLS "internal error" from Caddy, which only has a certificate for that name.

## Not done by the installer

- **No NIC carrying the default route is bridged.** Moving management onto a bridge is done by hand, because a mistake there cuts off the host.
- **ISOs aren't downloaded.** Copy them into `IsoPath`; `Config.Environment` reports the ones it didn't find.
- **No group membership applies to a running session.** The service user picks up `libvirt`, `kvm` and `docker` at their next login.
- **Existing configuration is kept.** An existing `tofu/environment.yml` and vault are left alone unless you pass `--force`.

## Status

Verified on 2026-10-10 with a full install on a fresh Debian 13 VM (a LABaPe clone with nested KVM and a second NIC):
- Every step completed, with `ContainerRuntime: podman` and `AuthMode: test-authentik`.
- The run used `--resume` and `--step` after fixing the failures it found (Claude_Docs/Testing_Troubleshooting-Log.md, "Installer on a fresh VM").
- It built `br1` on the second NIC, installed the toolchain, wrote the vault and `environment.yml`, built and started the Podman stack, and registered the host and network.

A deploy through that fresh install's web UI then worked end to end: OIDC sign-in, then a static and a DHCP VM nested on the VLAN. The Docker path is the one the lab host's own stack has used throughout.
