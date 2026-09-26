# Validating an OpenTofu/Ansible Install and Configuration

A structured checklist for confirming a control-machine setup
(User_Docs/Install-OpenTofu.md, User_Docs/Install-Ansible.md, or their
Windows/WSL equivalents) actually works, rather than just having run
without visible errors. Each check below has a corresponding script
under `scripts/test/` — run them individually while troubleshooting a
specific step, or all together with `scripts/test/run-all.sh` once
setup is believed complete.

This closes a real gap from the M1 scaffolding session: the OpenTofu
HCL and Ansible playbook/roles were written and manually reviewed but
never executed against a real `tofu`/`ansible` install (no such binary
was available in that session). These scripts are what actually proves
the scaffold works, in an environment where it can.

## 1. Tools and collections are actually installed

**Script**: `scripts/test/test-tools-installed.sh`

Checks: `tofu version` reports `>= 1.6.0` (Claude_Docs/Design_System-Overview.md §6.3's
`required_version`); `ansible --version` runs at all (catches the
`pyo3`/`cryptography` ABI mismatch that can happen when a pip-installed
`ansible-core` picks up a stale system `cryptography` package — a real
failure mode, not hypothetical, hit while preparing this repo);
`python3` has `yaml` importable (every helper in `scripts/lib/` needs
it); and `ansible-galaxy collection list` shows all four collections
User_Docs/Install-Ansible.md §4 installs (`ansible.windows`, `microsoft.ad`,
`community.general`, `chocolatey.chocolatey`).

## 2. The Ansible playbook and roles actually parse

**Script**: `scripts/test/test-ansible-playbook-syntax.sh`

Runs `ansible-playbook --syntax-check` against
`ansible/playbooks/site.yml`. This is a genuinely meaningful check, not
a formality — it caught nothing wrong in this repo's case, but it
*would* catch a malformed task, a bad Jinja expression, or a role
referenced that doesn't exist, before ever touching real
infrastructure. Requires the collections from §1 to be installed
first — module names are resolved (not just YAML-parsed) even at
syntax-check time.

Auto-copies `software-manifest.example.yml` if the real file doesn't
exist yet, since `--syntax-check` still needs *a* file at that path to
parse the play that reads it — doesn't touch it if the real file is
already there.

## 3. The OpenTofu configuration actually validates

**Script**: `scripts/test/test-opentofu-config.sh`

Runs `tofu init -backend=false` + `tofu validate` against
`tofu/backends/libvirt` (and `tofu/backends/hyperv`, once M2 populates
it — the script skips a backend directory cleanly if it has no `.tf`
files yet, rather than failing). Validates HCL syntax, type
consistency, and provider schema conformance — the parts of the M1
scaffold that a manual review can approximate but can't fully replace.

## 4. Backend connectivity — whichever backend you're using

Run whichever applies; skip the other.

**libvirt**: `scripts/test/test-libvirt-connectivity.sh` — confirms
`virsh`/`virt-install` are installed locally (User_Docs/Install-OpenTofu.md
§4) and that `virsh --connect <libvirt_uri> list --all` succeeds using
the URI from `secrets.vault.yml`. This is the single most common
failure point in the whole pipeline (SSH key not authorized on the
libvirt host, wrong URI, `libvirtd` not running) — worth confirming in
isolation before ever running `tofu plan`.

**Hyper-V**: `scripts/test/test-winrm-connectivity.py` — confirms WinRM
is reachable and authenticates, using `hyperv_host`/`hyperv_user` from
the vault (User_Docs/Install-OpenTofu-WSL.md §5). Since
`tofu/backends/hyperv` doesn't exist yet (M2), this validates the host
prerequisite work now so it doesn't need re-checking once that backend
lands.

## 5. Vault and SSH keys are consistent

**Script**: `scripts/test/test-vault-and-keys.sh`

Confirms `ansible-vault view secrets.vault.yml` succeeds with the
configured password file, and that `ansible_ssh_private_key_path`
points at a keypair where both the private key and a matching `.pub`
file actually exist. Doesn't (and can't) confirm that key is the one
actually baked into an already-created VM's kickstart — only that the
vault's own configuration is internally consistent.

## 6. Everything together

**Script**: `scripts/test/run-all.sh`

Runs 1–3 unconditionally, then 4 for whichever backend(s) have
credentials present in the vault (skipped cleanly, not failed, if a
backend's keys aren't set), then 5. Prints a pass/fail summary and
exits non-zero if anything failed — suitable for a final "is this box
ready" check, or for re-running after fixing something §1–§5 flagged.

## What this doesn't validate

Passing every check here means the *tooling* is correctly installed
and the *configuration* is internally consistent — it does not mean a
full `scripts/deploy.sh` run will succeed end-to-end (that still
depends on things these checks can't see in advance, like whether the
Rocky 9 ISO is actually staged at the path `environment.yml` claims, or
whether the bridge device actually exists on the libvirt host). Think
of this as clearing the ground before the first real deploy attempt,
not a substitute for that attempt.
