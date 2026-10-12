# Troubleshooting log

Detailed, blow-by-blow investigation history for bugs that took real
back-and-forth against live infrastructure to run down — kept for the
record because the ruled-out alternatives are as useful as the fixes.
[README.md](../README.md)'s Known Gaps section links here for anything
beyond a one-line summary.

## M1-M6: archived

The closed M1-M6 history (libvirt and Hyper-V bring-up, the Windows
answer-file rounds, domain services, directory objects, workstations,
templates and Packer) is in [Archive_Testing_Troubleshooting-Log-M1-M6.md](./Archive_Testing_Troubleshooting-Log-M1-M6.md).
References to this log from code comments about those milestones point there.

## M10 phase 10a (web interface)

The container stack (`container/`) ran on the lab host under Docker with
a throwaway Authentik (`tools/authentik-test/`). Verified there:
- **Sign-in:** OIDC sign-in for the three test users, with roles taken
  from their Authentik groups.
- **Permissions:** a viewer is refused when creating an environment, and
  can't see another user's environment or its credentials.
- **Host registration:** a KVM host registered through the API.
- **Jobs:** a Rocky template clone deployed and destroyed through the
  API, with OpenTofu state in PostgreSQL, the live log streamed over SSE,
  and cancel through the API. The deploy got through plan, the network
  check, apply, inventory generation and Ansible's connection to the
  clone. Ansible then failed at `linux_common`'s package step, because of
  the bridged-traffic problem below.
- **Break-glass, live through Caddy:**
  - A one-time credential signed in once, and its reuse was refused.
  - A `--local-only` credential was refused on the public listener, even
    with a spoofed `X-LABaPe-Local` header, and accepted on
    `https://localhost:8443`.

### Installing Docker cut bridged VMs off from the LAN (2026-10-10)

The UI's test clone could ping the KVM host but not the LAN gateway,
and DNS queries to the gateway timed out, so `dnf` couldn't resolve the
Rocky mirrors. lab1's long-lived `linsrv1` has the same problem. This
was first blamed on the router, wrongly.

The cause is Docker, installed on the host the same day. The Docker
daemon loads `br_netfilter`, which sets
`net.bridge.bridge-nf-call-iptables = 1`, so frames crossing a Linux
bridge go through iptables. Docker also sets the FORWARD policy to DROP.
A VM on `br0` reaching the host is INPUT and passes; a VM reaching the
gateway is bridged FORWARD traffic and is dropped.

Fix on any KVM host that also runs Docker, as root: stop filtering
bridged traffic, and load `br_netfilter` at boot so the setting applies
after it (Claude_Docs/Planning_Web-Interface-Design.md §20.6). The
alternative, `iptables -I DOCKER-USER -i br0 -o br0 -j ACCEPT` per bridge,
doesn't survive a reboot without extra tooling. Applied on the lab host
the same day (by hand: it needs root, beyond the automation user's scoped
sudo). lab1's `linsrv1` reached its gateway and resolved names again right
away. `container/README.md` lists it as a host prerequisite.

Separately, `rocky9-packer-2026.10.1`'s `/etc/resolv.conf` still lists
`nameserver 10.0.2.3`, QEMU's user-mode DNS from the Packer build, ahead
of the real server. NetworkManager runs with `dns = none`, so nothing
rewrites the file. That isn't the cause above, but it adds a timeout to
every lookup. `template_finalize` should clear the file.

### Authentik refused every grant for a blueprint-made provider

