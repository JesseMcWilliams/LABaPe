# Installing and Configuring OpenTofu (Debian 13)

This targets the **control machine** — the Linux/WSL box that runs
`tofu`/`ansible-playbook` (DESIGN.md §15), not necessarily the libvirt
host itself. If this same Debian 13 box is *also* your libvirt host
(a common single-box self-hosted setup), §5 below covers the extra
host-side packages; everything else is identical either way.

## 1. Install OpenTofu

Two supported paths. The APT repository is recommended — it gets
updates through `apt upgrade` like everything else on the box, rather
than needing a manual re-run later.

### Option A — APT repository (recommended)

```bash
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://get.opentofu.org/opentofu.gpg | sudo tee /etc/apt/keyrings/opentofu.gpg >/dev/null
curl -fsSL https://packages.opentofu.org/opentofu/tofu/gpgkey | \
  sudo gpg --no-tty --batch --dearmor -o /etc/apt/keyrings/opentofu-repo.gpg >/dev/null
sudo chmod a+r /etc/apt/keyrings/opentofu.gpg /etc/apt/keyrings/opentofu-repo.gpg

echo \
  "deb [signed-by=/etc/apt/keyrings/opentofu.gpg,/etc/apt/keyrings/opentofu-repo.gpg] https://packages.opentofu.org/opentofu/tofu/any/ any main
deb-src [signed-by=/etc/apt/keyrings/opentofu.gpg,/etc/apt/keyrings/opentofu-repo.gpg] https://packages.opentofu.org/opentofu/tofu/any/ any main" | \
  sudo tee /etc/apt/sources.list.d/opentofu.list > /dev/null
sudo chmod a+r /etc/apt/sources.list.d/opentofu.list

sudo apt-get update
sudo apt-get install -y tofu
```

### Option B — standalone installer script

No repository added to the system; installs a single binary. Useful if
you don't want to manage another APT source, or need a specific
version pinned outside of whatever the repo currently ships.

```bash
curl -fsSL https://get.opentofu.org/install-opentofu.sh -o install-opentofu.sh
chmod +x install-opentofu.sh

# Verifies the download's signature — needs gnupg (already on most
# Debian installs; `sudo apt-get install -y gnupg` if not). Only fall
# back to --skip-verify if you understand you're skipping that check.
./install-opentofu.sh --install-method standalone

rm install-opentofu.sh
```

`--install-method deb` (installs via a temporary local repo, same
effect as Option A) and `--install-path /some/dir` are also available
— see `./install-opentofu.sh --help`.

## 2. Verify

```bash
tofu version
```

Should report `1.6.0` or newer — `tofu/backends/*/versions.tf` in this
repo requires `>= 1.6.0`.

## 3. Provider plugin cache (recommended)

This repo has two backend directories (`tofu/backends/hyperv`,
`tofu/backends/libvirt`), each with its own `.terraform/` provider
download. A shared plugin cache avoids re-downloading the same
provider version twice:

```bash
mkdir -p ~/.terraform.d/plugin-cache
cat >> ~/.bashrc <<'EOF'
export TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
EOF
source ~/.bashrc
```

(OpenTofu kept Terraform's `TF_*` environment variable names for
compatibility — this one works as-is.)

## 4. Install this repo's provider dependencies

`tofu/backends/libvirt` uses the `dmacvicar/libvirt` provider, which
itself needs the system `libvirt` client libraries present to build/run
against — not just the OpenTofu provider binary:

```bash
sudo apt-get install -y libvirt-clients virtinst
```

`virtinst` provides `virt-install`, which `tofu/modules/vm/libvirt`
shells out to directly for the `iso_direct` path (docs/base-images.md
§4 — the libvirt *provider* has no native kickstart-injection
primitive, so this repo uses `virt-install --initrd-inject` instead;
see that module's `main.tf` comments for the full reasoning).

## 5. If this box is *also* the libvirt host

Skip this section if your libvirt host is a separate machine. If it's
this same Debian 13 box:

```bash
sudo apt-get install -y qemu-system-x86 libvirt-daemon-system \
  libvirt-clients bridge-utils virtinst virt-manager cpu-checker

# Confirms hardware virtualization is available and usable
kvm-ok

# Lets your user run virsh/virt-install without sudo
sudo usermod -aG libvirt,kvm "$USER"
newgrp libvirt
```

Bridge setup for VM networking (docs/networking.md §1) is a separate
step from installing these packages — see that doc.

## 6. Configure access to the libvirt host

If the libvirt host is remote, OpenTofu and `virt-install` both connect
over SSH via a `qemu+ssh://` URI (docs/credentials.md §3). This is a
one-time manual prerequisite, not something OpenTofu sets up for you:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/labape_libvirt -C labape-libvirt
ssh-copy-id -i ~/.ssh/labape_libvirt.pub user@your-libvirt-host

# Verify before involving OpenTofu at all
virsh --connect qemu+ssh://user@your-libvirt-host/system list --all
```

If that `virsh` command hangs or refuses, fix it there first — OpenTofu
will fail the same way, just with a less direct error.

`libvirt_uri` in `secrets.vault.yml` (docs/credentials.md §1) is this
same connection string, e.g.
`qemu+ssh://user@your-libvirt-host/system`.

## 7. Initialize this repo

```bash
cd tofu/backends/libvirt
tofu init
```

From here, `scripts/deploy.sh` (repo root) drives `tofu plan`/`apply`
for you — see the root README's quickstart.

## 8. Troubleshooting

- **`tofu init` can't download the provider** — check the control
  machine has outbound HTTPS to `registry.opentofu.org` (proxies/
  firewalls sometimes block it even when general internet access
  works).
- **`virsh --connect qemu+ssh://...` asks for a password every time**
  — the SSH key isn't being offered; check `~/.ssh/config` doesn't
  override `IdentityFile` for that host, or pass it explicitly:
  `qemu+ssh://user@host/system?keyfile=/home/you/.ssh/labape_libvirt`.
- **`error: Failed to connect socket ... Permission denied`** from
  `virsh`/`virt-install` — the SSH user isn't in the `libvirt` group on
  the *host* side (§5's `usermod` step applies there too, not just
  locally if the control machine and host are the same box).
- **`tofu apply` hangs during VM creation** — that's
  `virt-install --wait -1` blocking on the kickstart install
  (docs/base-images.md §4); check the VM's console
  (`virsh --connect <uri> console <name>`) for what Anaconda is
  actually doing before assuming it's stuck.
