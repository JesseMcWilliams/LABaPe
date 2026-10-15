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

### Secrets test stores bring-up (2026-10-12)

`tools/secrets-test/` was developed on `secretsdev` (VM `secdev1`, Debian 13,
deployed through the web interface so it had a platform certificate).
- **OpenBao 2 has no `file` storage.** The container restarted with "unknown
  storage type file". It uses one-node Raft storage now (`cluster_addr` set).
- **`bao status` exits 2 when sealed.** Under `pipefail`, piping it into the
  "initialized?" check made a sealed, initialized OpenBao look uninitialized,
  and the script tried `operator init` again (refused). It reads the JSON
  output instead, and refuses to initialize while `.env` holds an unseal key.
- **OpenBao stayed down after a reboot.** It exits 0 when the host shuts down,
  and `unless-stopped` didn't restart it; the other containers came back. It
  uses `restart: always`, and the boot unit runs `docker compose up -d`
  before unsealing.
- **`conjurctl policy load` prints created roles' API keys on stderr.** One
  test host key reached a session transcript before the output was
  captured; the next run issued the host a new key through the admin API, and
  the printed one then got 401. The script captures the output now and never
  shows it.
- **The minimal Debian clone had no `curl`**; the script installs `curl`,
  `openssl` and `python3` when missing.
- **A Debian clone's own host name didn't resolve (fixed).** `/etc/hosts`
  still had `127.0.1.1 template` from the Packer build, so `sudo` warned
  "unable to resolve host secdev1" and `hostname -f` failed. cloud-init's
  `update_etc_hosts` only runs with `manage_etc_hosts`, but fixing it in the
  clone seed would change every Linux clone's seed hash and reinstall
  existing VMs on their next deploy (design §24.3). So `site.yml` fixes it
  instead, in a play right after "Wait for connection": an existing
  `127.0.1.1` line becomes `127.0.1.1 <vm>.<domain_name> <vm>` (RHEL-family
  hosts have no such line and are left alone). Existing clones pick it up at
  their next deploy, with no reinstall. Checked on `secdev1`: the line was
  rewritten, `hostname -f` answered `secdev1.labape.test`, `sudo` stopped
  warning, and a second run changed nothing.

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

### Firefox didn't install on Debian 13 (found 2026-10-11, fixed 2026-10-12)

While testing batch G (the guest agent), a Debian 13 template clone failed
`linux_common`'s apt install with "No package matching 'firefox' is
available". Debian ships Firefox as `firefox-esr`, while Ubuntu's `firefox`
is a transitional package. `ansible/package_catalog.yml` names one apt
package for every apt distribution, so the default software manifest's
Firefox fails on Debian. Everything else in the run completed. A fix needs
per-distribution names in the catalog (for example `apt: {debian:
firefox-esr, default: firefox}`). The Rocky and Windows hosts in the same
run were clean.

