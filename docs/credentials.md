# Credentials: Hypervisor Auth, VM Bootstrap, and the Vault

This covers two distinct problems that both have to be solved before any
Ansible role ever runs, plus how they share one secrets store rather
than each inventing its own:

1. **OpenTofu → hypervisor host** — how OpenTofu authenticates to
   Hyper-V or the libvirt host to create VMs in the first place.
2. **Ansible → freshly created VM** — how Ansible gets its very first
   connection to a VM that nothing has configured yet.

## 1. One vault, not several

`secrets.vault.yml` (Ansible Vault–encrypted, per DESIGN.md §13) is the
**single** source of truth for every credential this project needs —
hypervisor auth, VM bootstrap credentials, and the AD domain admin
password all live in the same file, unlocked with the same vault
password. There's no separate credential store for OpenTofu vs. Ansible;
`scripts/deploy.sh` decrypts it once per run and hands each tool what it
needs (§5).

```yaml
# secrets.vault.example.yml (unencrypted shape — the real file is vault-encrypted)
hyperv_host: hyperv01.lan
hyperv_user: Administrator
hyperv_password: "..."
libvirt_ssh_key_path: ~/.ssh/labape_libvirt

windows_bootstrap_admin_password: "..."   # shared local Administrator password, §4
domain_admin_password: "..."              # authenticates domain JOIN, not promotion — §6
dsrm_password: "..."                      # AD DS promotion's DSRM account, DESIGN.md §8
```

## 2. Hyper-V host authentication

`taliesins/hyperv` connects to the Hyper-V host over WinRM. It needs a
host/port, an admin-capable account on that host, and TLS settings
(self-signed certs are normal for a self-hosted lab, so `insecure = true`
is expected rather than a misconfiguration). These come from
`hyperv_host`/`hyperv_user`/`hyperv_password` in the vault, exported as
`TF_VAR_*` before `tofu apply` (§5) — never written into a `.tfvars`
file that could get committed.

## 3. libvirt host authentication

`dmacvicar/libvirt` connects via a libvirt URI. Since the control
machine is a separate Linux/WSL box rather than the libvirt host itself
(DESIGN.md §15), the realistic default is
`qemu+ssh://user@<host>/system` — SSH, not a password. This is a
**one-time manual prerequisite**, not an ongoing secret to store: run
`ssh-copy-id` once from the control machine to the libvirt host. The
private key's path (`libvirt_ssh_key_path`) lives in the vault only so
`deploy.sh` can point OpenTofu at it; the key itself stays on disk with
normal SSH permissions, same as any other SSH key — it doesn't belong
inside the vault file's encrypted contents.

## 4. Windows VM bootstrap (local Administrator)

The answer files (`iso/answer-files/windows/`) and Packer builds both
need a local Administrator password to set during install, and a WinRM
listener enabled so anything — Packer during a build, Ansible on a live
host — can connect afterward.

That password is **never hardcoded in the checked-in answer-file XML**.
Answer files are rendered from a template at build/apply time
(Packer's own templating for its builds; `templatefile()` for the
direct-ISO-boot path in the `vm` module) with
`windows_bootstrap_admin_password` substituted in from the vault. The
same password is used for Ansible's first WinRM connection to any freshly
created Windows host — there's no separate "initial-only" credential to
juggle. Domain join doesn't remove or disable this local account, so v1
keeps using it for ongoing Ansible connections too rather than adding a
credential-switch step after promotion; using domain credentials instead
is a reasonable later refinement if a specific reason to need it comes
up (e.g. group policy disabling local accounts), not a v1 requirement.

## 5. Linux VM bootstrap (SSH key)

Kickstart/cloud-init/AutoYaST files bake in an **SSH public key**, not a
password — standard practice, and it means there's no plaintext
credential in the answer file at all. The key used is the Ansible
control machine's own SSH key (the same one `ansible-playbook` already
needs for every subsequent connection) — no separate bootstrap keypair
to generate or manage. The public key content is substituted into the
rendered answer file the same way the Windows password is (§4); the
private key never leaves the control machine and is never stored in the
vault.

## 6. Putting it together: `deploy.sh`'s credential flow