Authorize failed with `invalid_request` ("The request is otherwise
malformed"). Authentik's log said "Invalid grant_type for provider:
authorization_code". Authentik 2026.8's OAuth2 provider has a
`grant_types` list, and a provider created by a blueprint gets an empty
one, which allows no grant at all. The test blueprint now sets
`grant_types: [authorization_code, refresh_token]`. A provider made in the
Authentik UI doesn't hit this, because the form fills the list in.

### Caddy failed the TLS handshake when reached by IP address

With `LABAPE_HOSTNAME` set to an IP address, `curl https://<ip>/` got
"tlsv1 alert internal error". Clients send no SNI for IP addresses, so
Caddy had no name to pick a certificate by. The fix is the
`default_sni {$LABAPE_HOSTNAME}` global option in `container/caddy/Caddyfile`.

### virt-install couldn't import `gi` inside the image

`virt-install` starts with `#!/usr/bin/env python3`. In the image, the
app's venv comes first on `PATH`, so virt-install ran under the venv's
Python and failed with "No module named 'gi'". The venv is now created
with `--system-site-packages`, so it sees Debian's `python3-gi` and
`python3-libvirt`.

### The vault's SSH key path belongs to the CLI host

`deploy.sh` reads `ansible_ssh_private_key_path` from the vault, and that
names a path under the CLI user's home, which doesn't exist in the
container. `deploy.sh` and `destroy.sh` now honor
`LABAPE_SSH_PRIVATE_KEY_PATH` ahead of the vault. The job runner sets it
to its copy of the key pair from `container/engine-secrets/ssh/`.

### `safe-undefine.sh` needed sudo, which the container doesn't have

The destroy job undefined the VM, then failed with "sudo: command not
found" while removing its disk and seed ISO. On the CLI host,
`sudo -n rm` is needed because the files sit in a root-owned directory.
The job runner is root in its container, so the script now runs plain
`rm` when it's already root.

### A disk smaller than the template broke the clone

A host group asked for `disk_gb = 20` from a template with a 40 GB
virtual size. The clone dropped to a dracut emergency shell ("/dev/mapper/
rlm_template-root does not exist"): the overlay truncated the disk, so the
root LV's tail was missing. `create-from-template.sh` now raises a smaller
request to the template's size and prints a warning.

### Operational: cancelled jobs left zombie processes

Cancelling a job (SIGTERM to its process group) worked, but the killed
tools' children became zombies under the worker. As PID 1, the worker
doesn't reap orphans. The app containers now run an init process:
`init: true` in compose, `RunInit=true` in the Quadlets.

### Operational: syncing a Windows checkout gave CRLF line endings

Copying this Windows working tree to the host with `tar` shipped CRLF
endings from `core.autocrlf`: "env: 'bash\r': No such file". Sync from a
git tree instead. Build a temporary index with `git add -A`, write it
with `git write-tree`, and pipe
`git -c core.autocrlf=false archive <tree>` to the host. This needs no
commit.

The `-c core.autocrlf=false` matters. `git archive` applies
`core.autocrlf` to its output, so without it every text file lacking an
explicit `eol=lf` attribute (`.tf`, `.tpl`, `.yml`) arrived with CRLF
endings. That changed the rendered answer files, and a lab1 plan from
that copy wanted to replace all three VMs. With the flag, the plan
matched the host's own checkout (no infrastructure changes). The phase
10a image built earlier that day came from such a copy; images built
from a Linux checkout aren't affected.

### Operational: scripts committed without the executable bit (2026-10-12)

Seven `.sh` files were committed from the Windows checkout as mode
100644: `container/ca/init.sh`, `container/ca/repass.sh`,
`deploy/labape-rollback.sh`, `deploy/update-agent/install.sh`,
`deploy/update-agent/labape-update.sh` and the two
`tofu/modules/vm/libvirt/scripts/lib/` helpers. On the lab host,
`~/LABaPe-web/deploy/update-agent/install.sh` then failed with
"Permission denied" after a `git reset --hard`; `bash install.sh` worked.
Windows has no executable bit, so git keeps whatever mode the index
already had, and a new file starts as 100644. Fixed with
`git update-index --chmod=+x`. For a new script, run that before the first
commit (or check with `git ls-files -s -- '*.sh'`).

### Other setup notes

- **PostgreSQL password file:** PostgreSQL reads its password file as
  uid 70, so `container/setup.sh` makes the files in `container/secrets/`
  0644 inside a 0700 directory.
- **pydantic patterns:** pydantic's Rust regex engine has no look-ahead,
  so the environment name's `test-` exclusion is a validator, not part of
  the pattern.
- **Host-only break-glass:** this can't rely on a loopback client IP,
  because behind the runtime's port publishing a host-local browser
  arrives from the bridge gateway. Caddy's loopback-only listener
  (`127.0.0.1:8443`) adds an `X-LABaPe-Local` header instead, and the
  public listener strips it.

## Networks and IP address management (2026-10-10)

Built per Claude_Docs/Planning_Web-Interface-Design.md §20, and tested on
the new lab VLAN (`br1`, DHCP scope and static pool as in §20.6):

- **lab1 unchanged:** lab1 planned no infrastructure changes under the
  new engine. Its only diff was the same sensitivity marking a plan from
  the host's own `main` shows. It also passed the policy check as the
  legacy one-network catalog.
- **Policy refusal:** a profile whose offset landed inside the DHCP
  scope was refused by `check_ip_policy.py` before anything was created
  ("172.21.48.100 is inside lab-vlan48's DHCP scope").
- **CLI deploy** (`deploy.sh --test`, two Rocky clones):
  - the static one took 172.21.50.8 from the pool (per-network offset 520);
  - the DHCP one's lease (172.21.48.58) was found through the host's ARP
    table;
  - `site.yml` ran clean on both, including EPEL and package installs;
  - destroy left nothing behind.
- **Web UI deploy:**
  - the `lab-vlan48` catalog entry was limited to `labape-deployers` and
    attached to `kvm1` on `br1` as the default;
  - a domain controller on DHCP was refused;
  - the static VM was allocated 172.21.50.1 and the DHCP VM was
    discovered at 172.21.48.120;
  - Ansible was clean, and destroy released the allocation (0 left on
    the network).

## Network follow-ups, lab2 and the installer (2026-10-10)

`lab2` is lab1's replacement on `lab-vlan48`, built through the web UI
from templates: a static DC, plus a DHCP Linux member and a DHCP Windows
member.

### DHCP discovery rejected the Windows VM

`l2win1` was in the host's ARP table, but discovery never accepted it.
The confirmation step required a ping reply, and Windows' firewall drops
ICMP echo. Confirmation is now "probe, then the same MAC must still map
to that address": the probe makes the kernel re-resolve the entry,
whether or not the guest answers it.

Separately, `virsh domifaddr --source arp` sometimes came back empty
while the host's neighbor table had REACHABLE entries for the same MACs.
The table held only 7 entries, so this isn't truncation. Discovery now
reads `/proc/net/arp` directly when it runs on the KVM host (the CLI), and
only goes through libvirt from the worker container, with retries
between looks.

### A cloned Rocky member kept its template's DNS, and the realm join failed

The join role set the NetworkManager connection's DNS to the DC
(`ipv4.ignore-auto-dns`), but `/etc/resolv.conf` still listed the template's
resolvers. cloud-init configures clones with NetworkManager `dns = none`,
so NetworkManager never writes that file. `realm discover` then found no
SRV records ("No such realm found").

The role now checks `/etc/resolv.conf` after the NetworkManager change. If
the file doesn't list exactly the DCs, the role writes it directly; in
that mode neither NetworkManager nor DHCP touches it.

### Ansible Galaxy failures broke image builds

Two builds in a row failed installing the collections:
- one on a corrupted response cache ("Missing expected 'results' in
  ansible-galaxy cache");
- one on a download timeout.

The Dockerfile and the installer now use `--no-cache --timeout 120` and
three attempts. The Dockerfile also installs Ansible and the collections
in a layer before the app is copied in, so app changes no longer
re-download them.

### Debian template clones had no default route and kept the build's DNS

A static clone of `debian13-packer-2026.10` (the installer test VM) came
up with only its connected route and with `nameserver 10.0.2.3`:
- **Default route:** cloud-init's ifupdown (eni) renderer turns the v2
  `routes:` entry into `post-up route add default gw ...`, and Debian 13
  doesn't ship `route` (net-tools), so it failed silently (`|| true`).
- **DNS:** the renderer's `dns-nameservers` lines need the `resolvconf`
  package, which isn't installed either, so the template's
  `/etc/resolv.conf` (written by dhcpcd during the Packer build) stayed.

Fixed for Debian (preseed-family) clones only:
- the network config uses `gateway4`, which renders as ifupdown's own
  `gateway` line;
- static clones write `/etc/resolv.conf` through cloud-init `write_files`.

Rocky and Ubuntu clone seeds render byte-identically to before, checked
with `tofu console` for static and DHCP, so existing clones (`lab2`)
aren't replaced. Earlier Debian clone tests didn't catch this because
they only checked SSH from the host, which is on the same subnet.

### Installer on a fresh VM (2026-10-10)

`deploy/install-labape.py` was run as root on a fresh Debian 13 clone
with nested KVM, a second NIC on `br1`, Podman, and the throwaway
Authentik. Fixes found, all re-run with `--step`/`--resume`:
- **Package check:** dpkg reports a missing package as
  `unknown ok not-installed`, which also *ends with* "installed", so
  `Prereq.Packages` skipped `python3-venv` and `curl`. The check now
  requires the exact status `install ok installed`. `Tools.Ansible` also
  recreates a venv that was made without pip.
- **Compose provider:** `podman-compose` (the Python implementation)
  passed compose.yaml's `${LABAPE_MAX_KVM_HOSTS:-4}` through literally
  in the shared `x-labape` block, so the app wouldn't start. Podman now
  uses the Docker Compose v2 binary as `podman compose`'s provider
  (package `docker-compose` plus `podman.socket`).
- **Leftovers:** containers and a network left by the `podman-compose`
  attempt (`labape_default`, without Compose's labels) blocked the new
  provider and had to be removed by hand. That only matters when
  switching providers.
- **Smoke test temp directory:** `Smoke.Cli`'s temporary directory was
  root's while the check runs as the service user; it's now chowned to
  that user.
- **Authentik with Podman:** `tools/authentik-test/deploy.sh` hard-coded
  `docker compose`. It now honors `$COMPOSE` and falls back to
  `podman compose` when Docker isn't installed.

### Operational: the API and worker raced to create new tables

After an upgrade added tables (`templates`, `user_errors`), the worker
died at startup with `duplicate key value violates unique constraint
"pg_type_typname_nsp_index"`. The API and the worker had both run
`create_all` at the same moment, and both tried to create the same new
table. Docker restarted the worker, so no harm was done. `init_db` now
takes a PostgreSQL advisory lock around `create_all`.

### Open: Firefox doesn't install on Debian 13 (found 2026-10-11)

While testing batch G (the guest agent), a Debian 13 template clone failed
`linux_common`'s apt install with "No package matching 'firefox' is
available". Debian ships Firefox as `firefox-esr`, while Ubuntu's `firefox`
is a transitional package. `ansible/package_catalog.yml` names one apt
package for every apt distribution, so the default software manifest's
Firefox fails on Debian. Everything else in the run completed. A fix needs
per-distribution names in the catalog (for example `apt: {debian:
firefox-esr, default: firefox}`). Not fixed yet; the Rocky and Windows
hosts in the same run were clean.

### Remote KVM hosts: libvirt refused the host key; the scripts wanted the .pub (2026-10-11)

Two problems came up while bringing up batch J against the lab host
registered as `qemu+ssh`:

- **Host key.** `virsh --connect qemu+ssh://...` failed with "Host key
  verification failed". libvirt's ssh transport doesn't take the
  worker's `StrictHostKeyChecking=accept-new`; it only reads
  `known_hosts`. On a first contact, virsh ran before any of the worker's
  own ssh calls had recorded the key. `hostio.Host.prime()` now runs
  `ssh ... true` (accept-new) once per host before libvirt calls, with
  HOME set to the job HOME so both use the same `known_hosts`.
- **Key pair.** The first remote deploy stopped at "ssh-key.pub not
  found": `deploy.sh` checks that the VM SSH key has its `.pub` beside
  it. The runner now ships both into `.labape-remote/`.

### Platform CA bring-up (2026-10-11)

- **step-ca image init leaks the password.** The image's
  `DOCKER_STEPCA_INIT_*` first-start init echoes the provisioner password
  into the container log. LABaPe needs that password to sign, so
  `container/ca/init.sh` runs `step ca init` itself, with the Docker
  secret, and logs only the root fingerprint.
- **Port 9443 taken.** The test Authentik publishes HTTPS on 9443, so
  the CA's renewal port defaults to 9444.
- **Caddy kept its own certificate for an IP address.** The global
  `acme_ca` option is ignored for IP identifiers, which Caddy never sends
  to an ACME CA by default. An explicit `tls { issuer acme { dir ...;
  trusted_roots ... } }` in the site block makes it use step-ca (which
  supports IP identifiers).
- **`include_tasks` with `==` in a free-form template.** `include_tasks:
  "{{ 'a.yml' if x == 'y' else 'b.yml' }}"` failed with "Invalid options
  for include_tasks", because the `=` made Ansible parse the string as
  `key=value` options. Use `include_tasks: {file: "{{ ... }}"}`.
- **Redeploying during a job kills it.** `deploy_web.sh` recreates the
  worker, so a deploy job running at the time died and stale-job recovery
  marked it failed. Retrying it was safe (OpenTofu state was intact), but
  don't redeploy the stack while a job is running.

### A syntax error in cli.py crash-looped the stack (2026-10-11)

An edit to `cli.py` made through a shell heredoc turned `
` inside an
f-string into a real newline. pytest passed, because no test imports
`cli.py`. The image built, and the API and worker crash-looped at start,
with the web interface down for about five minutes. `tests/test_imports.py` now imports
every module. Python edits go through files or the Edit tool, never
inline heredocs with escapes (the same trap as earlier in the session).

### Maintenance mode: a cached flag let a job start (2026-10-11)

The first live test of maintenance mode queued a job straight into the
database right after turning maintenance on, and the worker ran it. The
worker read the flag through the same 5-second cache the API uses, so it
still saw "off". The worker now reads the flag fresh before every claim;
the API keeps the cache (the process that changes the flag clears it).

### Authentik: setting a token's key needs add_token on the token (2026-10-11)

Rotating the directory token got 403 from `POST
/api/v3/core/tokens/labape-directory/set_key/`. What didn't work:

- A role with the global `set_token_key` permission (which the API docs
  name) was refused. Authentik checks object permissions on the token, and
  the `set_key` action goes through its generic `ObjectPermissions`, which
  maps POST to `add_token`.
- Granting it through the blueprint failed. An entry-level `permissions:`
  block on the token raised "For global permissions, first argument must be
  in format app_label.codename", and the failed validation stopped the
  **whole** blueprint applying. An earlier attempt with `!KeyOf` also failed
  validation ("failed to find entry ... and a model instance"; `!Find`
  resolves). Token entries also need `user` and `intent` on update.

What worked: `deploy.sh` runs `role.assign_perms([...view_token, add_token,
set_token_key], obj=token)` in `ak shell`, on the directory token only.
The failed rotation in between kept the old token, as designed.

### Secret rotation R3 bring-up (2026-10-11)

- **`step crypto jwe encrypt` has no `--password-file`.** It only prompts.
  `step ca provisioner update --private-key ... --password-file` didn't
  re-encrypt the provisioner key with the new password either; the script's
  own check caught that before the real `ca.json` was touched. The
  provisioner key (a PBES2-HS256+A128KW / A256GCM compact JWE) is now
  re-encrypted by `labape ca-repass` with `cryptography`, cross-checked
  against the step CLI in both directions.
- **Dry runs of `step ca provisioner update` need `defaults.json`**; without
  it the CLI asks for `--ca-url`.
- **A stale data-key cache.** `_data_keys` was keyed by id alone. In the test
  suite (tables recreated, ids reused) a new key with an old id encrypted
  with the stale key, and the value then didn't decrypt. It's now keyed by id
  and wrapped value.
- **Heredoc escapes again.** `b"\x00"` written through a shell heredoc
  became a real NUL byte in `ca.py` ("source code string cannot contain null
  bytes"). It was fixed with a script file. The rule stands: Python edits go
  through files or the Edit tool.

### Secret rotation R2/R4 bring-up (2026-10-12)

- **A Windows password change that "failed" had worked, and the password
  was lost.** The first live rotation of `lab2`'s local Administrator ran
  `win_user` connected as that Administrator over Basic auth. The module
  set the password, then its next request (sent with the old password)
  was rejected, so it reported FAILED. The rotation treated that as
  "nothing changed" and dropped the new value, and `l2win1` then rejected
  the stored password. It was put back through the domain admin over NTLM
  (`net user` showed "Password last set" at the job's time).
  Reference_Credentials.md §9 already warned about exactly this, and it
  wasn't read first. Now the change runs detached (`async`, `poll: 0`) and
  signing in decides; the candidate password is stored before anything
  changes.
- **ansible-core 2.19+ has no built-in `json` stdout callback** (it's in
  `ansible.posix`), so the first VM-key push failed ("Could not load
  'json' callback plugin"). The old key stayed, as designed. `ansible -o`
  (oneline) works but is deprecated for removal in 2.23. Ad-hoc calls now
  go through a generated playbook and parse PLAY RECAP.
- **A comment edit in a clone seed would have replaced every Linux
  clone.** The clone reinstall hash covers the rendered user-data, comments
  included (the Windows one strips comments). The comment was put back;
  all four Linux answer files render byte-identically for `labape`.
- **A rotated VM key reinstalled a Linux clone.** The end-to-end redeploy
  of `lab2` after the rotations succeeded, but OpenTofu replaced
  `l2lin1`'s `null_resource.vm_from_template`: the clone seed (and the ISO
  answer files) carry the public key, and the reinstall trigger hashes
  them. The Windows password had been masked for exactly this reason; the
  key never rotated before R2. Fixed by hashing with the key each VM was
  created with (`terraform_data.creation_ssh_key`, `ignore_changes`).
  `l2lin1` was rebuilt from its template and redeployed cleanly; it was a
  test VM.
- **`tofu init` for the Hyper-V backend fails** in the image: the
  `taliesins/hyperv` provider isn't in the OpenTofu registry. It's not
  related to this change; libvirt validates.