**Fixed (2026-10-12).** A package-manager spec may now be
`{package: <name>, distributions: {<ansible_facts['distribution']>: <name>}}`;
`linux_common` picks the distribution's name and falls back to `package`.
The repository catalog's Firefox uses it (`Debian: firefox-esr`). Web jobs
merge the database catalog over the file, and the web catalog was seeded
from the old entry, so migration `0003_firefox_debian` updates the seeded
Firefox entry (only while it's still as seeded). It blocked the secrets
dev VM (`secretsdev`), whose first deploy failed here before the platform
certificate step; the redeploy installed `firefox-esr` and got its
certificate.

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

### Syslog collector bring-up (2026-10-12)

Design §27 (Planning_Web-Interface-Design.md). Deployed as environment `syslog1` (Rocky 9, rsyslog 8.2510, SELinux enforcing) and tested with LogaPe's syslog sink from the Windows development machine.

1. **TCP messages arrived 5-20 s late; UDP and TLS were immediate.** A raw probe that kept its TCP connection open showed a 5 s gap between sending and rsyslog's receive time, so the delay was on the collector. The cause was rsyslog's reverse-DNS lookup of each new sender (`imptcp`), which is slow for the development machine's address on this network. Reverse DNS is now a role option, `syslog_collector_dns_lookup`, off by default (`global(net.enableDNS="off")`): the lines already record the sender's address (`from=`), not its name. Afterwards a probe arrived in 31 ms.
2. **rsyslog asks every TLS client for a certificate,** including on the `authMode="anon"` port (6514). On the `x509/certvalid` port (6515), a client without a certificate completes the handshake and is dropped only then ("did not provide a certificate, not permitted to talk to it"). This matters to senders, not to the role: LogaPe now treats a close right after the handshake as a failed send.
3. **Observation, not run down:** twice, a second `sudo -n` in the same SSH command as the `labape` user on `syslog1` failed with "a password is required", while six in a row worked later. If it recurs, look at the VM's sudoers and sssd before blaming the role.

### Audit syslog over TLS: no close_notify, platform certificates not trusted (2026-10-12)

1. **A reset could drop the message.** `notify.send_syslog` closed the raw socket right after sending, without a TLS close. With unread data in its receive buffer (TLS 1.3 session tickets), closing sends a TCP reset, and the collector's pending read then fails ("connection was forcibly closed"; rsyslog: "non-properly terminated"), which can lose the message it had already received. Found while writing the TLS pytest. Fixed: `unwrap()` sends close_notify first.
2. **A platform certificate didn't verify.** The sender trusted only the container's system roots, so a collector with a platform certificate (the Syslog collector service) worked only with verification off. It now also trusts the platform root (`ca.root_pem()`), with TLS 1.2 as the minimum. Live: Settings → Syslog pointed at `syslog1:6514` (TLS, verification on); the settings change and the test message arrived with no TLS errors on the collector.

### KVM host access and Prepare bring-up (2026-10-13)

Design §28.4, live on the throwaway Debian 13 VM `kvmtest1` (nested KVM).

1. **"Refused the password":** LABaPe's VMs allow key sign-in only (`PasswordAuthentication no`). Correct for them, and the error now says it may be the cause. The live script turns password sign-in on for the test only.
2. **The install job never started:** the worker skips jobs on disabled hosts, and a host is prepared before it's enabled. `host.install` now runs regardless.
3. **Jobs marked failed after two minutes:** jobs the worker runs itself didn't update their heartbeat, so `recover_stale` failed the long install ("the worker running this job stopped"). `runner.beat()` now keeps it (and the power job's) alive and reports Cancel.
4. **No metrics on a disabled host:** the collector filtered on `enabled`, which is about deploys. It now watches every host with access.
5. **Installer: missing `secrets.vault.example.yml`, then "Destination not writable":** the image lacked the repo-root example, and `Config.Secrets` runs ansible-vault as the service user, who must own the repository copy. The image now carries the file; the job installs to a stable `/opt/labape-kvm` owned by the service user (a per-job folder also lost the installer's progress and the host's vault between runs).
6. **Installer bug, older than this work:** `Storage.Dirs` created the VM and template folders as root without giving them to the service user, so **Check** reported them not writable on a fresh host. Fixed in the installer; it showed only on a host that didn't have the folders already.

## Redeploy with VMs off (2026-10-13)

1. **lab2's redeploy waited 45 minutes and failed:** "no DHCP address found for l2lin1, l2win1". All three lab2 VMs had been shut off since the host reboot on 2026-10-12 (libvirt autostart isn't set). `tofu apply` only replaced l2lin1's cloud-init seed file, so nothing started the VMs, and DHCP discovery waited for leases that couldn't come. `scripts/deploy.sh` now runs `scripts/lib/start_stopped_vms.py` after the apply: it starts every one of the environment's VMs that is shut off or crashed (domain controllers first) and logs each, before DHCP discovery and Ansible.

## Stack syslog collector (Y1, 2026-10-13)