```
1. ansible-vault view secrets.vault.yml --vault-password-file ~/.labape-vault-pass
   -> parse out hyperv_host / hyperv_user / hyperv_password / libvirt_ssh_key_path
2. export TF_VAR_hyperv_host=... TF_VAR_hyperv_user=... TF_VAR_hyperv_password=...
3. tofu apply -var-file=profiles/<size>.tfvars
   (VM module renders answer files with windows_bootstrap_admin_password /
    the control machine's SSH public key baked in, per §4/§5)
4. ansible-playbook -i inventory/generated site.yml \
     -e @software-manifest.yml \
     -e @secrets.vault.yml --vault-password-file ~/.labape-vault-pass
   (first connection to every host uses the same bootstrap credential
    the answer file set; dsrm_password is used by the domain_controller
    role during AD DS promotion, DESIGN.md §8 — domain_admin_password is
    used afterward, by windows_domain_join/linux_domain_join, to
    authenticate each host's domain join. Promotion itself runs as the
    connecting WinRM credential, i.e. windows_bootstrap_admin_password,
    which becomes the new domain's Domain Administrator with its
    password unchanged — so until M5 adds dedicated domain-admin
    accounts, domain_admin_password must equal
    windows_bootstrap_admin_password or every join fails to
    authenticate)
```

One vault password (kept out of the repo, e.g. in a password manager or
a `--vault-password-file` outside version control) unlocks everything
both tools need for that run.

## 7. Firewall scoping (given bridged-by-default networking)

Since DESIGN.md §14 defaults to bridged — VMs directly reachable on the
physical LAN rather than behind NAT — the management protocols this
whole document is about (WinRM, SSH) are more exposed than they'd be
behind NAT. The fix in both cases is the same shape: restrict the
listener to the control machine's address instead of leaving it open to
the whole LAN.

This needs a new, non-secret config value — `network.management_source`
in `environment.yml` (DESIGN.md §10): the control machine's IP (or a
small CIDR, if that IP isn't perfectly stable — see the caveat below).
It's not sensitive, so it lives in `environment.yml` rather than the
vault, and gets passed through as one of the `template_vars` the `vm`
module renders into answer files/finalize scripts (§6.1 of DESIGN.md).

```yaml
network:
  management_source: 192.168.1.50    # control machine's IP, or a small CIDR
```

### Windows: scope the WinRM firewall rule

Baked into the answer file's `FirstLogonCommands` (or run as a Packer/
`promote-to-template.sh` provisioner) once WinRM is enabled:

```powershell
Set-NetFirewallRule -Name "WINRM-HTTP-In-TCP" `
  -RemoteAddress "<management_source>"
Set-NetFirewallRule -Name "WINRM-HTTPS-In-TCP" `
  -RemoteAddress "<management_source>"
```

(Only the HTTPS rule matters once HTTPS-only WinRM is in place — keep
both scoped in the meantime if HTTP is still enabled during bring-up.)

### Linux: scope the SSH firewall rule

RHEL family, Fedora, Oracle Linux, openSUSE/SLES (`firewalld`):

```bash
firewall-cmd --permanent --zone=public --remove-service=ssh
firewall-cmd --permanent --zone=public --add-rich-rule=\
'rule family="ipv4" source address="<management_source>" service name="ssh" accept'
firewall-cmd --reload
```

Debian family — Ubuntu, Debian, Mint (`ufw`):

```bash
ufw allow from <management_source> to any port 22 proto tcp
ufw deny 22/tcp
```

Both run as a provisioner step in the same place the OS-family finalize
steps already run (docs/base-images.md §3), so it's part of every
template/promoted image rather than a manual per-VM step.

### Caveat: the control machine's IP has to be knowable at build time

This only works cleanly if `management_source` is stable — a static IP,
or a DHCP reservation, on the control machine. If the control machine's
address genuinely moves around, scope to a small CIDR that covers where
it's expected to be (e.g. a `/29` reserved for management use) rather
than a single IP, trading a little precision for not locking yourself
out. Either way, getting `management_source` wrong before a host is
built means re-provisioning it, not just an Ansible re-run — one more
reason it's worth deciding this value once per environment rather than
improvising it per VM.

Neither this nor HTTPS-only WinRM is a hard requirement to start
building — both are known gaps named now rather than discovered later,
and worth closing before this sees any use beyond a fully trusted
personal LAN.
