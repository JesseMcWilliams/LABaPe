# Installing and Configuring Ansible (Debian 13)

Control-machine only (DESIGN.md §15) — Ansible doesn't run as a control
node on Windows, so this never targets the Hyper-V host or a Windows
VM, only the same Linux/WSL box `tofu` runs on.

## 1. Why not just `apt install ansible`

Debian 13 enforces [PEP 668](https://peps.python.org/pep-0668/) —
a plain `pip install` outside a virtual environment fails with an
"externally-managed-environment" error unless you force it. Debian's
own `ansible` package works, but tends to lag the latest Ansible
release. **pipx** is the recommended middle ground: it installs a
Python CLI application into its own isolated virtual environment while
still giving you a normal global command, with no system-package
conflicts and no manual venv activation.

```bash
sudo apt-get update
sudo apt-get install -y pipx
pipx ensurepath
source ~/.bashrc   # or open a new shell — picks up pipx's PATH change
```

## 2. Install ansible-core

This repo pins specific collections (§4) rather than wanting the
"batteries included" `ansible` package's large pre-selected bundle, so
install just the engine:

```bash
pipx install ansible-core
```

Verify:

```bash
ansible --version
```

## 3. Add WinRM support (for M3+, not needed by M1's Linux-only scope)

`ansible.windows` modules connect over WinRM, which needs `pywinrm` in
the *same* isolated environment pipx created — a plain
`pip install pywinrm` wouldn't reach it:

```bash
pipx inject ansible-core pywinrm
```

Safe to do now even though nothing in M1 exercises it yet — one less
thing to remember when M3 lands Windows support.

## 4. Install the collections this repo uses

```bash
ansible-galaxy collection install ansible.windows
ansible-galaxy collection install microsoft.ad
ansible-galaxy collection install community.general
ansible-galaxy collection install chocolatey.chocolatey
```

Or all at once with a requirements file:

```bash
cat > /tmp/labape-collections.yml <<'EOF'
collections:
  - name: ansible.windows
  - name: microsoft.ad
  - name: community.general
  - name: chocolatey.chocolatey
EOF
ansible-galaxy collection install -r /tmp/labape-collections.yml
```

What each is for in this repo: `ansible.windows` — `win_package`,
`win_copy`, `win_group`/`win_user` (docs/credentials.md,
docs/software-manifest.md, docs/directory-objects.md). `microsoft.ad` —
OU/domain-group/domain-user/membership management
(docs/directory-objects.md). `community.general` — `zypper` (openSUSE/
SLES package installs) and, later, secrets lookup plugins
(docs/credentials.md §13). `chocolatey.chocolatey` — `win_chocolatey`
for the Windows side of the software manifest.

Verify:

```bash
ansible-galaxy collection list
```

## 5. Set up the vault password file

```bash
# Anything reasonably long and random — this is not itself a secret
# checked into git, just kept out of it (docs/credentials.md §1)
openssl rand -base64 32 > ~/.labape-vault-pass
chmod 600 ~/.labape-vault-pass
```

`scripts/deploy.sh`/`destroy.sh` read this path from
`LABAPE_VAULT_PASS_FILE`, defaulting to `~/.labape-vault-pass` if unset.

## 6. Set up the bootstrap SSH keypair

Ansible's first connection to any freshly created Linux VM authenticates
as the `labape` user via this key (docs/credentials.md §5) — it's the
control machine's own key, not a separate one baked into the image:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/labape_bootstrap -C labape-bootstrap
```

Its path goes in `secrets.vault.yml` as `ansible_ssh_private_key_path`
(`secrets.vault.example.yml` shows the shape) — `scripts/deploy.sh`
reads the matching `.pub` file to pass to OpenTofu, and points
Ansible's generated inventory at the private key.

## 7. This repo's `ansible.cfg`

Already checked in at `ansible/ansible.cfg` — sets the inventory path,
roles path, and disables host-key checking (disposable lab hosts reuse
IPs across rebuilds, DESIGN.md §8/§14, so a stale `known_hosts` entry
would otherwise block every single deploy). Nothing to configure here
beyond what's already committed.

## 8. Test connectivity

Once `scripts/deploy.sh` has run at least once and
`ansible/inventory/generated` exists:

```bash
cd ansible
ansible linux_server -m ping
```

Expect a `SUCCESS` per host, connecting as `labape` over SSH with the
key from §6.

## 9. Troubleshooting

- **`ansible-galaxy collection install` fails to reach the Galaxy
  server** — check outbound HTTPS to `galaxy.ansible.com` specifically;
  it's a separate host from anything OpenTofu needs.
- **`ansible: command not found` after `pipx install`** — `pipx
  ensurepath` needs a new shell (or `source ~/.bashrc`/`~/.profile`) to
  take effect; it edits your shell's startup file, not the current
  session.
- **WinRM connection errors once M3 lands** — confirm `pipx inject
  ansible-core pywinrm` actually landed in the right environment:
  `pipx list` should show `pywinrm` injected under `ansible-core`.
- **`UNREACHABLE` on a freshly created VM** — kickstart may not have
  finished yet (`virt-install --wait -1` should block until it has,
  docs/base-images.md §4), or `labape`'s SSH key doesn't match what
  `render_environment_tfvars.py`/the kickstart template actually baked
  in — check `secrets.vault.yml`'s `ansible_ssh_private_key_path` points
  at the *same* keypair used when the VM was created, not a newer one
  generated afterward.