1. **rsyslog refused `conninfo="host=... dbname=..."`:** "parameter 'conninfo' contains whitespace, which is not permitted" (rsyslog 8.2504). The libpq URI form has no spaces: `conninfo="postgresql:///syslog?host=/run/syslog-db&user=rsyslog_writer"`.
2. **garagepc (Ubuntu 24.04) sent nothing:** "CA certificate could not be accessed ... Permission denied". Ubuntu runs rsyslogd as the `syslog` user and confines it with an enforced AppArmor profile that allows reads only under `/etc/rsyslog.d/`; the certificates were in a root-only `/etc/labape-syslog`. They now go to `/etc/rsyslog.d/labape-tls/` (0750, group `syslog` where it exists; the key 0640). Debian runs rsyslogd as root, so kvm1 had worked.

## Installer services and answer file (Y2a, 2026-10-13)

1. **`Stack.UpdateAgent` failed on a fresh install:** "Permission denied: container/update/agent.json". The folder didn't exist, so Docker created it as root for the API's bind mount on the stack's first start. `container/setup.sh` now makes it as the service user, and the step fixes the owner on installs that already have a root-owned one.
2. **A fresh install's image was stamped `dev`:** `git describe` ran as root on the service user's checkout and git refused it ("dubious ownership"). `repo_version` passes `-c safe.directory=<repo>`.
3. **Noise, not fixed yet:** every audit event sent over TLS makes the collector log "unexpected eof while reading", because the sender opens and closes one TLS connection per event. Nothing is lost. A persistent connection in the sender would remove it (backlog).

## RHEL-family KVM hosts (Y2b, 2026-10-13)

1. **Prepare stopped at "sudo: tar: command not found"** on a minimal Rocky 9 image. Prepare now installs `tar` (dnf or apt) before copying the installer.
2. **Avoided in advance:** `platform.freedesktop_os_release()` is Python 3.10+, and RHEL 9's `python3` is 3.9, so the installer would have failed its OS check there; ansible-core 2.21 also needs Python 3.12+. The installer reads `/etc/os-release` itself, keeps to 3.9 syntax (tested), and builds the Ansible venv with `python3.12` on 9.x.
3. **Lab limit:** registering the Rocky test host hit `LABAPE_MAX_KVM_HOSTS` (4); the disabled `kvmtest1` record (its VM destroyed) was renamed and reused rather than raising the limit.

## Primary file host (Y3a, 2026-10-13)

1. **A deploy on the Rocky host failed after cloning the VM:** `discover_dhcp_ips.py` stopped with "unsupported operand type(s) for |: 'type' and 'NoneType'". Jobs on a remote host run `scripts/` with that host's `python3`, 3.9 on RHEL 9, where `str | None` in a signature needs `from __future__ import annotations`. Added to the two scripts that use it; a test keeps `scripts/` parsing as 3.9 and checks the import.
2. **A new bridge on rkvm1 came up without its port:** NetworkManager had made an automatic "Wired connection 1" on the new NIC, and `nmcli con up <bridge>` doesn't bring the port connection up (autoconnect-slaves is off). The installer's `Net.Bridges` now runs `nmcli con up <bridge>-port` too.

## ARM stack host (Raspberry Pi 5, 2026-10-14)

1. **A destroy on a prepared KVM host failed:** "sudo: a password is required" from `safe-undefine.sh`, which removed the VM's disk with `sudo -n rm`; hosts LABaPe prepares give the service user no sudo rule. The VM folder is the service user's, so a plain `rm` works: tried first now.
2. **Raspberry Pi OS lets only `AllowSSH` members sign in over SSH** (`AllowGroups` in sshd); the user added `localadmin01` by hand. Set up access now joins the first `AllowGroups` group.
3. **The Pi ran Debian 12, not 13, with Docker CE from Docker's repository:** Debian's `docker.io` would have conflicted (an existing `docker compose` is now used as is), and ansible-core 2.21 needs Python 3.12 (2.19 is installed on 3.11).
4. **Uploads on the Pi's stack stayed on the Pi:** no primary file host existed (a fresh database starts at the latest schema, and bootstrap registered garagepc directly), so the Files page used local paths. The worker and bootstrap now make sure there's one.
5. **Firefox: `SSL_ERROR_INTERNAL_ERROR_ALERT` on the Pi's site:** the browser opened it by DNS name (`director.jlm.abq`) and sent that name; Caddy had a certificate only for the address it was installed with, so it answered with an internal_error alert (reproduced with `openssl s_client -servername`). The Pi now uses the name; the procedure is in deploy/README.md.
6. **After the name change, Authentik refused the redirect URI (400):** the blueprint creates the LABaPe provider with `state: created` (so a rotated client secret isn't reset), which also means a new `LABAPE_HOSTNAME` never reaches its redirect URI; and `deploy.sh` only wrote `LABAPE_HOSTNAME` into its `.env` the first time. It now updates both.

## Stack monitoring (M1, 2026-10-14)

1. **Conjur crash-looping on the Pi although the installer had parked it:** Compose *merges* a service's `profiles` with the override's, so `labape-conjur` had both `stores` and `off`, and the `stores` profile (COMPOSE_PROFILES in `.env`) started it on the next `compose up`; its amd64 image restarted forever on arm64. The installer's override now writes `profiles: !override ["off"]`; the stale containers were removed.
2. **First alerts were mostly noise:** exited containers that aren't LABaPe's (other projects on the same Docker) and "certificate expires within 7 days" for the 24-hour web certificates Caddy renews by itself. Only LABaPe's compose projects count now, and certificates alert below a quarter of their lifetime (or 7 days, whichever is less).

## Alert handling (M2, 2026-10-14)

1. **The kept-open syslog connection reconnected on every event over TLS (fine over TCP):** `SSLContext.wrap_socket(raw)` *detaches* `raw` (its file descriptor moves to the TLS socket), so the liveness check's `select([raw], ...)` raised and every check said "closed". Select on, and close, the TLS socket; `raw.close()` is then a no-op. The unit test only used plain TCP, so a TLS variant with a throwaway certificate was added.

## Full-stack backup (B1, 2026-10-14)

1. **The archive check said "empty file" and every file was missing:** `cmd | python3 - args <<'PY'` gives Python the here-document as its program *on stdin*, so the piped archive never reached it (and the unsealer died with a broken pipe). The checker is written to a file in the work folder and run as `python3 check.py`.
2. **`mkdir /data/backups`: permission denied:** the data root is root's and the service user has no passwordless sudo. It is in the `docker` group, so the script makes the folder in a root container with the parent mounted and chowns it to the service user.
3. **On the Pi (installed with `sudo install-labape.py`) the backup stopped at Authentik's `.env`, and Authentik's database was silently left out:** the installer writes that `.env` and its saved answers as root, 0600, so `cp` failed, and `docker compose ps` in Authentik's folder can't interpolate its compose file without the `.env`, so the "is it running" test was false. The script now reads files it can't read through a root container, finds Authentik's PostgreSQL by its compose labels, and a part that's set up but missing (Authentik with no running database) fails the archive's check instead of passing quietly.

## Install from a backup (B2, 2026-10-14)

1. **A restore drill's install stopped at Stack.Up (no /api/health for 3 minutes):** Caddy gets the web certificate from the platform CA over ACME, and the CA checks the http-01 challenge by resolving the web address, which DNS still sends to the real Pi; containers don't read the VM's `/etc/hosts`. A drill (`--restore-drill`) now adds `extra_hosts: [<web address>:host-gateway]` to the CA in the installer's `compose.override.yaml`. A real restore doesn't need it: DNS points at the new machine.
2. **Stack.Stores failed on a fresh OpenBao with HTTP 500, "cannot write to readonly storage":** with raft storage the node needs a moment after the unseal to become active, and `stores.sh` enabled the first mount straight away (the Pi's install was lucky). `unseal()` now waits until `sys/health` answers 200 (active), up to 2 minutes.

## VM snapshots (V1, 2026-10-14)

1. **The snapshot list stayed empty in the web interface ("the VM isn't on its KVM host"):** the API container has no libvirt socket and no SSH key to the hosts; only the worker does. The worker now reads the list back after every snapshot job (and on Refresh) into `vm_snapshots`, and the API serves that.
2. **`virsh snapshot-dumpxml … --no-domain`: "doesn't support option --no-domain"** (libvirt 11.3): dropped; the full XML parses the same, reading only the snapshot's own top-level elements.
3. **Destroying an environment with snapshots failed: "cannot delete inactive domain with 2 snapshots".** `safe_undefine` adds `--snapshots-metadata`.


## Hosts never sleep and one environment over two hosts (P1b, 2026-10-14)

1. **kvm1 stopped answering 15 minutes after a reboot:** Debian 13 with GNOME; the GDM login screen suspends the machine after 15 idle minutes on AC power, VMs and the web stack with it (`sleep.target` active 07:25-08:14). Nothing masked the sleep targets. The installer's `Prereq.NoSleep` and the access-account setup now mask them, and the host page warns while one can still be reached (PR #98). A `systemd-inhibit` as the service user is refused by polkit ("Interactive authentication required"), so only root can fix it.
2. **Prepare on a Debian cloud image (the nested host `nkvm1`) failed at Tools.Ansible, "No matching distribution found for ansible-core":** installing `network-manager` left `/etc/resolv.conf` as "# Generated by NetworkManager" with no nameserver; the management NIC is ifupdown/cloud-init's and NetworkManager doesn't manage it. `Prereq.Packages` now writes `/etc/NetworkManager/conf.d/90-labape-dns.conf` (`dns=none`) before installing NetworkManager on a host that had none, and puts the old `resolv.conf` back if it lost its nameservers.
3. **A spread deploy's second part failed, "duplicate key value violates unique constraint templates_kvm_host_id_name_key":** the reused host record still had template rows (not present) from before; `filedist.ensure_template` only looked for present rows and inserted another. It now reuses the host's row.
4. **The second part then failed with "Cannot get interface MTU on 'br1'":** the reused host record still carried `lab-vlan48` on `br1` (the old host's bridge); Prepare with typed-in networks doesn't change a host's network attachments. Fixed on the record (`hosts.py attach 2 lab-vlan48 br-lab`); a reused record needs its attachments checked.

## Sign-on between linked stacks (F1, 2026-10-14)

1. **The linked stack's Authentik answered the source's authorize with "The request is otherwise malformed"** (its log: "Invalid grant_type for provider: authorization_code"): Authentik 2026.8 providers carry `grant_types`, and one made in code starts with none. `sso.py` copies the LABaPe provider's (`authorization_code`, `refresh_token`).
2. **On the Pi, the agent couldn't run Authentik's compose ("open tools/authentik-test/.env: permission denied")**, and its full-stack backup had failed for the same reason: the installer ran `deploy.sh` as root, which left `.env` root-owned 0600, while the agent runs as the service user. `deploy.sh` now hands `.env` and `trust/` to the checkout's owner when run as root; existing stacks: `chown` it once (admin guide §9).
3. **A test sign-in through the source left a user without its source connection, and the next try failed with a duplicate user name:** the flow executor answers 302 to itself after each stage that needs no input, and the test helper took the first 302 for the end of the flow, leaving before the stage that records the connection. `live/linked_login.py` follows the executor's own redirects. (A browser does this itself.)
4. **kvm1's agent ran an old `sso.py`:** the agent runs the scripts from the stack's checkout, which `deploy-web.sh` had filled before the fix; the provider it made had no grant types. Re-deploying the tree and turning sign-on off and on again on the other side remade it.

## Secret health (S1, 2026-10-14)

1. **Recovering the worker's key on the nested host failed: the access account's SSH was dropped right after the check ("kex_exchange_identification: Connection reset", sshd: "drop connection ... penalty: failed authentication").** OpenSSH 10 (Debian 13) has per-source penalties: a refused key login makes sshd drop every connection from that address for a while, the access account's too, since both come from the stack's address. The check now treats a dropped connection as "can't tell yet" and tries once more after 20 s; recovery uses the hosts the check found refusing (no new probes) and retries the access account's connection after a pause.

## Recovery pairs (A1, 2026-10-14)

1. **The domain's labape-admin2 was created and its password set, but WinRM refused `LAB\labape-admin2`:** the inventory signs in with WinRM Basic authentication, which takes no `DOMAIN\user` form. On a domain controller the plain name is the domain account (as `Administrator` is), and the domain's second admin only signs in on DCs, so it uses the plain name.
2. **A deploy took the lab's worker down for about ten minutes:** a docstring containing a backslash followed by `u` (from `DOMAIN\user`) is an invalid escape in Python source, and the worker crash-looped on import; deploy-web.sh's health check only asks the API, which doesn't import that module at start. Run the tests (at least `ast.parse`) before deploying.

## A password of its own per VM (A2, 2026-10-14)

1. **The tasks after the Administrator change were skipped:** the change sat in a block whose `when` included `labape_admin_password != ansible_password`. A block's `when` is checked again before each of its tasks, and the `set_fact` of `ansible_password` inside it made the condition false for the rest. The decision is now a fact set once, at the start (`playbooks/tasks/labape_admin_password.yml`).
2. **The detached (async) Administrator change failed on l2win1 in every deploy, so the next sign-in with the new password was refused:** the VM's `%USERPROFILE%\.ansible_async` results showed `win_user` failing with `retrieving member "Create": Unknown error (0x80005000)` (it couldn't find Administrator, so it tried to create it), then `Set-LocalUser` with `CouldNotAutoloadMatchingModule`, then ADSI `SetPassword` with 0x80005000 again, although `COMPUTERNAME` and `PSModulePath` were set. The Security log (4724) confirmed that no reset happened. `win_powershell` had reported the `Set-LocalUser` run as changed, not failed: a non-terminating error doesn't fail it without `error_action: stop`. Reproduced outside a deploy with a throwaway account: a detached process that touches the account database a few seconds after it starts fails once the WinRM shell that started it has closed. A read at the very start still works, which is why rotation's detached `win_user` had got through before (it binds in its first second). Changing the password in the same shell works: the VM takes it at once, and only the task's reply is refused (a task failure, not unreachable). The deploy (`playbooks/tasks/labape_admin_password.yml`) and rotation (`vmaccess._set_password`) now do that, ignore the reply, and let the sign-in with the new password decide. If it never works, the deploy goes back to the old password and fails with the change's own error. `tools/helpers/live/win_probe.sh` shows which stored password a VM takes and its recent async results.

## People in OpenBao (V1b, 2026-10-14)

1. **After `LABAPE_OPENBAO_BIND=172.21.20.11`, `stores.sh setup` stopped with "OpenBao isn't answering on https://127.0.0.1:8200", and OpenBao, just recreated, stayed sealed for about two minutes:** publishing the port on one host address replaces the loopback publish, which `stores.sh`, the update agent and the command line use. Bound to `0.0.0.0` instead; `stores.sh` now refuses any other single address for `LABAPE_OPENBAO_BIND` and `LABAPE_STORES_BIND`.
2. **LABaPe's policy allowed `"policies" = ["labape-env-*"]` on groups, yet OpenBao denied every group update that carried `policies`; as a string (`"labape-env-x"`) it passed, and so did `"labape-env-x,labape-agent"`:** OpenBao compares an `allowed_parameters` glob only with a string value, never with each item of a list, and a comma-separated string counts as one string. So the glob can't keep LABaPe to its own policies. The policies and their attachment moved to `stores.sh people-policies` (root token, fixed template, run by the update agent); LABaPe's policy has no policy rights at all.
3. **OpenBao refused the OIDC sign-in: "id token issued by a different provider, expected .../application/o/labape/ got .../application/o/labape-openbao/":** Authentik gives each application its own issuer. `stores.sh` now points OpenBao's discovery at the `labape-openbao` application's issuer.
