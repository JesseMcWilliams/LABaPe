# M10 web interface: design draft

Draft design for the LABaPe web application (Claude_Docs/Design_System-Overview.md
§20, M10). It implements the decisions in
Claude_Docs/Planning_Web-Interface-Options.md (referred to below as "decision N") and
the environment-template model in Claude_Docs/Planning_Environment-Templates.md.
Remaining open questions are in Claude_Docs/Planning_Questions.md (29, 34-38).
Nothing here is built yet.

## 1. Scope

The app becomes the primary way to use LABaPe (decision 1): sign in,
manage environment templates, deploy and destroy environments, build and
refresh VM templates, manage files, and see job progress, all from a
browser or the REST API. The existing scripts stay the engine; the app
never reimplements what `deploy.sh`, `destroy.sh`, `build-template.sh`,
`promote-to-template.sh` and `refresh-template.sh` do.

Out of scope for M10: the certificate authority role (M9), NAT mode (M7),
Hyper-V parity (Claude_Docs/Reference_Backend-Parity.md).

## 2. Components

```
                  browser (React SPA)
                        |  HTTPS (LAN)
                     [ Caddy ]  TLS: self-signed or ACME
                        |
     [ labape ] -------------------------------- [ Authentik ] (existing, or bundled)
       FastAPI API + static SPA                     OIDC
            \------------------------------------ [ LDAP / Active Directory ] (optional)
       job worker(s) -> scripts -> tofu/ansible/packer/virsh
        |            |                 |
   [ PostgreSQL ]  volumes:          libvirt socket / qemu+ssh  -> KVM host(s), max 4
   app data,       /data/<type>,     /dev/kvm (Packer builds)
   tofu state      git repo, logs
```

| Container | Image | Role |
|---|---|---|
| `labape` | built from this repo | API, React SPA (static), job worker, the whole toolchain |
| `postgres` | upstream PostgreSQL | app data, OpenTofu state, (bundled Authentik's database) |
| `caddy` | upstream Caddy | TLS termination: internal CA (self-signed) by default, ACME optional |
| `authentik-server`, `authentik-worker` | upstream Authentik | only with the `bundled-authentik` profile (decision 12) |
| `registry` | upstream distribution | only with the `local-registry` profile (decision 18) |

The worker runs in the same image as the API (same toolchain, same
volumes); it is a second process (or a second container from the same
image) so a long job never blocks the API.

## 3. Repository layout (new)

```
app/                 FastAPI backend (Python 3.12+)
  api/               routers: auth, environments, templates, files, jobs, hosts, settings, admin
  core/              settings, permissions, audit, secrets providers
  engine/            job runner + adapters that call scripts/*.sh
  models/            SQLAlchemy models, Alembic migrations
web/                 React + TypeScript + Vite SPA
container/
  Dockerfile         one image: toolchain + app + built SPA
  compose.yaml       Docker Compose (reference) / podman compose
  quadlet/           systemd Quadlet units (Podman, rootful and rootless)
  caddy/Caddyfile
  authentik/         blueprint for the bundled-Authentik profile
tools/secrets-test/  scripts + doc to deploy throwaway OpenBao and Conjur
```

The existing `scripts/`, `tofu/`, `ansible/`, `packer/`, `iso/` stay as
they are and are copied into the image.

As built in phase 10a, the backend is one Python package,
`app/labape/`: `api/`, `auth/` (provider plugins), `engine/` (runner),
`models.py`, `security.py`, `permissions.py`, `config.py`, `cli.py`,
with tests in `app/tests/`. The test Authentik lives in
`tools/authentik-test/`. Section 19 has the details.

## 4. Data model (PostgreSQL)

| Table | Purpose / key columns |
|---|---|
| `users` | `id`, `provider`, `subject`, `username`, `email`, `display_name`, `last_login` |
| `auth_providers` | `name`, `kind` (oidc, ldap, active_directory, ...), `enabled`, `order`, `config` (JSON, secrets by reference) |
| `breakglass_grants` | one-time credential hash, `expires_at`, `used_at`, `created_by` (host user running the command), `local_only` |
| `groups` | Authentik groups seen at sign-in (`name`), synced per login |
| `user_groups` | membership snapshot from the last sign-in |
| `role_bindings` | group or user -> role (`admin`, `template_editor`, `deployer`, `file_manager`, `viewer`) |
| `api_tokens` | `user_id` or service name, hashed token, scopes, `expires_at`, `last_used` |
| `kvm_hosts` | up to `max_kvm_hosts` (default 4): `name`, `libvirt_uri` (local socket or `qemu+ssh://`), `ssh_credential_ref`, `vm_storage_path`, `template_storage_path`, `bridge`, `concurrency_limit`, `enabled` |
| `storage_locations` | per file type (`isos`, `installers`, `files`) and per host: `path` (default `/data/<type>`) |
| `files` | `type`, `name`, `path`, `size`, `sha256`, `uploaded_by`, `uploaded_at`, `description` |
| `templates` | index of the git repository: `path`, `name`, `latest_commit`, `kind` (environment / sub-assembly) |
| `environments` | `name` (= OpenTofu workspace), `kvm_host_id`, `template_path` + `commit`, `status`, `created_by` |
| `grants` | `object_type` (environment / template / file), `object_id`, `principal_type` (user / group), `principal`, `level` (owner / user) |
| `jobs` | `type`, `params` (JSON), `state` (queued, running, succeeded, failed, cancelled), `kvm_host_id`, `requested_by`, timestamps, `exit_code`, `log_path`, `retry_of` |
| `audit_log` | who, when, action, object, outcome, source IP |
| `settings` | central repository configuration and other instance settings (key -> JSON) |
| `secrets` | built-in store: `name`, encrypted value (AES-GCM, key from container secret); unused when an external provider is configured |

OpenTofu state also moves into PostgreSQL (section 7).

## 5. Permissions

Roles say what kind of thing a user may do; ownership says which objects.
Grants can name a user or an Authentik group (decision 9, 40).

| Action | admin | template_editor | deployer | file_manager | viewer |
|---|---|---|---|---|---|
| Manage hosts, settings, role bindings, secrets provider | yes | | | | |
| Create environment templates (becomes owner) | yes | yes | | | |
| Edit / share / delete a template | yes | owners | | | |
| Deploy from a template | yes | owners + users | owners + users | | |
| Change / destroy an environment | yes | own | own | | |
| See an environment's credentials | yes | own | own | | |
| See environments and job logs | yes | own + shared | own + shared | | shared |
| Upload / delete files | yes | | | yes | |
| Use files (pick an ISO, installer) | yes | yes | yes | yes | yes |
| Build / promote / refresh VM templates | yes | yes | | | |
| Move VM storage | yes | | own | | |
| Create own API tokens | yes | yes | yes | yes | yes |

"Own" = the user (or one of their groups) holds an owner grant. Every
check happens in the API, never only in the UI; every change is written
to `audit_log`.

## 6. Job runner

- **Queue in PostgreSQL** (`jobs` table, workers claim with
  `SELECT ... FOR UPDATE SKIP LOCKED`). No extra broker.
- **Concurrency**: per KVM host (`kvm_hosts.concurrency_limit`, default
  2), plus a global limit; Packer builds count double (they're the
  heaviest; six at once saturated a host's disks in M6).
- **Execution**: each job runs one engine script as a child process in
  its own process group, in a per-job working directory with the
  generated input files (section 7). stdout/stderr go to
  `/var/lib/labape/logs/<job-id>.log` and are streamed to the browser over
  Server-Sent Events (tail from an offset, so reconnects resume).
- **Cancel**: SIGTERM to the process group, SIGKILL after a grace period;
  the job ends `cancelled`, and anything half-created is left for an
  explicit destroy (as the CLI behaves today).
- **Retry**: a new job with the same parameters, linked by `retry_of`.
- **Job types** and the script each calls:

| Job type | Script |
|---|---|
| `environment.deploy` | `scripts/deploy.sh libvirt <env> <profile> --env-file ... [--directory-manifest ...]` |
| `environment.destroy` | `scripts/destroy.sh libvirt <env> <profile> --env-file ...` |
| `template.build` | `scripts/build-template.sh <os-key> <name> [--core]` |
| `template.promote` | `scripts/promote-to-template.sh libvirt <env> <vm> <name>` |
| `template.refresh` | `scripts/refresh-template.sh libvirt <template> <new> --os <os-key>` |
| `vm.move_storage` | new `scripts/move-vm-storage.sh` (section 10) |
| `environment.import` | new `scripts/import-environment.sh` (section 13) |

- **Secrets to scripts**: passed through the environment or 0600 files in
  the job directory, never on a command line, and scrubbed from logs.

## 7. Engine integration

- **Per-job inputs.** The app renders, from the environment template and
  instance settings, the files the scripts already read: an
  `environment.yml` (domain, network, storage paths, ISO paths from the
  file manager, template storage), the `<profile>.tfvars` (host groups),
  and the software and directory manifests. Scripts get them through
  their existing options (`--env-file`, `--directory-manifest`) plus a
  small addition for the tfvars path and software manifest.
- **OpenTofu state in PostgreSQL.** The libvirt backend gains a
  `backend "pg"` block (connection string from the environment), so state
  lives in the app's database with locking, one workspace per environment.
  The CLI can still be used against the same state by setting the same
  connection string. Existing local state (lab1) is migrated with
  `tofu init -migrate-state` during import.
- **Shared rendered files.** `environment.auto.tfvars.json` and the
  `.rendered/<workspace>/` answer files are already per run and per
  workspace; the job directory isolates the rest.
- **Inventory and credentials.** The generated inventory and credentials
  handout are captured after deploy; credentials are stored through the
  secrets layer (section 11), visible to owners and admins only (decision
  9).

## 8. Environment templates

- **Built-in git repository** in the data volume
  (`/var/lib/labape/templates.git`, working copy managed by the app);
  every save is a commit authored as the signed-in user (decision 11).
- **Optional external remote** from the central repository configuration:
  push after each commit when reachable; "pull" on request or on a
  schedule; a diverged history is shown in the UI for an owner/admin to
  resolve, never auto-merged.
- **Export / import** as a git bundle for disconnected sites.
- **Schema** as in Claude_Docs/Planning_Environment-Templates.md §1-2 (host groups,
  software, directory objects, sub-assemblies with auto-prefixed host-group
  names and overrides), validated with a JSON Schema shared by the API and
  the React editor.
- **Deploys pin a commit**, so redeploying an environment uses the same
  template version unless the owner chooses to update.

## 9. File manager

- Types `isos`, `installers`, `files` (extensible), each stored under its
  configured location, default `/data/<type>` (decision 10, 41).
- Chunked, resumable uploads (the tus protocol), no fixed size limit;
  SHA-256 computed on completion; duplicates detected by checksum.
- Admins and file managers upload and delete; everyone can list and use.
- ISOs show which OS key uses them; the app writes `os_iso_paths` from
  this, so the file manager replaces hand-editing ISO paths.
- On remote KVM hosts, files are placed through libvirt storage pools
  (`virsh vol-upload`) rather than a local path (section 14).

## 10. VM storage locations and moves

- Per-host `vm_storage_path` and `template_storage_path`, registered as
  libvirt storage pools (decision 23).
- `vm.move_storage` (offline, first version): shut down, copy the disk
  into the target pool with `virsh vol-create-from`, flatten if the
  template isn't reachable from the target, update the domain XML, start,
  delete the old volume after verification. Live moves (`virsh
  blockcopy`) later (decision 23, question 43).

## 11. Secrets

A provider interface (decision 22):

```
get(path) -> value       put(path, value)      delete(path)
list(prefix) -> [path]   test_connection() -> ok/error
```

- **Built-in** (default): encrypted rows in PostgreSQL.
- **OpenBao / HashiCorp Vault**: KV v2, AppRole or token auth.
- **CyberArk Conjur**: host identity + API key.

Providers are Python entry points (`labape.secrets_providers`), so new
ones are added without touching the core. What's stored: the Ansible
vault password (or the vault's contents), bootstrap credentials,
per-environment credentials, SSH keys for remote KVM hosts.
`tools/secrets-test/` provides a script and doc to stand up throwaway
OpenBao and Conjur containers for testing (question 42).

**Built 2026-10-12** (`tools/secrets-test/README.md`): `deploy.sh` runs OpenBao
2.7 (Raft storage, KV v2 at `labape/`, AppRole role `labape`) and Conjur Open
Source 1.27 (account `labape`, host `labape/app` owning policy
`labape/secrets`, behind an nginx TLS proxy) on ports 8200 and 8443, with
certificates from the platform CA: the VM's own (`/etc/labape/tls`, followed on
renewal) or, on the LABaPe host, one the stack signs with the new
`labape ca-sign` command (renewed by a daily timer). It initializes and
unseals OpenBao (also at boot), keeps every credential in a 0600 `.env`, and
ends with a smoke test as LABaPe would sign in. What a provider must do is in
`Claude_Docs/Reference_Secrets-Backend-Requirements.md`. The providers
themselves are next.

## 12. Authentication

Pluggable providers (decision 24) behind one interface, discovered as
Python entry points (`labape.auth_providers`):

```
kind: redirect (OIDC, SAML) | password (LDAP, AD, break-glass)
begin_login(request) / complete_login(request)   # redirect providers
authenticate(username, password)                  # password providers
-> Identity(subject, username, email, display_name, groups[])
test_connection() -> ok / error
```

Several providers can be enabled at once, in a configured order; the
sign-in page shows a button per redirect provider and one
username/password form that tries the password providers in order.
Groups from every provider feed the same `role_bindings`.

| Provider | Notes |
|---|---|
| `oidc` | Authentik by default (any OIDC provider works): authorization code + PKCE; groups from the token's `groups` claim |
| `ldap` | service-account bind, user search, bind as the user to check the password; group membership by `memberOf` or group search; LDAPS or StartTLS required; several directories allowed |
| `active_directory` | the LDAP provider with AD defaults: `sAMAccountName` or UPN logins, nested groups through `LDAP_MATCHING_RULE_IN_CHAIN` (1.2.840.113556.1.4.1941), domain controller discovery from DNS SRV records, optional Kerberos/SPNEGO later (question 29) |
| `breakglass` | see below |

Day one: Authentik local accounts; MFA optional, enforced in Authentik
when wanted (decisions 13, 14). SAML, upstream OIDC, LDAP/AD and Kerberos
through Authentik also keep working.

**Break-glass** (decision 25). There is no standing local admin password.
When every provider is down:

```
docker exec -it labape labape breakglass enable [--minutes 5] [--local-only]
```

prints a one-time password and sign-in link, valid for the given minutes
(default 5, configurable), usable once; the session it creates is an
admin session limited to 1 hour (configurable) and labelled break-glass
in the UI. Running the command requires control of the host or container
runtime, which is the actual proof of identity. Every invocation and
every action in the session is written to the audit log (and, once
notifications exist, announced). `--local-only` accepts the login only
from the host itself (reach it with an SSH port forward). Companion
commands: `labape auth status` (provider health), `labape auth
disable-provider <name>` / `enable-provider <name>` (to recover from a
broken provider configuration), `labape breakglass revoke`.

**API tokens**: created by users in the UI, shown once, stored hashed,
scoped (read, deploy, admin), expiring.

**Authentik**: existing instance by default (the setup wizard asks for its
URL and the OIDC client id/secret, with instructions for creating the
provider and application). The `bundled-authentik` Compose profile runs
Authentik and applies a blueprint that creates the LABaPe OIDC application
and default groups (`labape-admins`, `labape-developers`, ...)
automatically (decision 12).

## 13. Setup and migration

- **First-run wizard**: hostname (decision 16), TLS mode (self-signed or
  ACME + directory URL), Authentik (existing or bundled), the first KVM
  host (local socket or `qemu+ssh://`), storage locations, central
  repository configuration, secrets provider.
- **Optional import** (decision 15) of a CLI-built environment: reads its
  `environment.yml`, tfvars, inventory and vault entries, migrates its
  OpenTofu state into PostgreSQL, tags its VMs if needed, and registers it
  with an owner. lab1 is the first candidate.

## 14. Deployment

From scratch: `deploy/install-labape.py`, which follows the BlueTrack
installer's model (deploy/README.md). It runs named, resumable steps,
from host prerequisites, bridges and storage through the toolchain, the
vault, the container stack, sign-in and `labape bootstrap` (KVM host and
network catalog), and keeps answers per instance in a reusable answers
file that never holds a secret. The rest of this section describes what
it sets up.

- **One image** (`labape`), built from `container/Dockerfile`: Debian
  base; OpenTofu, Packer + plugins (pre-installed, mirrored per the
  central configuration), Ansible + collections, libvirt clients,
  virt-install, qemu-img, xorriso, the Python app and the built React SPA.
  Toolchain versions pinned per release.
- **Compose** (`container/compose.yaml`), Docker Compose v2 as the
  reference; the same file under `podman compose`. Profiles:
  `bundled-authentik`, `local-registry`.
- **Mounts and devices** for a local KVM host: the libvirt socket,
  `/data` at the same path, `/dev/kvm`; a named volume for
  `/var/lib/labape` (git repo, logs, job directories).
- **Podman**: rootful and rootless (decision 17). Rootless: the user in
  the `libvirt` and `kvm` groups, `--group-add keep-groups`, socket and
  device passed through, SELinux labels (`:z`). Quadlet units in
  `container/quadlet/` (`labape.container`, `labape-postgres.container`,
  `labape-caddy.container`, `.volume` and `.network` units).
- **TLS** by Caddy: its internal CA for self-signed certificates, or ACME
  against an internal or public directory (decision 16).
- **Images**: built locally by default; the central configuration can
  name an external registry to pull from or push to (decisions 18, 19).
- **CI** builds the image and runs the stack under Docker, rootful Podman
  and rootless Podman.

## 15. Remote libvirt and multiple hosts

Up to `max_kvm_hosts` (default 4) per instance (decision 20), local or
over `qemu+ssh://`.

**Environments can span hosts** (decision 26). Each host group gets a
`kvm_host` in the environment template, or `kvm_host: auto` ("best fit"):
the app places the group on the enabled host with enough free memory and
CPU, enough space in the target storage pool, the fewest running jobs,
and preferably the needed VM template or ISO already present (otherwise
it's copied there through libvirt first). Placement is decided at deploy
time and recorded, so later redeploys keep VMs where they are.

Engine consequences:

- One OpenTofu workspace per environment still; the libvirt backend gains
  one provider alias per host in use and places each `vm` module
  instance on its host's provider (the per-host values: URI, bridge,
  storage paths).
- All hosts of one environment must reach the same lab network segment
  (bridged to the same LAN/VLAN), so static addressing, the DC and domain
  join work unchanged. The app checks this at host registration (bridge
  and subnet) and refuses to place an environment across hosts that
  don't share it.
- For remote hosts, the changes in Claude_Docs/Planning_Web-Interface-Options.md
  "Remote libvirt" apply: disks and seed ISOs created through storage
  pools and `vol-upload`, console logs and screenshots through libvirt,
  the IP pre-flight check run from a host over SSH.
- Packer builds for a remote host: question 34.

## 16. Central repository configuration

One settings document (UI page and an exportable file), every external
source in one place (decision 19):

```yaml
container_registry: { url: "", pull_only: true }
template_remote: { url: "", branch: main, push_on_save: true }
opentofu_provider_mirror: ""      # network mirror URL, empty = registry.opentofu.org
packer_plugin_mirror: ""
ansible_collections: { galaxy_url: "", offline_tarballs: "" }
os_package_mirrors: { rocky: "", ubuntu: "", debian: "" }
chocolatey_sources: [ { name: chocolatey, url: "https://community.chocolatey.org/api/v2/" } ]
acme_directory: ""
```

The app renders these into the tools' own configuration (`.tofurc`
`network_mirror`, Packer plugin paths, `ansible.cfg` Galaxy servers,
answer-file mirror settings, Chocolatey sources) for every job.

## 17. Build phases

| Phase | Delivers |
|---|---|
| 10a | Container stack (Docker + Podman, Quadlets), Caddy TLS, OIDC sign-in against an existing Authentik, break-glass command, roles from groups, KVM host registration, job runner running the existing scripts, deploy/destroy of an environment from a tfvars-level form, live logs, audit log, OpenTofu state in PostgreSQL |
| 10b | File manager (chunked uploads, `/data/<type>`), ISO and installer catalogs feeding `os_iso_paths` and the software store, template library view, build/promote/refresh jobs. **Built** as batch C (§21.3) |
| 10c | Environment templates: git-backed repository, editor with sub-assemblies, owners/users, external remote sync, bundle export/import |
| 10d | Secrets providers (built-in, OpenBao/Vault, Conjur) and `tools/secrets-test/`. The built-in store and rotation are built (§24), and so are the test instances (§11), the OpenBao/Vault and Conjur providers (§25.1), per-secret placement with Move (§25.2) and rotation of the stores' own logins (§25.3), documented in `User_Docs/Web-Interface-Admin.md` §8.1. Left for later: the agent's file secrets (question 95) |
| 10e | Remote libvirt, multiple hosts, environments spanning hosts with best-fit placement (section 15). Remote hosts are built (§22.4); spanning and placement aren't |
| 10f | VM storage moves (offline) |
| 10g | Environment import (migration), central repository configuration applied to all tools, bundled-Authentik profile with blueprint |
| 10h | Direct LDAP and Active Directory providers (section 12) |

Each phase ends with the stack deployed on the lab host and its features
exercised end to end, like the M1-M6 work.

## 18. Open questions

None remaining for this design (Claude_Docs/Planning_Questions.md). 34
(Packer builds with a remote app) was answered as built in §22.4: each
KVM host builds its own templates. 29 (Kerberos) was answered: later. 35
(backups), 36 (retention) and 38 (notifications) were answered as built in
batch E (§21.5: daily database backups, retention, job emails).

## 19. Phase 10a as built

Phase 10a was deployed on the lab host under Docker and exercised end to
end. Findings are in Claude_Docs/Testing_Troubleshooting-Log.md, "M10
phase 10a". Where the build differs from, or adds detail to, the
sections above:

**Code and UI**

| Area | As built |
|---|---|
| Backend | `app/labape/` with FastAPI, SQLAlchemy 2 and Authlib. Tables come from `create_all`; Alembic arrives with the first schema change (it did: §23.2, migrations). `pytest app/tests` runs the API, break-glass, permission and claim tests on SQLite. |
| Web UI | React 19, TypeScript and Vite, with **MUI** as the component library (question 9). Pages: sign-in, break-glass, environments (list, create form, detail with deploy/destroy/credentials), jobs (list, detail with live log, cancel, retry), hosts, audit. |

**Jobs and state**

| Area | As built |
|---|---|
| Job claim | A `pg_advisory_xact_lock` serializes claims so per-host `concurrency_limit` holds across workers, plus `FOR UPDATE SKIP LOCKED`. Workers heartbeat every 10 s, and a job whose worker stops for 2 minutes is marked failed. The global limit and Packer's double weight wait for 10b. (Later: 10b shipped without them; each worker runs up to `LABAPE_WORKER_SLOTS` jobs, default 4, and a Packer build counts as one.) |
| Working directory | Per **environment**, not per job: `/var/lib/labape/environments/<name>/engine`, refreshed from the image's engine snapshot before each job. That keeps `.rendered/` answer files and `.terraform` between deploy and destroy. Only one job per environment runs at a time, so environments never share a directory. It's removed after a successful destroy. |
| Inputs | The runner writes `environment.yml` (the instance base from `/etc/labape` plus the host's storage paths) and `tofu/environments/labape-ui.tfvars`. The tfvars holds the host groups, `static_ip_offset_start`, the host's bridge and `libvirt_uri`; a `-var-file` beats the vault's `TF_VAR_libvirt_uri`. It also copies the software and directory manifests and the vault, and adds `labape_backend_override.tf` (`backend "pg"`, with `PG_CONN_STR` from the environment). |
| Script changes | `destroy.sh` gained `--yes`, `--delete-workspace` and a `tofu init` when `.terraform` is missing. `deploy.sh` and `destroy.sh` honor `LABAPE_SSH_PRIVATE_KEY_PATH`. `safe-undefine.sh` skips sudo when it's root. `create-from-template.sh` raises a disk smaller than its template to the template's size. |
| Outputs | `hosts.generated` is stored on the environment and shown to anyone who can see it. The full inventory and `credentials.generated` go to the encrypted `secrets` table and are deleted from disk. |

**Security and packaging**

| Area | As built |
|---|---|
| Break-glass host-only | Section 12 assumed a loopback client IP. Behind the runtime's port publishing, a host-local browser arrives from the bridge gateway instead. So Caddy runs a second listener published on `127.0.0.1:8443` that sets `X-LABaPe-Local: 1`, and the public listener strips that header. The API also refuses cross-origin writes, except from that listener. |
| Image | Debian trixie with OpenTofu 1.12.6 and Packer 1.16.1 (checksum-verified), and ansible-core 2.21.4 plus pywinrm and the four collections. The venv uses `--system-site-packages` for virt-install. OpenTofu providers are mirrored into `/opt/labape/tofu-mirror` (`TF_CLI_CONFIG_FILE`), and Packer plugins are pre-installed. About 950 MB. |
| Stack | `container/compose.yaml` (postgres, labape-api, labape-worker, caddy; `init: true`), `container/setup.sh` (`.env` and generated secrets), and Quadlet units in `container/quadlet/`. The containers run as root in rootful Docker. Rootless Podman and the Quadlets are written but not yet exercised on the lab host. (Later: rootful Podman with `podman compose` was verified by the installer on a fresh VM, deploy/README.md § Status; rootless Podman and the Quadlets still aren't.) |
| Hosts | Only local `qemu:///system` hosts are accepted; `qemu+ssh` is refused until 10e. (Later: remote `qemu+ssh` hosts were built in §22.4.) |

## 20. Networks and IP address management (built 2026-10-10)

Before this, the engine had one network per environment:
- `network_cidr` and the gateway came from `environment.yml`;
- one `bridge_device` served every VM;
- static addresses were `static_ip_offset_start` + n;
- DNS was hard-wired to the gateway;
- DHCP was accepted by the vm module but never discovered.

Networks are now configured at three levels, in both the CLI and the web
UI (questions 46-50 in Claude_Docs/Planning_Questions.md). Each level is
checked against the one above it, and the engine checks again after the
plan.

### 20.1 Central network catalog (what is allowed)

In the web UI this is the **Networks** page (admin only, `networks`
table). For the CLI it's the `networks:` map in `environment.yml`
(`tofu/environment.example.yml`). Nothing outside the catalog can be used.

| Field | Meaning |
|---|---|
| `name` | e.g. `lab-vlan48`, referenced by hosts and host groups |
| `cidr`, `gateway`, `dns_servers` | Addressing handed to guests at install time. Gateway defaults to the network's .1, and DNS to the gateway. Domain-joined hosts then use **only the environment's domain controllers** (all of them) as DNS; see 20.5. |
| `vlan`, `description` | Informational (web UI) |
| `addressing` | Allowed modes: `static`, `dhcp`, or both |
| `static_pools` | Ranges LABaPe may assign. Required when static is allowed (web UI). Must not overlap the DHCP scope, the reserved ranges or the gateway. |
| `dhcp_ranges` | The DHCP server's scope, never assigned statically, and the range the DHCP discovery sweeps |
| `reserved` | Never assigned (infrastructure) |
| `allowed_roles` / `allowed_groups` | Who may deploy onto it (question 48). With both empty, anyone who can deploy may. Web UI only. |
| `enabled` | Disabled networks keep existing environments but refuse new ones (web UI) |

Validation: ranges lie inside `cidr`, pools overlap nothing reserved,
and no two networks overlap.
- Web UI: `app/labape/netpolicy.py`.
- CLI: `scripts/lib/labape_networks.py`.

### 20.2 KVM host network attachments (what each host provides)

Web UI: **Hosts → Networks** (`host_networks` table). The runner turns
a host's attachments into the `networks:` catalog it writes into that
job's `environment.yml`.

| Field | Meaning |
|---|---|
| `network` | Catalog name |
| `bridge` | The host bridge for it, e.g. `br1` |
| `static_pool` (optional) | A slice of the network's pools for this host, so hosts sharing a VLAN never collide. It must lie inside a pool. Empty means the whole pool, shared through the allocation table. |
| `is_default` | What a host group gets when it doesn't name a network |

`kvm_hosts.bridge` stays as the fallback for a host with no attachments.
For the CLI, each catalog network names its own `bridge`.

### 20.3 Host groups (what a deployment uses)

Each host group can set:
- `network`: a catalog name; default is the host's default network
  (web UI) or `default_network` (CLI);
- `addressing`: `static` (default) or `dhcp`;
- `addresses`: optional explicit addresses, one per instance. The web UI
  fills these from its allocations.

In the CLI, static VMs without explicit addresses use the profile's
`static_ip_offset_start`, or `static_ip_offsets = { <network> = N }` per
network. The offset is counted per network, so an existing
single-network environment's addresses don't change.

The web UI's deploy form only offers networks that are attached to the
host, enabled and allowed for the user, and only the modes that network
allows. Domain controllers are always static.

### 20.4 Address allocation (web UI)

The `ip_allocations` table holds network, address, environment and VM
name.
- **Filled** when a deploy is queued, in the same transaction, with the
  network row locked so concurrent deploys can't collide.
- **Reused** on redeploy; **released** by a successful destroy.
- **On failure:** if a pool runs out, the create is refused and leaves
  nothing behind.

This replaces the hand-typed `static_ip_offset_start`; older environments
that have one still render it.

### 20.5 Engine checks and DHCP discovery (CLI and web UI)

- `scripts/lib/check_ip_policy.py` runs on the plan, before
  `check-network.sh` and before anything is created. It checks that:
  - each network is in the catalog and allows the mode used;
  - static addresses are in a pool and outside the DHCP scope, reserved
    ranges and the gateway;
  - domain controllers are static;
  - no address is used twice.

  New VMs that break a rule are refused. Existing ones only get a warning,
  so tightening the catalog never blocks redeploying an existing
  environment.
- Every VM gets a deterministic MAC (`52:54:00` plus a hash of workspace
  and name). ISO installs now get it too; it's passed to virt-install
  and isn't a reinstall trigger.
- `scripts/lib/discover_dhcp_ips.py` runs between `tofu output` and
  `generate-inventory.py`. For each DHCP VM:
  1. it asks `virsh domifaddr --source agent`, then `--source arp`, which
     is the KVM host's ARP table read through libvirt, so it also works
     from the web UI's worker container;
  2. between looks, it ping-sweeps the network's `dhcp_ranges` so the
     host learns its neighbors;
  3. it confirms an ARP answer with a ping and a second look before
     using it;
  4. it waits up to 45 minutes, to allow for ISO installs.
- DNS comes from `addressing.dns`. With one DNS server equal to the
  gateway, every answer file renders byte-for-byte as before, so
  existing VMs' reinstall triggers don't change. lab1 still plans no
  infrastructure changes. Windows ISO answer files set the first DNS
  server only; Windows clones set all of them.
- The older single `network:` block in `environment.yml` still works: it
  reads as a one-network catalog named `default`, static only, on the
  profile's `bridge_device`.
- `refresh-template.sh` gained `--network` and `--addressing`.
- **Domain members use only the domain controllers for DNS**, every DC
  in the environment, and DNS from a DHCP lease is ignored. The join
  roles enforce it per platform:

  | Platform | How |
  |---|---|
  | Windows | A static DNS list, which overrides DHCP |
  | NetworkManager (Rocky) | `ipv4.ignore-auto-dns` |
  | systemd-resolved/networkd (Ubuntu) | Global `DNS=` with `Domains=~.`, plus a per-link drop-in: `DNS=` reset, `UseDNS=no` |
  | Debian on ifupdown | `/etc/resolv.conf`, plus dhclient and dhcpcd hooks so lease renewals don't rewrite it |

- **Refresh addresses:** `scripts/refresh-addresses.sh` (and the
  environment page's **Refresh addresses** button, job type
  `environment.refresh_addresses`) re-finds DHCP VMs' leases and
  regenerates the inventory without touching any VM. A failed refresh
  leaves the environment's status alone.
- **Several DNS servers:** Windows ISO answer files take every server
  (`netsh ... add dns` for the second onward). With one server they
  render exactly as before.
- **Discovery confirmation:** an ARP answer is confirmed by probing the
  address and finding the same MAC mapping afterwards, not by a ping
  reply (Windows' firewall drops ICMP). The CLI reads the host's
  `/proc/net/arp` directly; the worker container goes through libvirt.

**Packer builds stay on QEMU's user-mode network (decided 2026-10-10).**
- Templates never need the LAN: each clone gets its network from its
  host group at deploy time.
- Packer's `net_bridge` finds the VM only by passively reading the host's
  ARP table, which a DHCP guest that never talks to the host doesn't
  populate.
- It also needs root-side `qemu-bridge-helper` and `/etc/qemu/bridge.conf`
  setup.

The one real defect, the build-time resolver (10.0.2.3) left in Linux
templates' `/etc/resolv.conf`, is now cleared by `template_finalize`.
Existing templates lose it at their next refresh.

Moving lab1 off 172.21.20.0/22 (question 49): its replacement, `lab2`,
runs on `lab-vlan48` and was built through the web UI from templates.
- `l2dc1` is a static DC (172.21.50.1).
- `l2lin1` (Rocky) and `l2win1` (Windows Server 2022) are DHCP members,
  joined to `labape.test`, each using only the DC for DNS.
- Refresh addresses was verified on it.

lab1 was destroyed on 2026-10-10, at the owner's request, after `lab2` was verified.

### 20.6 Host prerequisites (done on the lab host)

1. **Bridged traffic past Docker** (Claude_Docs/Testing_Troubleshooting-Log.md,
   "Installing Docker cut bridged VMs off from the LAN"). As root:
   ```bash
   printf '%s\n' 'net.bridge.bridge-nf-call-iptables = 0' 'net.bridge.bridge-nf-call-ip6tables = 0' \
     'net.bridge.bridge-nf-call-arptables = 0' > /etc/sysctl.d/90-labape-bridge-nf.conf
   echo br_netfilter > /etc/modules-load.d/labape-br_netfilter.conf
   modprobe br_netfilter && sysctl --system
   ```
2. **A bridge per VM network.** For the lab VLAN on `enp14s0f1`
   (172.21.48.0/22, gateway and DNS 172.21.48.1): a DHCP scope of
   .48.16-.49.254 and LABaPe's static pool .50.1-.51.254. The host keeps
   a DHCP address on `br1` (which DHCP discovery needs) but never takes
   its default route there. As root:
   ```bash
   nmcli con add type bridge ifname br1 con-name br1 bridge.stp no \
     ipv4.method auto ipv4.never-default yes ipv6.method ignore
   nmcli con add type bridge-slave ifname enp14s0f1 master br1 con-name br1-port
   nmcli con mod "Wired connection 4" connection.autoconnect no
   nmcli con down "Wired connection 4"; nmcli con up br1
   ```

## 21. Web UI usability and admin features (2026-10-11)

These come from a round of UI feedback (questions 51-61). All six batches
are built and merged (PRs #16-#21). They were built in this order:
- **A:** UI fixes;
- **B:** KVM host resources and load;
- **C:** Files tab (phase 10b);
- **D:** services, applications and packages;
- **E:** admin settings;
- **F:** Authentik group lookup.

### 21.1 Batch A, built

**Environment form and engine**

| Area | As built |
|---|---|
| VM names | Optional names per host (`host_groups[].names`); blanks become `<group><n>` (`app/labape/naming.py`, mirrored in `tofu/backends/libvirt`). Names are checked unique **across the KVM host**, against every other environment's VMs there, when the environment is created. Windows' 15-character limit applies. |
| Template metadata | `<name>.json` beside each `<name>.qcow2` holds `os_key`, `edition`, `min_cpu`, `min_memory_mb`, `description` and `notes` (quirks and after-tasks). The worker syncs the library into the `templates` table every minute. For a template without metadata, it infers the OS and edition from the name and writes the file. The disk floor comes from the image's virtual size. Edits made in the UI are written back to the file. |
| Deploy form | Templates appear in a dropdown filtered by KVM host and OS, showing description and notes. Amber warnings appear when vCPUs or memory are below the template's minimums, or the disk is smaller than the template, which raises it. The image source reads "Build from template" / "Build from ISO". Each host gets a name box. |

**Errors and admin views**

| Area | As built |
|---|---|
| Errors | Refusals come back as `{"detail", "errors": [{field, label, message}]}`, where each message states the rule (`app/labape/errors.py`). Network policy errors name their field. The UI checks the same rules before sending, and every field reserves its helper line so rows stay aligned. Errors show in red with an icon; size warnings show in amber. |
| Error log | Every refused request (400, 403, 409, 422), and every submit the browser blocked (`POST /api/errors/report`), goes to `user_errors`. The admin **Errors** page lists them. Retention is configurable (default 90 days) and purged daily by the worker. |
| Audit | Filters by actor and action prefix. **Export JSON** downloads the filtered log, and the export itself is audited. CSV export is planned. |

**User menu and credentials**

| Area | As built |
|---|---|
| User menu | Clicking your name shows roles, groups, sign-in provider, this and the previous sign-in (from the audit log), session end and account age. **Reload rights** does a silent OIDC re-sign-in (`prompt=none`) that re-reads the user's groups. If Authentik needs the user, the app says so instead of failing. |
| Credentials | Returned as sections of label/value items; values whose label mentions a password are secret. Each secret is masked, with **Show**, **Show large** and **Copy**, and every reveal and copy is audited. |

**Networks page**

| Area | As built |
|---|---|
| Fields | "VLAN ID (reference only)" (question 56: a label; LABaPe doesn't tag traffic) and "Or limit to groups". List fields parse on save, so typing a comma works. |

### 21.2 Batch B, built: KVM host resources and load

- **Collection:** the worker collects each enabled host's numbers every
  minute through libvirt into `host_stats`:
  - `nodeinfo`, `nodememstats`, and `dominfo` for every defined VM;
  - free space on the VM and template storage paths;
  - `/proc/loadavg`.

  The API reads that table; it has no libvirt access of its own.
- **API:**
  - `GET /api/hosts` includes each host's resources.
  - `GET /api/hosts/{id}/resources?vcpus=&memory_mb=&disk_gb=` adds a
    proposed demand, which is how the deploy form projects a new
    environment onto the host.
- **Thresholds** (question 57, `engine/hoststats.py`):
  - warn above 4 vCPUs per *logical* CPU allocated;
  - warn above 90% of RAM committed;
  - warn below 15% free on the VM storage;
  - **refuse** a new environment that would commit more than 100% of
    RAM, a field error on the KVM host.

  Committed RAM is every defined VM's maximum memory, running or not,
  since a stopped VM can be started. The new environment's disk is
  counted at its full size, though clones start small. The block is
  skipped when the worker hasn't reported for 10 minutes.
- **UI:**
  - The deploy form shows the selected host's bars (vCPUs, committed
    RAM, RAM in use, VM storage) with this environment added, plus any
    warnings, and disables **Create** when the RAM block applies.
  - The Hosts page shows every host's load.
- **Verified:** live on the lab host. A 10 × 32 GB environment was refused
  at 269% committed.

### 21.3 Batch C, built: Files tab (phase 10b brought forward)

**Files**

| Area | As built |
|---|---|
| Locations | `isos`, `installers` and `files`, each under its configured root (`files.roots` setting, default `/data/<category>`; admin `PUT /api/files/roots`, or `labape bootstrap`). The installer sets the ISO root to its `IsoPath`. The API container now mounts the data root so it can write uploads. |
| Uploads | tus 1.0, creation and termination (`/api/files/tus`); the browser uses `tus-js-client` with 32 MB chunks, retries, and resume after a dropped connection. When the last chunk arrives, the SHA-256 is computed and the file moves from `<root>/.uploads/` into place. A file with the same content as an existing one is refused as a duplicate, and a taken name is refused before the upload starts. Admins and file managers upload, edit and delete; everyone signed in can list. |
| Scan | The worker registers files found in the locations every minute (copied in by hand, or there before the app) and marks vanished ones. A background thread checksums unhashed files, so job claiming never waits on a multi-GB ISO. |
| ISOs | The OS key is inferred from the name (the installer's patterns) and editable; one ISO per OS can be the **default**. `environment.yml`'s `os_iso_paths` is written from these for every job (the default ISO, else the newest), replacing hand-edited ISO paths. |

**Templates**

| Area | As built |
|---|---|
| Templates tab | Lists every host's library with OS, edition, disk and minimums. **Edit** changes metadata (written back to `<name>.json`). **Refresh** clones the template, applies every update and saves it under a new name. **Build a template** runs Packer from the OS's ISO. Environment pages get **Promote to template** per VM (not for domain controllers). |
| Template jobs | `template.build`, `template.refresh` and `template.promote` run `build-template.sh`, `refresh-template.sh` and `promote-to-template.sh` in a per-job engine copy (`/var/lib/labape/jobs/<id>`, removed afterwards). Promote writes the environment's stored inventory into it first. Names are checked against existing templates and running jobs. Packer runs the build VM inside the worker (image now has `qemu-system-x86` and `ovmf`, on `/dev/kvm`). |
| Script changes | The build, promote and refresh scripts write the template's metadata file (`scripts/lib/write_template_meta.py`). A refresh keeps the source's description and notes and appends a dated note. A failed metadata write only warns. `build-template.sh` honors `LABAPE_SSH_PRIVATE_KEY_PATH`. `promote-to-template.sh` gained `--os` and `--from-template`. |

### 21.4 Batch D, built: services, applications and packages

| Area | As built |
|---|---|
| Catalog | One `catalog_items` table holds `service`, `application` and `package` items, each draft, published or retired (`app/labape/catalog.py`). **Applications** take the `ansible/package_catalog.yml` entry shape (Chocolatey/apt/dnf/zypper names, and/or `windows` / `linux` installers from a URL or a file in the installers location). **Services** are `windows: {features, services}` and/or `linux: {packages: {dnf, apt}, units}`. **Packages** list items and may include packages one level deep (question 53). Validation states the rule, e.g. an MSI needs its product code. |
| Who does what | Everyone browses published items and can **request** a new one (Catalog → Requests). Admins create, edit, publish and retire items, and answer requests (questions 51, 52). A package can be published only when everything in it is published. |
| Import and export | Admins export the chosen items, or everything, as `labape_catalog: 1` YAML; a package's dependencies are included. They import such a file, as drafts or published, skipping or overwriting existing names. |
| Seed | First start publishes a starter set: IIS, DNS server, DHCP server and file server on Windows; nginx, Podman and Cockpit on Linux; plus the usable entries of the repository's `package_catalog.yml` (Firefox, VS Code, 7-Zip, Notepad++). |
| Deploy form | Each host group picks services, applications and packages, filtered to items with something for its OS family. The API refuses unpublished items and items with nothing for the group's OS. |
| Engine | The runner writes each VM's choices to inventory `host_vars` (`labape_services`, `labape_extra_packages`, packages expanded), merges every published application into the job's `package_catalog.yml`, and sets `software_store_path` to the installers location. `linux_common` and `windows_common` add the extra applications to the host's list. The new `labape_services` role installs Windows features (rebooting if needed) and starts services, or installs Linux packages and enables units. The CLI path is unchanged: it sets none of these variables. |
| Verified | Live on the lab host: a Rocky clone (from the `rocky9-webui-2026.10` template built in batch C) with nginx, Cockpit and 7-Zip. Both units were enabled and active, nginx answered 200, and 7-Zip installed as EPEL's `7zip-standalone`, which provides `p7zip`. |

### 21.5 Batch E, built: admin settings

| Area | As built |
|---|---|
| Storage | Each section is one `settings` row (`app.backup`, `app.retention`, `app.syslog`, `app.smtp`), validated by pydantic models in `app/labape/appsettings.py`. The SMTP password is kept in the encrypted secrets store (`settings/smtp_password`); the API reports only `has_password`, and the audit entry records only that it changed. |
| Settings page | Admin only (nav → Settings): file locations (the existing `PUT /api/files/roots`), backups, history, syslog and email. Each section saves on its own and shows field errors. Syslog and email each have a test button. |
| Backups | `system.backup` jobs, run by the worker without a KVM host: `pg_dump --format=custom` (the password goes in `PGPASSWORD`, not the command line) to `labape-YYYYmmdd-HHMMSS.dump` in the backup directory (default `/var/lib/labape/backups`, on the shared `labape-data` volume). After each run, dumps older than the retention are removed. The worker queues one backup a day once the configured time (UTC) has passed. If none has run since that time, it queues one at startup. "Back up now", the list and downloads are on the page. The image now includes `postgresql-client` (17 on trixie, matching the postgres:17 server). Restore with `pg_restore`. |
| History | Once a day the worker deletes finished jobs' log files older than `job_logs_days`, audit rows older than `audit_days` (0 keeps them forever, the default) and user errors older than `errors_days`. The Errors page's retention box edits the same value. |
| Syslog | When enabled, every audit event is sent after its row is committed: RFC 5424, with the event as a JSON message body. The severity follows the outcome (ok → info, denied → warning, failed → error). UDP, TCP or TLS (RFC 6587 octet framing for TCP and TLS; certificate verification can be turned off). Sending is queued on a background thread, so a slow or unreachable server never delays a request. The HOSTNAME field is the public URL's host name. |
| Email | SMTP with none, STARTTLS or SSL, and an optional sign-in. With "Email people when their jobs finish" on, the requester gets a short message with the job's state and a link, sent to the address from their last sign-in. |
| Verified | Live on the lab host: the settings round trip; audit events and the test message arrived at a UDP listener; a backup job wrote a 75 KB `PGDMP` dump, which downloaded; the scheduler had already queued that day's backup at startup; an unreachable SMTP server gives a clear field error. pytest (42) covers validation, secret handling, syslog over UDP, backup, scheduling, purges and job emails on SQLite. |

### 21.6 Batch F, built: Authentik group lookup

| Area | As built |
|---|---|
| Settings | A **Directory** section: "Search Authentik", the Authentik URL (blank uses the OIDC issuer's origin), certificate verification, and an API token. The token is kept in the secrets store (`settings/directory_token`), and the API reports only `has_token` (question 55). Test search calls Authentik and reports the groups it can see, or why it failed (for example, HTTP 403: the token is invalid or can't view groups). |
| Search | `GET /api/directory/groups?q=` (admin) calls Authentik's `/api/v3/core/groups/?search=` with the token, 20 results, 5 s timeout. If the directory is off, has no token or fails, it searches the group names LABaPe already knows: users' groups from their last sign-in plus group role bindings. It says when it fell back. |
| UI | `GroupPicker`, a type-ahead multi-select with chips that also accepts free text (Enter adds a typed name). Its helper line says where suggestions come from. Used for the network's "Or limit to groups"; other group fields can reuse it. |
| Test Authentik | The blueprint adds the `labape-directory-reader` role (only `authentik_core.view_group`), the `labape-directory` service account and its non-expiring API token. The token's key is `LABAPE_TEST_DIRECTORY_TOKEN`, which `deploy.sh` generates into `.env`, including for an existing `.env`. |
| Verified | Live: the token listed the 8 groups and was refused a write (403). After saving it in Settings, the search for "deploy" returned `labape-deployers` from Authentik. Test search reported the groups. A wrong token gave a clear field error. A deployer is denied the search. pytest covers the fallback, secret handling, the Authentik call and the failure path. |

## 22. Dashboard, list pages, installer profiles, platform CA, downloads (built 2026-10-11)

These come from the next round of feedback. The decisions are questions
62-73 in `Claude_Docs/Planning_Questions.md`, all answered 2026-10-11.
The batches are built in the order below.

### 22.1 Batch G: dashboard (built)

- A **Dashboard** page becomes the landing page (`/`). Clicking the app
  name in the top left always opens it.
- **Everyone** sees only what they may access:
  - the KVM hosts they can deploy to: CPU, RAM, free disk and disk activity;
  - their environments: host count, online/offline per VM, and per-VM CPU, RAM, free disk and disk activity.
- **Admins** also see:
  - every KVM host and every environment;
  - the sign-in methods and their state: OIDC discovery and JWKS reachable, last successful sign-in, break-glass on or off with its expiry, the directory token;
  - the secrets back ends and their health: today the built-in store (an encrypt/decrypt self-test) and the engine's Ansible vault;
  - the SMTP and syslog configuration and their last result;
  - the installed version, and whether a newer release exists.
- The engine needs:
  - per-VM samples each minute from `virsh domstats` (state, CPU time, memory, block I/O, allocation);
  - host disk I/O from `/proc/diskstats` deltas;
  - guest free space and a "responding" check through qemu-guest-agent, which every image gets (question 62);
  - samples kept 24 hours, enough for a sparkline;
  - a daily release check (question 64).

As built:

| Area | As built |
|---|---|
| Sampling | Each minute the worker makes one `virsh domstats --raw` call for all domains, and runs `virsh guestinfo --filesystem` for each running VM (in parallel, 6 s timeout). It also reads `/proc/stat` and `/proc/diskstats` (the device holding the VM storage). Rates come from the difference against the previous sample, whose counters are kept in `host_stats.stats._counters`: CPU % per VM is CPU time over elapsed time and vCPUs. `metric_samples` keeps one row a minute for each host and VM, purged after 24 hours. |
| VM fields | State (running or blocked counts as online), responding (the guest agent answered; unknown when the VM has no agent channel), CPU %, memory used (balloon driver stats; unknown on Windows), free space from inside the guest (each device counted once), disk file allocation and capacity, and disk read/write rates. |
| Visibility | Environments: the existing grants (admins see all), excluding new and destroyed ones. KVM hosts: admins see all, deployers see the enabled hosts, and others see only the hosts of their environments. VMs are matched to environments by `instance_names`. |
| Admin health | `/api/dashboard/admin`, cached 30 s to 10 min per check: each sign-in provider's `test_connection` and last sign-in, break-glass tokens, the directory token, the built-in secret store (encrypt/decrypt self-test plus decrypting one stored secret), the engine's Ansible vault (`ansible-vault view`, output discarded), SMTP (connect, EHLO, STARTTLS), syslog (TCP/TLS connect; for UDP, the API process's last send), and the last job email (`status.email`). |
| Version | The image is built with `LABAPE_VERSION` = `git describe --tags --always --dirty` (`deploy/install-labape.py`, compose build arg). The worker asks GitHub's latest-release API for `JesseMcWilliams/LABaPe` daily (Settings section `updates`: on/off and repo), and admins can check now. `CHANGELOG.md` starts at v0.2.0. |
| Guest agent | Every `virt-install` (the four ISO paths and template clones) adds the `org.qemu.guest_agent.0` channel. On KVM guests (`ansible_system_vendor == 'QEMU'`), `linux_common` installs and starts `qemu-guest-agent`. `windows_common` downloads the virtio-win guest tools 0.1.302 (pinned SHA-256) and installs them unless the `QEMU-GA` service exists. VMs created before this change have no channel: they show "unknown" and their disk file allocation instead. |
| UI | The landing page `/` (the app name in the top bar links to it), refreshed every minute. KVM host cards have meters and 24-hour sparklines, built from ten-minute buckets. The environments table expands to each VM. The admin panel shows the version banner (with Check now), sign-in, secrets and notifications. |
| Verified | Live on the lab host: host CPU, RAM, free space and disk activity. Test environment `agent1` (Rocky 9, Debian 13 and Windows Server 2022 clones) had the agent channel on every VM. All three reported responding, with guest free space (Windows: 40.8 of 60 GB free), CPU and disk rates. lab2's older VMs show unknown with allocation. The health panel showed OIDC, break-glass, the directory token, the built-in store and the vault all ok. A developer saw the host and lab2, and a viewer saw nothing and was refused the admin panel. pytest: 47. |

### 22.2 Batch H: list pages and branding (built)

- Hosts, Networks, and the other admin lists (catalog items, templates)
  follow the Environments pattern:
  - a list of only the entries the user may see;
  - an **Add** button at the top right;
  - the name links to the entry's own page (`/hosts/:id`, `/networks/:id`, ...), where editing happens.
  - Non-admins see Hosts and Networks read-only, limited to what they may use (question 67).
- The app name ("LABaPe") becomes a setting (Settings → Branding). It is
  used in the top bar, the browser title and emails. A logo comes later (question 68).

As built:

| Area | As built |
|---|---|
| Pattern | `components/PageParts.tsx`: `ListHeader` (title, intro, Add at the top right for those who may add), `DetailHeader` (back link, title, actions such as Delete, Publish, Retire) and `Facts` (read-only rows). Lists link each name to its page, and the edit dialogs are gone. |
| Pages | Hosts → `/hosts/:id` (settings, the networks it carries, load; `/hosts/new` registers). Networks → `/networks/:id` (`/networks/new`). Catalog items → `/catalog/:id` (`/catalog/new?kind=`). Files → `/files/:id`. Templates → `/templates/:id`. The Catalog and Files tabs live in the URL (`?tab=`), so Back returns to the same tab. |
| Who sees what | Hosts and Networks are in everyone's top bar. `permissions.visible_hosts`: admins see all hosts, deployers and template editors the enabled ones, and others only the hosts of their environments. The dashboard uses the same rule. Networks keep the existing may-use rule. Non-admins get the read-only `Facts` view, plus the networks they may use on a host. New `GET` endpoints for one host, network, catalog item, template and file apply the same rules; they're declared after the literal routes (`/export`, `/requests`, `/roots`). |
| Branding | Settings section `branding` (`app_name`, 1-40 characters, no `<` or `>`). Public `GET /api/branding` so the sign-in page can show it. Used in the top bar (which links to the dashboard), the browser title, the sign-in heading, and the job and test email subjects. Help text that means the software itself still says LABaPe. |
| Verified | Live on the lab host: the detail endpoints, admin, developer and viewer visibility (the viewer sees no hosts and gets 404 on host 1), a developer refused an edit, the branding round trip read publicly, and every new SPA route served. pytest: 50. |

### 22.3 Batch I: downloads from a URL, hash verification (built)

- On the Files page, a file can come from a URL instead of an upload. The
  worker downloads it as a job (progress in the job log, resumed with
  HTTP Range) into the chosen category.
- HTTPS only, unless a global setting allows HTTP. Admins and file
  managers can start downloads (question 71).
- LABaPe's own file hashes are always SHA-256. To verify against a
  vendor's published value, uploads and downloads can carry an expected
  hash in SHA-256, SHA-1, SHA-512 or MD5. A mismatch rejects and deletes
  the file (question 72).

As built:

| Area | As built |
|---|---|
| Downloads | `POST /api/files/download` (admins and file managers) checks the scheme, the name (from the URL when left empty), the expected hash, and that the target and any queued download of it are free. It then queues a `files.download` job, which the worker runs itself (`engine/downloads.py`), like backups. The worker follows redirects itself, checking each hop: the scheme, link-local literals, and (without a proxy) DNS answers in 169.254.0.0/16 or fe80::/10. It streams to `<location>/.downloads/<sha1(url)>-<name>.part` with HTTP Range resume, so a retried job continues the same file. Progress goes to the job log every 10 s, together with the heartbeat and a cancel check (cancel keeps the partial file). |
| Hashes | The expected value may be bare hex, a `SHA256SUMS` line or a BSD `SHA1 (name) = ...` line, in SHA-256, SHA-1, SHA-512 or MD5. LABaPe always computes and stores SHA-256 (the duplicate check uses it); the vendor's type is computed as well when it differs. On a mismatch, or when the content duplicates a stored file, the download is deleted and the job fails, saying why. Uploads take the same fields in tus metadata and are checked when the last chunk arrives (422 on a mismatch). |
| Settings | Section `downloads`: `allow_http` (off) and `proxy` (HTTP(S) proxy URL; with a proxy, DNS is the proxy's). |
| UI | Files tabs: "Download from URL" (URL, name, hash type, expected hash, description; opens the job), and "Verify against a hash…" before an upload. Settings → Downloads. |
| Verified | Live: plain HTTP and the metadata address refused. virtio-win guest tools 0.1.302 (31.6 MB) downloaded over HTTPS, checked against its SHA-256 (pasted as a checksum-file line), stored with the requester as uploader, then deleted. pytest: 53 (parsing, URL rules, a local HTTP server with a redirect, SHA-1 mismatch, duplicate content, uploads with an MD5). |

### 22.4 Batch J: installer profiles, remote KVM hosts (built)

- `install-labape.py --profile full|kvm|web`:
  - `kvm` installs the KVM-host side only (packages, libvirt, networks, storage, the engine tools, secrets);
  - `web` installs the container stack only and registers a remote KVM host.
- The `web` profile depends on remote libvirt over SSH (phase 10e,
  §15), which is built in this batch (question 69).

As built:

| Area | As built |
|---|---|
| Approach | Jobs for a `qemu+ssh://user@host/system` host run **on that host** over SSH, with the same engine scripts the CLI uses there. This replaced §15's plan of rewriting every script for libvirt storage-pool APIs. The worker builds the workspace as usual, with `libvirt_uri = qemu:///system`, because libvirt is local to the scripts. It ships the workspace with `tar` over SSH to `~/.labape/jobs/<job>`. The vault password, the VM SSH key pair and an `env` file (state connection, key paths, `LIBVIRT_URI`, `TF_PLUGIN_CACHE_DIR`) go into `.labape-remote/`, mode 0700. The command runs with `ssh -tt` (a TTY, so cancelling the local ssh hangs up the remote job) and `-R 127.0.0.1:<port>:postgres:5432`, so OpenTofu's pg backend reaches the state database through the tunnel. Afterwards `ansible/inventory` is copied back for the usual output handling, and the remote copy is removed. Cleanup also runs after failures. |
| ISOs | For ISO host groups and template builds, the needed ISO is copied to `<vm_storage_path>/.labape-isos/` on the host when its name and size differ, and `environment.yml` points there. |
| Packer (question 34) | Template builds for a remote host run on that host, with its Packer (the `kvm` profile installs it). |
| Worker-side reads | Host stats keep using libvirt directly (`virsh --connect qemu+ssh://...?keyfile=...&no_tty=1`). `/proc` and disk usage come from one Python probe over SSH. The template library is listed, and its metadata written, by Python snippets over SSH (`hostio.TEMPLATES`, `WRITE_FILES`). The host key is accepted on first contact and recorded in the job HOME's `known_hosts` before any libvirt call, because libvirt's ssh transport refuses an unknown host. |
| Key | The worker's key `~/.ssh/labape_kvm` (job HOME, on the data volume) is generated on first use. It's shown on the host's page with Copy, and printed by `labape worker-key`. It is never used for VMs. |
| API and UI | Hosts accept `qemu:///system` or `qemu+ssh://user@host[:port]/system`. `GET /api/hosts/worker-key`. `POST /api/hosts/{id}/check` reports the libvirt version, the tools on the host (virsh, virt-install, qemu-img, xorrisofs, tofu, ansible-playbook, packer, tar) and whether the storage paths are writable. The host page shows a "Remote access over SSH" panel. |
| Installer | `--profile full|kvm|web` (answer `Profile`). `kvm` skips the `Stack.*` steps and adds `Remote.AuthorizeWorker` (`WorkerPublicKey`). `web` skips the KVM side (no `/dev/kvm`, base packages only) and registers `RemoteKvmUri`. Steps that won't run no longer ask for their answers, and answers can be `optional`. |
| Verified | Live: the lab host registered a second time as `kvm1-ssh` (`qemu+ssh://AutomationUser@172.21.20.11/system`), with the worker's key authorized for AutomationUser. **Check connection** passed (QEMU 10.0.13, all tools, writable paths). Stats (CPU, storage) and all 20 templates came over SSH. Environment `remote1` (a Rocky 9 clone, DHCP) deployed on the host over SSH: OpenTofu state went through the tunnel, the VM was created, its DHCP address found, and Ansible ran with 0 failed. The inventory was copied back and `~/.labape/jobs/<job>` removed. The destroy ran the same way. Afterwards the test host was disabled and the key removed. Installer: dry runs of all three profiles ran exactly the expected steps. pytest: 58. |
| Not yet | One host per environment, as before; spanning hosts with best-fit placement (§15) is still to come. ISO and template copies between hosts aren't automatic beyond the ISO a job needs. |

### 22.5 Batch K: platform certificate authority (built)

- A step-ca for the LABaPe deployment that issues certificates to the
  web UI, the KVM hosts and every VM. An environment with its own CA
  gets that CA certified by the platform CA instead.
- Option A was chosen (question 70): environment CAs are subordinate
  to the platform root. Details are in
  `Claude_Docs/Planning_Certificate-Authority.md` §5.

As built:

| Area | As built |
|---|---|
| CA service | `labape-ca` in the stack: `smallstep/step-ca:0.30.2`. On first start `container/ca/init.sh` runs `step ca init` (standalone; root and intermediate, 10 years each, not the 5-year intermediate proposed). It uses `secrets/ca_password` (generated by `setup.sh`) for the keys and the JWK provisioner `labape-runner`, plus an ACME provisioner. The image's own init isn't used, because it prints the provisioner password into the container log. `labape-runner` issues for 30 days by default (90 at most). DNS names: `labape-ca`, `localhost`, `LABAPE_HOSTNAME`. Published on `LABAPE_CA_PORT` (default 9444, since 9443 is the test Authentik's HTTPS port) for renewals. The volume `labape-ca` is mounted read-only into the API, the worker and Caddy (the root certificate). |
| Signing | VMs make their own key and CSR, and Ansible posts the CSR to `POST /api/engine/certificates` with headers `X-LABaPe-Job` and `X-LABaPe-Token`. The token is random per deploy job, kept in the secrets store and cleared when the job ends; it's valid only while that job runs. The API checks that the host is one of the environment's VMs and that the CSR asks only for its short name, its FQDN (the instance's `domain_name`) and addresses (its static allocation, or an address in the environment's networks, for DHCP). It then signs with `step ca sign --provisioner labape-runner` and returns the chain plus the root. Each signing, and each refusal, is audited. |
| Jobs | The runner gives `environment.deploy` jobs `LABAPE_CERT_TOKEN`, `LABAPE_JOB_ID`, `LABAPE_CA_RENEW_URL` and `LABAPE_CERT_API`: the internal `http://labape-api:8000` for local jobs, and the public URL for remote hosts, whose `ansible/` runs there. The remote path passes them through. Without a configured CA the variables are empty and the role does nothing; CLI deploys are unchanged. |
| Role `labape_cert` | Runs last in `site.yml`, for Linux and Windows hosts. **Linux:** the root goes into the trust store (`update-ca-trust`, or `update-ca-certificates`); a P-256 key in `/etc/labape/tls/host.key` (0600, never copied off); a CSR with `openssl req -addext subjectAltName`; the chain in `host.crt`. A daily systemd timer runs `labape-cert-renew`, which, with under 10 days left (or `--force`), calls step-ca's `/1.0/renew` over mTLS with the current certificate. **Windows:** the root goes into Trusted Roots and the intermediate into Intermediate CAs. `certreq -new -machine` makes a non-exportable RSA key in the machine store and the CSR; the signed leaf is accepted with `certreq -accept -machine`. A daily scheduled task runs `renew.ps1`, which renews over mTLS (`Invoke-RestMethod -Certificate`) and links the existing key to the new certificate (`certutil -repairstore`). |
| Web interface certificate | `LABAPE_CADDYFILE=Caddyfile.labape-ca` (in `container/.env`): Caddy gets its certificate from step-ca's ACME provisioner. The explicit `issuer acme` matters, because Caddy otherwise uses its internal CA for an IP address. Browsers trust the LABaPe root, downloadable from the dashboard (`GET /api/ca/root.crt`, public). |
| Dashboard | "Secrets and certificates" lists the platform CA (`step ca health` and the root fingerprint), with the root download link. |
| Verified | Live on the lab host. The CA initialized with no password in its log. The API signed a probe CSR (30-day leaf, chain of 2). Caddy switched to `Caddyfile.labape-ca` and got its certificate over ACME for the IP address; `curl` trusts it with only the LABaPe root. Test environment `certs1` (a Rocky 9 and a Windows Server 2022 clone, DHCP): `labape_cert` ran on both. On Rocky: SANs `ctrky1`, `ctrky1.labape.test` and the DHCP address; `curl https://172.21.20.11/` succeeded through the system trust store; `labape-cert-renew --force` renewed over mTLS on port 9444; the timer was enabled. On Windows: the certificate is in LocalMachine\My with its key; `Invoke-WebRequest` to the web interface succeeded through Windows trust; `renew.ps1 -Force` renewed and kept the key linked; the task was Ready. The environment was then destroyed. pytest: 62. |
| Not yet | Environment CAs subordinate to the platform root (Claude_Docs/Planning_Certificate-Authority.md §5.2) wait for M9's `certificate_authority` role. KVM hosts don't get certificates yet. |

## 23. Action tooltips, confirmations and the update flow (2026-10-11)

From the feedback after round 3:
- every action shows a short tooltip, saying when a confirmation follows;
- every action that changes something asks first, with a fuller note and Continue or Cancel;
- Check now offers a newer release (and a newer branch) with links to their notes;
- a downgrade is possible.

### 23.1 Tooltips and confirmations (built)

| Area | As built |
|---|---|
| Components | `components/Confirm.tsx`. `ConfirmProvider` (wrapping the app) and `useConfirm()`: a promise-based dialog with a title, a fuller note, Continue (red with a warning icon for destructive actions) and Cancel. `ActionButton` / `ActionIconButton`: a button with an MUI tooltip of its summary. With `confirm`, the tooltip ends with "(asks to confirm)" and the click runs only after Continue. A span keeps the tooltip on disabled buttons. |
| Confirmed | Deploy and redeploy, destroy, refresh addresses, cancel and retry a job, create and deploy an environment, remove a host group, register or save a host, save a host's networks, add, save or delete a network, publish or retire catalog items, save a catalog item, import catalog YAML (after the file is picked), upload (after the file is picked), cancel an upload, delete a file, save file and template details, save each Settings section, back up now, send a test syslog message or email, save error retention, reload rights, sign out. The browser's `window.confirm` is gone. |
| Tooltip only | Navigation (top bar, Add or New links that open a form, Back, Cancel), read-only actions (filter, export, show, copy, check connection, test search) and buttons inside dialogs that are themselves the confirmation (promote, start job, send request, respond). Plain links and buttons use `title`. |
| Verified | Type-checked in the image build and deployed on the lab host; the browser tool wasn't available in this session to click through every dialog. |

### 23.2 Update flow (built)

Decisions: questions 76-80, 82.

- **Check now** opens a dialog with each choice and a link to its notes:
  - the newest **release**;
  - a newer **pre-release** (the preview channel), if there is one;
  - **Roll back to <previous>**, while a previous version is on standby.
- **Signed releases only.** The agent checks the tag's signature against a pinned allowed-signers file, which the installer puts in place. An unsigned tag, or one signed by an unknown key, is refused.
- **Host-side update agent.** A systemd path unit installed by the installer runs as the service user, so the containers never get the Docker socket. The app writes a request file into a folder shared with the host, and the agent:
  1. turns on **maintenance mode** (§23.3) and waits for running jobs to finish, up to a limit;
  2. takes a database backup, labelled with the version it came from;
  3. tags the running image `labape:previous` and records the current tag;
  4. checks out the new tag, rebuilds with `LABAPE_VERSION`, and starts the stack, which runs database migrations;
  5. runs health checks for about 5 minutes (the API, the worker heartbeat, the database, the CA, and one harmless job);
  6. if they pass, turns maintenance off; if they fail, rolls back.

  It writes its progress where the dashboard shows it.
- **Rollback.** Only to the previous version, from the app. It restarts the `labape:previous` image (no rebuild), plus the pre-update backup if the database layout changed. It happens automatically when the health checks fail, or on demand with **Roll back** until the next update. A breakage the checks miss is why the previous image stays.
- **Further back.** A manual, documented script on the host: `deploy/labape-rollback.sh --to vX.Y.Z [--restore-backup FILE]`.
- **Persistent data.** Everything that must survive is already in named volumes (database, job data, the CA, Caddy), which updates never touch. Database migrations (Alembic) come first, so a version change knows the layout it's moving between.
- **Migrations (built).** `labape/migrate.py` runs at startup under the shared advisory lock:
  - a new database is created from the models and stamped at the head;
  - a database from before migrations is stamped at `0001_baseline`, then upgraded;
  - otherwise pending revisions are applied;
  - a database at a revision the code doesn't know (written by a newer version) stops startup with that message.

  `labape db-status` and the dashboard's version banner show the database's revision next to the code's. Later schema changes are new files in `labape/migrations/versions/`. Verified on the lab host: the existing database was stamped at the baseline.

As built:

| Area | As built |
|---|---|
| Shared folder | `container/update/` (git-ignored), mounted at `/var/lib/labape-update` in the API and worker. It holds `request.json` (written by the app), `status.json`, `previous.json` and `agent.log` (written by the agent), and `agent.json` (written at install). The install script creates it, so Docker doesn't create it as root. |
| Agent | `deploy/update-agent/labape-update.sh`, run by a user-level `labape-update.path` / `.service` (lingering keeps the user's systemd up). `install.sh` copies the script to `~/.local/libexec`, because it checks other versions out while it runs. The steps: refuse a checkout with local changes; fetch tags; `git verify-tag` against `~/.config/labape/allowed_signers` (SSH signatures; the list starts from `deploy/allowed_signers`); `labape maintenance on`; wait for running jobs (`LABAPE_DRAIN_SECONDS`, default 1800); `labape backup --label pre-update-<from>`; record the revision; tag `labape:previous` and write `previous.json`; check out, build with `LABAPE_VERSION` and `up -d` (migrations run at start); health. "Healthy" means `labape health --deep` (database at the code's head, worker heartbeat under 3 minutes, CA) **and** `/api/health` through Caddy (by `LABAPE_HOSTNAME`). It must be healthy within 2 minutes and at every check for `LABAPE_HEALTH_SECONDS` (default 300). If it passes, maintenance goes off. A failure at checkout, build, start or health rolls back on its own. |
| Rollback | Puts back the previous checkout and the `labape:previous` image (no rebuild). When the database's revision differs from the one recorded, it restores the pre-update dump with `labape restore` in a one-off container, the API and worker stopped. Then it starts, checks health and turns maintenance off. `previous.json` is removed, so it's one step back only. If the previous version doesn't come up either, maintenance stays on and the status says so. |
| Further back | `deploy/labape-rollback.sh --to vX.Y.Z [--restore-backup FILE]`, run by hand (maintenance, backup, checkout, build, optional restore, health). |
| App | `labape/update.py` and `/api/admin/update` (admins; allowed during maintenance). `GET` returns the current version, the newest release and a newer pre-release from GitHub's releases list (cached ten minutes), the database revision, what's on standby and the agent's status. `POST` accepts only a tag on offer and refuses while an update runs or when no agent is installed. `POST /rollback` needs a version on standby. New CLI commands for the agent: `labape maintenance on/off/status`, `labape backup --label`, `labape restore FILE --yes`, `labape health [--deep]`. The worker writes a heartbeat (`status.worker`) each cycle. |
| UI | Check now opens the update dialog: the current version and layout, then the release, the pre-release (marked as a preview) and Roll back, each with a link to its notes. Roll back says when its backup will be restored. It explains what Continue does, or shows the commands when no agent is installed, then shows the agent's progress live. |
| Installer | `Stack.UpdateAgent` (web profiles): `loginctl enable-linger` for the service user, then `install.sh`. |
| Verified | Live on the lab host, with local tags signed by a throwaway SSH key (never pushed). **A:** update to a signed tag passed every step (maintenance, backup, build, 90 s of health) and kept the previous version on standby. **B:** rollback restarted the standby image in seconds and cleared the standby record. **C:** a signed tag whose API crashes failed its health checks and rolled back on its own to a healthy stack with maintenance off. **D:** an unsigned tag was refused before anything changed. The API offered nothing for local tags (they aren't GitHub releases), refused a rollback with nothing on standby and showed the agent's last status. pytest: 78. |

### 23.3 Maintenance mode (built)

- **One flag**, set three ways:
  - by an admin (Settings > Maintenance, with a reason and an expected end);
  - by the update agent, a rollback or a restore;
  - by a secret rotation that needs a restart.

  Each change is audited and sent to syslog.
- **Writes stop.** The API refuses every change with 503 and the reason. These stay open: sign-in and sign-out (break-glass included), turning maintenance off, the update and rollback controls, and cancelling a job. Reads, status, the dashboard and live job logs keep working.
- **The worker drains.** It claims no new jobs and lets running ones finish. Scheduled work (backups, purges, release checks, rotations) pauses, except the work the maintenance itself needs.
- **Visible everywhere.** A banner on every page shows the reason, who started it and the expected end. Change buttons are disabled with "Unavailable during maintenance". `/api/health` reports the mode.
- **Scheduled windows.** A window can be set ahead, with a heads-up banner before it starts. Rotations that need a restart use the same window (§24).

As built:

| Area | As built |
|---|---|
| State | `maintenance_mode.py`: the `maintenance.state` setting holds on/off, the reason, the source (admin, update, rotation, restore), who and when, the expected end and an optional scheduled start. The API's middleware reads it through a 5-second cache. The worker reads it fresh before every claim; a cached "off" once let a job start during maintenance. |
| API | `GET /api/maintenance` (signed in): the state plus running and queued job counts and the next window. `PUT /api/admin/maintenance` turns it on (a reason is required; optional expected end), schedules it, or turns it off, all audited. While it's on, every non-GET `/api/` request answers 503 with the reason, except sign-in and sign-out, `/api/admin/maintenance`, `/api/admin/update*`, job cancel, job certificate signing and blocked-form reports. `/api/health` reports `maintenance`. |
| Worker | Claims no new jobs (running ones finish). Pauses the daily backup, purges and the release check (host stats, template and file sync go on, since they're status). Starts a scheduled maintenance at its time. Turning it off stays explicit. |
| Window | Settings section `maintenance_window`: on or off, weekday, start (UTC), length and notice hours. A heads-up banner appears before it starts. Restart-needing rotations will use it (§24). |
| UI | The banner on every page shows the reason, who started it, the expected end (or "overdue") and the jobs still running, plus a scheduled-maintenance notice and the window heads-up. Every action that confirms is disabled, with the tooltip "Unavailable during maintenance", except sign-out, Reload rights, job cancel and turning maintenance off. Settings → Maintenance mode: turn on now or schedule, and turn off. |
| Verified | Live on the lab host. With maintenance on, a developer's create was refused (503) while reads worked, health said `maintenance: true`, and an admin's Back up now was refused. A job queued directly in the database stayed queued for the whole maintenance, then ran and succeeded once it was turned off. pytest: 70. |

## 24. Secret rotation (phases R1 to R4 and the OIDC rotator built)

Decisions: questions 81, 83, 84, 90, 91.

- **Framework.** Each kind of secret has a rotator with five steps: make the new value, apply it where it's used, verify, commit, undo. Where the system allows it, old and new stay valid together until the new one is verified. Each rotation is audited. A failure keeps the old secret, alerts (dashboard, email, syslog) and retries hourly.
- **Settings > Secrets** lists each secret with its store, class, interval, window, last and next rotation, status, notes, and a Rotate now button.
- **Stores.** Several can be in use; each secret names its store (built-in first, and OpenBao/Vault or Conjur in phase 10d). When an external provider rotates a secret itself, LABaPe only reads it.
- **Elevated accounts.** A secret whose account can't change its own credential names an elevated account that can. Examples: the PostgreSQL superuser for the app's role, or an Authentik token allowed to replace the directory token.
- **Intervals.**
  - Privileged secrets: 24 hours.
  - Read-only secrets: 7 days.
  - Rotations that need a **service restart**: 7 days, inside the **maintenance window** (day and time set in Settings), with a note on the secret saying why.
  - Every interval can be changed per secret.

| Secret | Class | Default | Rotation |
|---|---|---|---|
| PostgreSQL app password | privileged, restart | 7 days in the window | `ALTER ROLE` with the elevated account, rewrite the connection secrets, restart the API and worker |
| `secret_key` (sessions, built-in store) | privileged | 24 h | Two keys at once (MultiFernet), re-encrypt the store, drop the old key later |
| CA key password and signing provisioner | privileged, restart | 7 days in the window | `step crypto change-pass`, provisioner update, restart the CA |
| Worker SSH key for remote KVM hosts | privileged | 24 h | Add the new key with the old one, verify, remove the old one |
| VM bootstrap SSH key | privileged | 24 h | Push to running VMs, keeping the 7 previous keys (§24.3) |
| Ansible vault password | privileged | 24 h | `ansible-vault rekey` (§24.3) |
| Authentik directory token | read-only | 7 days | Through the Authentik API with an elevated token, otherwise a reminder |
| OIDC client secret | privileged | 24 h with an elevated Authentik token, otherwise a reminder | New secret on the Authentik provider, checked at the token endpoint, kept in the built-in store (§24.4) |
| SMTP password, other external credentials | external | reminder only | |
| Job certificate tokens, break-glass tokens | one-time | n/a | They already expire after one use |

- **Lab environments (question 83).** The domain admin and local admin passwords rotate every **7 days** by default; each environment sets its own interval or turns rotation off. The **DSRM password never rotates**. A VM that's off when its password rotates is brought up to date at its next deploy or rotation.

### 24.1 Phase R1 as built

| Area | As built |
|---|---|
| Envelope encryption | The built-in store writes `v2:<data key id>:<token>`. Data keys live in `secret_data_keys`, wrapped by the master key (LABAPE_SECRET_KEY). The active key is read from the database on every write, so a rotation in another process is picked up at once. Values from before data keys are still read and are rewritten by the first rotation. Retired keys are deleted two days later, once no value uses them. |
| Policies | The `secret_policies` table (migration `0002`, the first real one) has one row per platform secret, seeded with the rotator's defaults: store, class, interval, restart, enabled, external flag, elevated account, parameters, status, last and next rotation, last error and notes. A restart-needing policy can't go below 168 hours. |
| Engine | `engine/rotation.py` holds the registry of rotators. Every minute the worker queues a `secret.rotate` job for each due policy (unless maintenance is on or a job is already queued). Restart-needing ones wait for the maintenance window and switch maintenance on while they run. A failure rolls back, keeps the old secret, records the error, retries an hour later and is audited (so it reaches syslog). Rotate now queues the same job. |
| Elevated accounts | Credentials stored as `elevated/<name>` in the built-in store, added on the Secrets page (write-only) and chosen per policy. |
| Rotators | `builtin_data_key` (24 h): re-encrypts every secret with a new data key and verifies before committing. `worker_ssh_key` (24 h): adds the new key on each enabled remote host with the old one, checks it logs in, then removes the old key there and swaps it here (undone on failure). `directory_token` (read-only, 7 days): sets a new key on the Authentik token with the elevated token, checks group search with it, and puts the old key back if that fails. The other seven secrets are listed as manual or reminder, with notes naming the phase that brings their rotator (all but the SMTP password have one now). |
| UI | Top bar → Secrets: every secret with its class, store, interval, last and next rotation and status (a "restart" chip, and the reason on hover when failed), plus Add elevated account. Each secret's page shows how it rotates, the restart note, the last job, its settings and Rotate now (confirmed). |
| Test Authentik | A `labape-rotator` service account and token. Authentik checks `POST /tokens/<id>/set_key/` as `add_token` on that token object; the `set_token_key` its docs name isn't checked in this version. The blueprint importer can't assign object permissions to a role, so `deploy.sh` grants them with `ak shell`, on the directory token only. Both tokens use `state: created`, so the blueprint never resets a rotated key; the `labape` provider does too, since the OIDC rotator (§24.4) changes its client secret. |
| Verified | Live on the lab host. Migration `0002` upgraded the database. The directory token rotated through the elevated account: search worked with the new key, the old one got 403, the next rotation is in 7 days. The data key rotated: all 19 secrets read back and the store self-test passed. The worker key rotated. The first attempt failed with Authentik's 403, and the old token stayed in use, as designed. pytest: 75. |

### 24.2 Phase R3 as built: secrets that need a restart

| Area | As built |
|---|---|
| Who rotates | The master key, the PostgreSQL password and the CA password live in host files (`container/secrets/`) that the containers only read, so the **host's update agent** rotates them (`rotate <kind>`). The scheduler (in the maintenance window) or Rotate now writes an agent request carrying the policy and a request id, and marks the policy running. The worker isn't asked to wait as a job, because the agent restarts it. Each cycle, `reconcile_agent` matches the agent's `status.json` (which now echoes the request id) and records ok or failed (retrying an hour later). |
| Agent steps | Maintenance on, wait for running jobs, make the new value, apply it, recreate the containers that read the file (`up -d --force-recreate`; a replaced file needs a new bind mount), health checks, maintenance off. A failure puts the old value back everywhere and recreates again. |
| Master key | `labape rewrap-master --from --to`, in a one-off worker container with the secrets folder mounted, re-wraps every data key to the new master key in one transaction, and moves any value still encrypted with the master key itself onto a data key. Then the file is swapped and the API and worker recreated. Sessions are signed with the master key, so everyone signs in again (why it's weekly, in the window). On failure the data keys are re-wrapped back. |
| PostgreSQL | `ALTER ROLE labape WITH PASSWORD` through `psql` on stdin (not on a command line). `postgres_password`, `database_url` and `tofu_pg_conn` are rewritten, then the API and worker recreated. On failure the old password is set again and the files restored. |
| CA password | `container/ca/repass.sh` (the CA folder is now mounted whole at `/labape`) re-encrypts the root and intermediate keys with `step crypto change-pass`. `labape ca-repass` re-encrypts `labape-runner`'s key in `ca.json`. That key is a compact JWE (PBES2-HS256+A128KW, A256GCM), implemented with `cryptography`, because `step crypto jwe encrypt` only takes a password at a prompt; it's checked against the step CLI in both directions. It runs in a one-off container with the CA volume. Then the file is swapped and the CA, API and worker recreated. `labape health --deep` now **signs a test request**, so a wrong password fails the check. |
| Verified | Live on the lab host, through Rotate now: the PostgreSQL password, then the master key, then the CA password, each with maintenance on and off again, a healthy stack, the secret store's self-test ok and directory search working. After the master key, all stored secrets still decrypted. After the CA password, the CA signed with the new one. No leftover files. pytest: 82. |
| Also fixed | The store cached unwrapped data keys by id alone. A recreated table, or SQLite reusing a rowid, could then encrypt with a stale key. They're now cached by id and wrapped value. |

### 24.3 Phases R2 and R4 as built: engine secrets, lab passwords, the Linux admin account

| Area | As built |
|---|---|
| Engine secrets (R2) | The vault password and the VM key live in `container/engine-secrets/`, which the containers mount read-only, so the **host's update agent** rotates them too (`rotate vault_password`, `rotate bootstrap_ssh_key`). Unlike R3 there's **no restart and no maintenance mode**: every job copies what it needs when it starts. Both run every 24 hours. |
| Vault password | The agent copies the vault, runs `ansible-vault rekey` on the copy in a one-off worker container, checks the copy opens with the new password, then swaps the vault and its password back to back and checks again (put back on failure). A job that starts mid-swap would copy a mismatched pair, so the runner checks its copies open and copies again (up to five tries). **The command line's vault** on the same host (2026-10-12) gets the same new password: the agent rekeys a copy of it from its own password file (its contents may differ from the engine's), checks it, and writes it and the password file in place beside the engine pair's swap; if either check fails, all four go back. Which vault is set by `install.sh --cli-vault FILE|none [--vault-pass-file FILE]` in `~/.config/labape/agent.env` (default `<repo>/secrets.vault.yml`, `~/.labape-vault-pass`). Verified live: the lab host's `~/LABaPe` vault followed a rotation, and a wrong password file stopped it with nothing changed. |
| VM key | The agent makes an ed25519 key beside the old one and runs `labape push-vm-key`. For each deployed environment it adds the new public key to the Linux VMs' admin account (signing in with the current key or one of the previous ones), checks the new key signs in, and removes keys older than the window. Then the old key moves to `ssh/history/`, the new one takes its place, and both are copied into the service user's `~/.ssh` (`labape_bootstrap`, history in `labape-history/`) so the command line stays in step (question 90, option b). **7 previous keys** are kept; jobs offer them after the current key (`ANSIBLE_SSH_COMMON_ARGS -i ...`), so a VM that was off during a rotation is still reached. A VM that can't be reached is reported, not fatal. Environments on remote KVM hosts are skipped: the stack can't reach their VMs. |
| Reinstall triggers | The Linux answer files and clone seeds carry the public key, and the VMs' reinstall triggers hash them, so a rotated key would reinstall every Linux VM on its next deploy. The `vm` module now records the key each VM was created with (`terraform_data.creation_ssh_key`, `ignore_changes`) and hashes the files with that key instead. New VMs still get the current key. Upgrading only adds that record (VMs built before R2 all have today's key, so their hashes don't change; a VM whose key had already rotated would be reinstalled once); a rotated key changes nothing; a real seed edit still reinstalls. All three were checked with a throwaway OpenTofu config, then on `lab2`. |
| Lab passwords (R4) | Policies `env.<id>.windows_admin` (the local Administrator on Windows hosts other than DCs) and `env.<id>.domain_admin` (the domain Administrator, changed on one DC) are created for each deployed environment that has such hosts, weekly by default. Owners and admins change the interval (in days) or turn it off on the environment's page; **DSRM never rotates**. A policy is skipped while its environment isn't deployed, and kept so its settings survive a redeploy. Environments on remote KVM hosts get no policies. |
| How a password changes | WinRM's Basic auth sends the password with **every request**, so a module that changes the password of the account it's connected as fails on its next request although the change worked (Reference_Credentials.md §9). The change therefore runs detached (`async: 120, poll: 0`) through a generated playbook, with the new password in a 0600 variables file. Signing in decides the outcome: `win_ping` with the new password (first after 10 s, then every 15 s, for up to 75 s, so a lockout policy sees few failures), then with the old one. **All or nothing:** if any host didn't change, the ones that did get their old password back. The candidate password is stored first (`environment/<id>/rotation-candidate/<kind>`), so neither a crash nor a host where neither password works loses it. |
| What follows a change | The stored inventory, the owners' credentials sheet (the Password line of the matching section; the sheet now has a separate "Domain administrator" section) and the environment's password overlay `environment/<id>/passwords`. Each job rewrites its own vault copy with the overlay (`overlay_vault`), so later deploys, joins and new hosts use the rotated passwords. The DC's inventory entry uses `domain_admin_password` (`LABAPE_DOMAIN_ADMIN_PASSWORD` for `generate-inventory.py`), so the two no longer have to be equal once rotated. Destroying an environment clears its overlay: a new DC is built from the vault's matching pair. One job runs per environment at a time (the claim skips an environment with a running job). |
| Linux admin account (question 91) | Settings → VM accounts → **Linux admin user** (default `labape`; a valid Linux name, not a system one). It's recorded in the environment's spec when it's created, so a change applies to new environments only. It flows into `environment.yml`, the OpenTofu backends' `linux_admin_user`, the answer files and clone seeds (`${admin_user}`), the inventory's `ansible_user` and the credentials sheet. Packer templates keep their build account, `labape`. With `labape`, every answer file renders byte-identically to before, so existing VMs aren't replaced. |
| Verified | Live on the lab host. The vault password rotated with maintenance off, and the worker opened the new pair. The VM key rotated: added and checked on `lab2`'s Rocky VM, swapped in both places, the previous key in history. The first attempt failed (ansible-core has no `json` callback; the old key stayed, as designed). On `lab2` (DC, Windows member, Rocky): the Windows Administrator and then the domain Administrator rotated, and the credentials sheet followed. The first Windows attempt hit the Basic-auth problem above: the password changed but was reported failed, so it was lost. `l2win1` was put back through the domain admin over NTLM, which led to the detached change. A full redeploy of `lab2` with all four rotated secrets then succeeded on every host, but it **reinstalled `l2lin1`** because its seed hash included the rotated key, which led to the creation-key fix above. Rotating once more before deploying the fix reinstalled it a second time, as expected for a VM whose key rotated before the record existed (only possible on this lab, since R2 ships with the fix). After a third rotation, a redeploy replaced nothing: only the seed file was rewritten, and every VM kept its domain ID. pytest: 93. |

### 24.4 OIDC client secret rotator as built

| Area | As built |
|---|---|
| Where the secret lives | It's installed as the container secret file `secrets/oidc_client_secret`, read at start-up. After its first rotation the built-in store holds it (`platform/oidc_client_secret`) together with a SHA-256 of the file's value at that moment. Each sign-in reads the store, so the API picks up a new secret without a restart. If the file is later replaced by hand (its hash no longer matches), the file wins again: an admin who pastes a new secret isn't overridden by an old rotation. |
| Rotation | `oidc_client_secret` (privileged, 24 h, worker job). With the elevated Authentik token it finds the provider by client ID (`GET /api/v3/providers/oauth2/?client_id=`) and sets a new 128-character secret (`PATCH .../<pk>/`). Authentik keeps one secret per provider, so the old one stops working at once; a sign-in completing in that second fails and is retried by the user. |
| Verify and undo | The token endpoint checks the client secret before the code, so LABaPe posts an `authorization_code` request with a made-up code: `invalid_grant` means the secret is accepted, `invalid_client` that it isn't. Nobody is signed in. If the check fails, the old secret is put back in Authentik (or the error says to set it by hand when that fails too) and the store isn't changed. |
| Elevated account | The token needs `view_oauth2provider` and `change_oauth2provider` on the LABaPe provider only. The test Authentik's `labape-rotator` gets both from `deploy.sh`. |
| Reminder rule | A rotator that needs an elevated account (this one and `directory_token`) isn't scheduled until its policy names one, and the Secrets page shows it as a reminder rather than failing every hour. Rotate now still runs it and says what's missing. |
| Verified | Live on the lab host with the test Authentik (`tools/helpers/live/rotate_oidc.py`): rotated through the worker, the old secret then got `invalid_client` at the token endpoint, a fresh sign-in worked, and sign-in still worked after restarting the API. pytest: 96. |

## 25. External secret stores: OpenBao/Vault and Conjur providers (phase 10d, built 2026-10-12)

Requirements: `Claude_Docs/Reference_Secrets-Backend-Requirements.md`. Test stores: `tools/secrets-test/` (live on the KVM host). Decisions: questions 93-99 (all answered 2026-10-12).

**The gap.** Rotation policies name a store, but a policy maps to a rotator, not to stored secrets. Several policies share one secret (an environment's `windows_admin` and `domain_admin` both write `environment/<id>/passwords`), and most secrets have no policy at all (environment credential sheets and inventories, settings tokens, job certificate tokens). So the store is decided **per stored secret**, not per policy.

**Design.**

| Area | Plan |
|---|---|
| Providers | `labape.secrets_providers` entry points: `builtin`, `openbao` (KV v2, AppRole; HashiCorp Vault speaks the same API), `conjur` (host + API key). Each implements the interface in the requirements doc §3, with `httpx` (5 s connect, 15 s per request), certificates checked against the platform root or a CA bundle, and the session token in memory only. |
| Placement | Every stored secret has a **catalog row** in the `secrets` table naming its store. For an external store the row holds no value; `get_secret`/`put_secret` follow it, so every caller stays as it is, and the data key and master key rotations skip it. The row also links the secret's **rotation**: rotator, elevated account and period (question 93), through its rotation policy, so the scheduler keeps one source of truth. To keep that one-to-one, an environment's `passwords` value splits into `environment/<id>/windows_admin` and `.../domain_admin`. |
| Moving | One move function, run by hand (Move) and automatically (a policy's store change, a new default): copy, read back and compare, point the row at the new store, then delete the old copy. On any failure the secret stays where it was. Audited. Secrets → Stored secrets lists every secret's name, store and rotation, with Move. |
| New secrets | Settings → Secret stores has a default store for new environment secrets (built-in unless changed). The secrets the stores themselves depend on always stay built-in: provider credentials, elevated accounts, job certificate tokens. |
| Settings | Settings → Secret stores: one section per provider (address, CA bundle, verify TLS, auth identity, write-only credential, Test connection). A store is offered on the Secrets page once it's configured and its test passes. |
| Provider credentials | Rotators of their own: `openbao_secret_id` (issue a new AppRole secret ID, log in with it, revoke the old one) and `conjur_api_key` (the host rotates its own key). |
| Externally rotated | `managed_externally` secrets are never written by LABaPe and are read fresh (cache of 60 s at most). |
| Failure | No silent fallback to the built-in store: a job fails before it starts work if its store doesn't answer. Errors name the LABaPe secret and the store, never a value or a response body. |

**Phases.**
1. **P1 providers:** the interface, `builtin`/`openbao`/`conjur`, Settings → Secret stores, Test connection, dashboard check, pytest with the stores' HTTP APIs faked.
2. **P2 placement:** pointer rows, routing, Move, the Stored secrets list, the default store for new environment secrets, policy store changes moving their secrets.
3. **P3 credential rotation and external rotation:** the two provider credential rotators, `managed_externally` reads.
4. **P4 live test and docs:** `tools/helpers/live/` script against the KVM host's test stores (move a secret there and back, rotate it, check the dashboard), User_Docs admin guide, CHANGELOG, the requirements doc where it changed. The live scripts arrived with each phase (§25.1-§25.3); P4 wrote `User_Docs/Web-Interface-Admin.md` §8.1 and the README status line.

Out of this round (question 95): the secrets the host's update agent keeps in files (the engine's Ansible vault password, the VM bootstrap SSH key, the master key, the PostgreSQL password, the CA password). They will later behave like every other secret.

### 25.1 P1 as built

| Area | As built |
|---|---|
| Package | `app/labape/stores/`: `base.py` (`SecretStore`, `HttpStore`, `StoreError`, name encoding), `builtin.py`, `openbao.py`, `conjur.py`, and the registry in `__init__.py` (bundled stores plus any `labape.secrets_providers` entry point; `open_store`, `check`, `configured`, `available`). One instance per configuration keeps its session token until the settings change. |
| OpenBao / Vault | AppRole login (`/v1/auth/<mount>/login`), token renewed at 80% of its lease and once more after a 401/403. KV v2 `data/` for get and put, `metadata/` for delete (all versions) and a recursive LIST. Optional path prefix and Vault Enterprise namespace. Test connection: `lookup-self`, then LIST the mount. |
| Conjur | `POST /<authenticator>/<account>/<login>/authenticate` with `Accept-Encoding: base64`; the token is used for 6 of its 8 minutes. A first write that gets 404 loads `- !variable` into the branch (`POST /policies`) and writes again. Delete is a policy `PATCH` with `!delete`, skipped when the variable doesn't exist. List pages `/resources/<account>/variable`. Test connection: `/whoami` and the branch's policy resource. |
| TLS and errors | HTTPS only (settings refuse `http://`). Certificates are checked against the CA bundle in the settings, else the platform root (`ca_root`), else the system store. 5 s to connect, 15 s per request. Errors name the store and the LABaPe secret only, never a value, token or body. |
| Settings | Sections `store_openbao` and `store_conjur`, registered by the registry. The credential (`secret_id`, `api_key`) is write-only and stored built-in as `stores/<name>/<field>`. `POST /api/admin/settings/stores/<name>/test` (audited). The Settings page has both sections under "Secret stores". |
| Dashboard | Each configured store appears under "Secrets and certificates" with its cached (60 s) test result; the secrets checks run off the event loop. |
| Tests | `app/tests/test_stores.py` fakes both APIs with `httpx.MockTransport`: name encoding, CRUD and list, session expiry and new login, no values in errors, timeouts, the built-in wrapper, settings and dashboard. pytest: 107. |
| Verified | Live on the lab host against the test stores (`tools/helpers/live/stores.py`): both saved through Settings, both Test connections and dashboard checks ok with the platform root, and a write, read, replace, list and delete (a name with a space) in each store. |

### 25.2 P2 as built: the catalog and moves

| Area | As built |
|---|---|
| Catalog | Migration `0004` adds `secrets.store` (default `builtin`) and `secrets.policy_id`. An external secret's row keeps `value` empty. `security.get_secret`/`put_secret` follow the row's store; a new secret goes to `default_store()`: Settings → Secret stores' default (section `secret_stores`) for names under `environment/`, the built-in store otherwise. Names under `stores/`, `elevated/` and `job/` are pinned built-in. The data key and master key rotations and the dashboard self-test only touch built-in rows. |
| Rotation link | `rotation.policy_secret()` names the secret each policy rotates (directory token, OIDC client secret, SMTP password, each environment's `windows_admin` and `domain_admin`); `link_secrets()` sets `policy_id` and keeps `policy.store` equal to the row's store. It runs with `ensure_policies` and after every successful rotation. The data key, the worker key and the agent's secrets have no row. |
| Passwords split | Each environment's rotated passwords are `environment/<id>/windows_admin` and `.../domain_admin`, one secret per policy. `split_legacy_passwords()` converts an old `environment/<id>/passwords` JSON once and deletes it; `env_password_overrides()` maps them onto the vault keys for a deploy, and a destroy clears them. |
| Move | `stores.move()`: refuse a pinned name or an unavailable store, read the value, write it to the target, read it back and compare, switch the row, then delete the old copy (a failed delete is reported, not fatal), update the linked policy's store, audit `secret.move`. Called by `POST /api/admin/stored-secrets/move` (several names, each moves or stays) and by saving a policy with a new store. |
| UI | Secrets page → Stored secrets: every secret's name, store, linked rotation (period, status, link to the policy) and elevated account, with tick boxes and Move to. A secret's page offers the available stores and moves it on Save. Settings → Secret stores → Default store. |
| Tests | `app/tests/test_stores_catalog.py`: move there and back, failures leave it in place, no fallback when the store is down, the default store, data key rotation with external rows, the policy link and store change, the move endpoint, the passwords split. pytest: 115. |
| Verified | Live on the lab host (`tools/helpers/live/stores_move.py`): migration `0004` upgraded PostgreSQL; lab2's old passwords JSON split into two secrets linked to their policies; the directory token moved built-in → OpenBao → Conjur → built-in with group search working after each move; lab2's credentials sheet moved to OpenBao and back and read the same; the default store saved. |

### 25.3 P3 as built: the stores' own logins, externally rotated secrets

| Area | As built |
|---|---|
| Policies | `engine/rotation_stores.py` registers `openbao_secret_id` and `conjur_api_key` (privileged, 24 h, no restart). Each policy is created once its store is configured, scheduled only while it stays configured, and linked to its catalog row (`stores/<name>/<field>`, always built-in). |
| OpenBao / Vault | Issue a new secret ID (`POST auth/<mount>/role/<role>/secret-id`) with LABaPe's own session, or with an elevated account's token when the policy names one; sign in with it; store it; destroy the old one (`.../secret-id/destroy`). If the new one doesn't sign in it's destroyed and the old one stays. LABaPe's policy needs `update` on those two paths of its own role; `tools/secrets-test/deploy.sh` grants it. |
| Conjur | `PUT /authn/<account>/api_key?role=host:<id>` with the host's own access token (falling back to basic auth with the current key). The old key dies at once, so the new one is stored before the sign-in check; if that check fails the job says to set the key by hand. |
| Externally rotated | `managed_externally` is only accepted for a secret in an external store; such a policy is never scheduled, and every read goes to the store (no cache), so the store's own rotation is seen at once. |
| Test stores | `deploy.sh`'s smoke test signs in to OpenBao with a throwaway secret ID (destroyed after), and skips the Conjur host check with a note when `.env`'s host key has been rotated by LABaPe. Stale copies are removed automatically: `deploy.sh` (every run and at boot) drops a copy the store refuses and sets `LABAPE_OWNS_LOGINS=1`, after which it never issues those logins again; `lab-live.sh` deletes the live tests' copy once `stores.py` or `stores_rotate.py` reports it stale. LABaPe itself can't delete copies outside its containers, so the admin guide (§8.1) has a step to rotate once and delete setup copies. `stores.py` no longer overwrites LABaPe's saved logins while they work. |
| Tests | `app/tests/test_stores_rotation.py`: policies appear with their store and pause when it's off, both rotations end to end against the fakes, a refused OpenBao rotation keeps the old secret ID, and the `managed_externally` rule with fresh reads. pytest: 120. |
| Verified | Live (`tools/helpers/live/stores_rotate.py`) after re-running `deploy.sh` for the policy grant: Conjur's host key and OpenBao's secret ID each rotated through a worker job; Test connection then signed in with the new login and the previous one was refused. `stores.py` kept the rotated logins, and `stores_move.py` moved secrets through both stores with them. |

## 26. OpenBao and Conjur in the stack; LABaPe's own secrets in the store (planned 2026-10-12)

Decisions: questions 100-103 (and 104-106, going ahead with the defaults). Until now the master key, PostgreSQL password, CA password, Ansible vault password and VM bootstrap key were host files the update agent rotated (§24.2, §24.3); the external stores (§25) were optional and held catalog secrets only.

- **Required stores.** OpenBao and Conjur Open Source are services of the LABaPe stack (`container/compose.yaml`), set up by the installer for every install with the web interface. Their TLS certificates come from the platform CA. The **platform store** (OpenBao by default, question 104) holds LABaPe's own secrets; both are available for catalog secrets.
- **Secret zero.** What opens the stores stays on the host, readable only by the service user, in `container/stores/secrets/`: OpenBao's unseal key and root token, Conjur's data key and database password, and the agent's own store login. The agent unseals OpenBao when the stack starts (question 105).
- **Logins.** Three, each with least privilege: the **app** (the API and worker: catalog secrets and the engine secrets), the **agent** (bootstrap secrets: read and write), and the **command line** (read the engine secrets only).
- **Bootstrap secrets** (master key, PostgreSQL password, CA password): kept in the platform store. The agent fetches them into memory-only files (`$XDG_RUNTIME_DIR`, tmpfs) and points the stack's secret files there before starting it; its R3 rotations write the store instead of the files.
- **Engine secrets** (vault password, encrypted engine vault, VM key and its 7 previous keys): ordinary catalog secrets in the platform store. Each job takes its own copies from the store when it starts. The worker rotates them (no agent, no restart). `container/engine-secrets/` is backed up into the agent's state folder and deleted.
- **Command line** (question 102 c): `scripts/lib/store_get.py` (standard library only, both stores) reads the vault password and VM key with the command-line login (`~/.config/labape/store.env`) into temporary 0600 files that the scripts delete on exit. The command line's own vault (`secrets.vault.yml`, its own contents) is encrypted with the same password, so a vault-password rotation asks the agent to rekey it (old and new are read from the store, never passed in a request).

**Phases, each shipped on its own:**

| Phase | What |
|---|---|
| S1 | The stores in the stack: compose services, `container/stores/` (configuration, policies, `init.sh`), installer step `Stack.Stores` (also runnable on an existing install), agent unseal at start, LABaPe's Settings → Secret stores filled in by the installer. |
| S2 | Engine secrets into the platform store; jobs read them; the worker's rotators replace the agent's R2 ones; `engine-secrets/` retired. |
| S3 | The command line reads from the store (`store_get.py`, the command-line login). |
| S4 | Bootstrap secrets into the platform store, fetched by the agent at start; R3 rotations write the store. |

### 26.1 S1 as built: the stores in the stack

| Area | As built |
|---|---|
| Services | `labape-openbao` (2.7.1, Raft storage on the `labape-openbao` volume), `labape-conjur-db` (PostgreSQL 17), `labape-conjur` (1.27.0) and `labape-conjur-tls` (nginx) in `container/compose.yaml`, in the `stores` profile so an install that hasn't run the setup still starts. Secret files are mounted one by one (a missing source would become a directory). Conjur's data key and database URL come from `stores/conjur/env.sh`, which its entrypoint and `stores.sh`'s `conjurctl` calls both source. |
| Setup | `container/stores/stores.sh setup`, the installer's `Stack.Stores` step, as the service user with no root. It talks to both stores over HTTPS with `curl`: tokens and keys go in headers through process substitution or in bodies on stdin, never on a command line. Conjur's policy is loaded through the API with the admin key, and the API key it creates for `host/labape/app` is kept in memory only. |
| LABaPe's logins | `labape stores-configure --needs` says which stores LABaPe lacks a login for (none stored, or its settings name another address); `stores.sh` issues only those (a new Conjur key would end LABaPe's) and passes them on stdin; `labape stores-configure` saves the settings (address in the stack, role ID, platform root as the CA bundle) and logins; `--check` runs LABaPe's own checks. |
| Secret zero | `stores/secrets/` (0700, files 0644 like `container/secrets/`). The update agent's AppRole (`labape-agent`, policy on `labape-host/` only) is checked on each run and reissued if it no longer signs in. |
| Unattended start | User units `labape-stores.service` (unseal at start; it waits up to five minutes for OpenBao) and `labape-stores-tls.timer`. |
| Verified | Live on the lab host (an existing install): setup from scratch, a re-run with no changes, OpenBao restarted (sealed, LABaPe's check failed with HTTP 503) and unsealed by the unit, LABaPe's daily rotation of both logins against the stack's stores, the agent's write and read-back. The old test stores are no longer used by LABaPe (question 106). Not yet checked: a full host reboot. pytest: 129. |

### 26.2 S2 and S3 as built: the engine secrets in the store, the command line reads them

| Area | As built |
|---|---|
| Catalog | `engine/engine_secrets.py`: `platform/engine/vault_password`, `vault_password_history` (7 previous), `vault`, `ssh_key`, `ssh_key_pub`, `ssh_history` (7 previous, newest first). `stores.sh` runs `labape engine-secrets import --store openbao` once (built-in first, then moved) and `check` (a job's copies: the vault opens, the key pair and history are written). |
| Jobs | `_prepare_base` writes the job's vault and password from the store (read again if a rotation was half-written); `sync_home` writes the VM key and history into the job HOME. Until the import, the files are used as before. The dashboard's vault check reads the store too. |
| Rotation | `vault_password` and `bootstrap_ssh_key` run in the worker once imported (`rotation.uses_agent`); the agent still handles them on an install that hasn't moved. The vault is re-encrypted in a temporary folder and checked before anything is stored; the VM key is pushed with `vmaccess.push_vm_key` before it's stored. |
| Command line | AppRole `labape-cli` (policy `labape-cli`: read on those six paths only), login in `~/.config/labape/store.env` (0600). `scripts/lib/store_get.py` (standard library) and `scripts/lib/cli-secrets.sh` (in `deploy.sh`, `destroy.sh`, the template scripts, `refresh-addresses.sh`) write the password and key to `$XDG_RUNTIME_DIR/labape-cli/`; the command line's own vault is re-encrypted when it only opens with a previous password. `ansible-vault` is also looked for in `~/.local/bin`. |
| Not automated | Deleting `container/engine-secrets/`, `~/.labape-vault-pass` and `~/.ssh/labape_bootstrap*` (question 103): the auto-mode classifier refused an automatic delete, so it's a manual step (back up first). |
| Verified | Live on the lab host: import (VM key and 3 previous keys), the command line's login and vault check, a worker rotation of the vault password (then the command line's vault re-encrypted itself, the old password file stale), a worker rotation of the VM key (pushed to `lab2`'s Linux VM), a full deploy of a throwaway Rocky VM with the store's secrets, and SSH to it with the command line's key from the store. pytest: 131. |

### 26.3 S4 as built: the bootstrap secrets in OpenBao

| Area | As built |
|---|---|
| Where | The six files of `container/secrets/` (master key, PostgreSQL password, `database_url`, `tofu_pg_conn`, CA password, OIDC client secret) under `labape-host/bootstrap/` in OpenBao: only the update agent's AppRole reads that mount. `container/stores/hostsecrets.py push|pull|same` (standard library) moves them with that login. |
| Running stack | `compose.yaml`'s secrets read `${LABAPE_SECRETS_DIR:-./secrets}`. `stores.sh setup` (`move_bootstrap`, refused while jobs run) pushes the files, pulls them into `/run/user/<uid>/labape-secrets` (tmpfs), checks both copies match, sets `LABAPE_SECRETS_DIR`, recreates PostgreSQL, the CA, the API and the worker, and waits for `labape health --deep`; if that fails, it goes back to `container/secrets/`. |
| Boot | Compose's file secrets are bind mounts through the mounts API, so with the folder empty Docker refuses to start those containers ("bind source path does not exist") and creates nothing. `labape-stores.service` (`stores.sh unseal`) unseals OpenBao, pulls the folder and runs `docker compose up -d`. |
| Rotation | The agent's R3 rotations (§24.2) work on `LABAPE_SECRETS_DIR` and push to OpenBao right after swapping a file, before the restart and health check; a failed push undoes the change, and a rollback pushes the old values again. |
| Not automated | Deleting the old `container/secrets/` (question 103's rule): left to the admin, after a backup. |
| Verified | Live on the lab host: the move with a healthy stack; a simulated reboot (stack stopped, tmpfs copy deleted, OpenBao sealed): the start failed cleanly, then the unit brought everything back; the agent rotated the PostgreSQL password, the CA password and the master key, each written to OpenBao (the store matched the running copy); a second simulated reboot came back healthy on the rotated values. A real reboot of the lab host (2026-10-12, by the user) came back unattended: OpenBao unsealed, the stack started on its secrets from OpenBao, both store checks ok. pytest: 131. |

### 26.5 Follow-ups (2026-10-12)

| Area | As built |
|---|---|
| Agent R2 code removed | The update agent no longer rotates the vault password and VM key as files (`rotate_engine` in `labape-update.sh` is gone); the worker always rotates them in the store, and asks for `stores.sh setup` when an install still has them as files. `install.sh --cli-vault` now only tells `stores.sh` where the command line's vault is. |
| Setting a bootstrap secret by hand | `container/stores/set-secret.sh NAME` (value on stdin) writes `LABAPE_SECRETS_DIR` and pushes to the platform store once the secrets have moved, else `container/secrets/`. The installer's `Stack.Auth` and `tools/authentik-test/deploy.sh --configure-labape` use it for the OIDC client secret, so neither writes the old folder after the move. |
| Test stores retired | `tools/secrets-test/` is removed; `live/stores.py` and `live/stores_rotate.py` check the stack's stores, and `lab-live.sh` no longer mounts or deletes a login file. |
| Remote KVM hosts' command line | Not built: folded into batch O4 of Planning_Operations-And-Admin-UI.md (store reachable from the host, a login per host). |

### 26.4 The platform store is a setting (question 104)

| Area | As built |
|---|---|
| Setting | `LABAPE_PLATFORM_STORE=openbao|conjur` in `container/.env` (default `openbao`; installer answer `PlatformStore`, written by `Stack.Stores`). |
| Conjur | The policy adds hosts `labape/agent` (owns `labape/host`, where the bootstrap secrets are `bootstrap/<name>`) and `labape/cli`. The agent's API key is kept in `stores/secrets/agent_conjur_api_key` (secret zero); the command line's goes into `~/.config/labape/store.env` (`LABAPE_STORE_KIND=conjur`). With Conjur as the platform store, `stores.sh` loads read permits for `labape/cli` on the six engine variables. |
| Moving | `stores.sh setup` after a change: `labape engine-secrets import --store <new>` moves the engine secrets (`stores.move`, which deletes the old copy); the command line's login is reissued for the new store; `hostsecrets.py` copies the bootstrap secrets, checks them, records `stores/secrets/platform_store_active`, then deletes the old copy (`drop`). Until that record changes, the boot unit and the agent keep using the old store. |
| Boot | With Conjur, the unit waits for Conjur to answer (it needs a minute or more after a start; nginx answers 502 until then) before pulling. |
| Verified | Live on the lab host: the default run changed nothing; a switch to Conjur moved everything; a worker rotation (vault password, the command line caught up) and an agent rotation (PostgreSQL password, written to Conjur) worked; a simulated reboot failed at first (Conjur's 502, which led to the wait), then came back healthy from Conjur; a switch back to OpenBao moved everything back and deleted the Conjur copies. pytest: 132. |

## 27. Syslog collector service; service roles (built 2026-10-12)

A Linux-only catalog service, **Syslog collector** (`syslog_collector`), gives a lab a syslog server to send to: for LABaPe's own audit trail (§21.5) and for testing other projects' syslog clients (LogaPe's syslog sink was the first). It follows the service shape of §21.4: packages and systemd units, plus one new optional field.

| Area | As built |
|---|---|
| Definition | `{"linux": {"packages": {"dnf": ["rsyslog", "rsyslog-gnutls"], "apt": ["rsyslog", "rsyslog-gnutls"]}, "units": ["rsyslog"], "role": "syslog_collector"}}`. No `windows` section, so the deploy form offers it only to Linux groups and the API refuses it on Windows ones. |
| `linux.role` | New optional service field: the name of an Ansible role in `ansible/roles` that configures what packages and units can't. Only roles on an allow-list may be named: `SERVICE_ROLES` in `app/labape/catalog.py` (validation) and the same list in `site.yml`'s play (enforcement), so an edited database row can't run another role. A Linux section now needs packages, units and/or a role, and takes no other keys. |
| When it runs | A new play, "Roles for the selected services (Linux)", after the platform certificates (§22.5), because a role may need the host's certificate. `labape_services` still installs the packages and enables the units first. The CLI path sets no services, so the play does nothing there. |
| Catalog | Seeded on a new install (`catalog.SEED`); migration `0006_syslog_collector` publishes it in a catalog seeded before it existed. Nothing is added to an empty catalog (the first-start seed does that) or when the name is taken. |
| Listeners | rsyslog: UDP 514 (RFC 5426); TCP 514 (RFC 6587, octet-counted or LF); TLS 6514 (RFC 5425, server certificate only); TLS 6515 (client certificate signed by the platform CA required). TLS is 1.2/1.3 only (RFC 9662; GnuTLS priority `NORMAL:-VERS-ALL:+VERS-TLS1.3:+VERS-TLS1.2`). Without a platform certificate (a CLI deploy) the TLS listeners are left out. |
| Certificates | `/usr/local/sbin/labape-syslog-collector-certs` copies the host's platform certificate into `/etc/labape/syslog-collector/` (key readable by rsyslog's group, for Ubuntu's privilege drop) and builds `ca.pem` from the root and the intermediate, so a client certificate that arrives without its intermediate still verifies. `labape-syslog-collector-certs.path` reruns it and restarts rsyslog when `labape-cert-renew` replaces the certificate. |
| Storage | Reverse DNS for senders is a role option, `syslog_collector_dns_lookup`, off by default (`net.enableDNS="off"`): the lines record the address, and lookups delayed TCP messages. Set it in inventory to resolve names. Remote messages go through their own ruleset (never into the host's own logs): `/var/log/remote/all.log` and `/var/log/remote/<host>/<app>.log`, one line per message showing the parsed header (`pri=<facility>.<severity> app= procid= msgid= sd= msg=`) so a sender's format can be checked. Kept 14 days (logrotate). |
| Host setup | SELinux: TCP 514 becomes `syslogd_port_t` (it's `shell_port_t` by default) and 6515 `syslog_tls_port_t` (`community.general.seport`). The ports are opened in firewalld or ufw when either is running. `rsyslogd -N1` checks the configuration before every restart. |
| LABaPe's own audit trail | Settings → Syslog sends to the collector over TLS with verification on: the sender trusts the platform root as well as the system roots, requires TLS 1.2 or later, and ends each connection with close_notify (before, a reset could drop the message). The lab sends its audit trail to `syslog1:6514` (`live/syslog_settings.py`). |
| Verified | Live on the lab host (environment `syslog1`, Rocky 9, SELinux enforcing): the deploy passed (55 tasks) and a redeploy changed only the edited configuration. All four listeners were up on IPv4 and IPv6, `rsyslogd -N1` was clean, and firewalld had the ports open. LogaPe (5.1 and 7, from the development machine) sent over UDP (RFC 5424 and 3164), TCP (octet-counted and LF), TLS (pinned, skip-check) and mutual TLS with a client certificate from `labape ca-sign`, and each line showed the right facility, severity, app, PID, MSGID and structured data. TLS without a trusted root, and mutual TLS without a certificate, were refused. Reverse DNS is off by default (`syslog_collector_dns_lookup`): it held TCP messages back by seconds (Testing_Troubleshooting-Log.md). pytest: 135. |

## 28. VM operations, admin area, host details and remote access (planned 2026-10-12)

The plan, options and decisions are in [Planning_Operations-And-Admin-UI.md](Planning_Operations-And-Admin-UI.md) (batches O1-O6, questions 107-120). This section records each batch as built.

### 28.1 O1 as built: Admin area, Jobs and Errors filters, Catalog requests

| Area | As built |
|---|---|
| Admin area | One **Admin** button in the top bar (admins only) opens `/admin`, with a list on the left: KVM hosts, Networks, Settings, Secrets, Audit log, User errors (question 107). `web/src/pages/Admin.tsx` (`AdminLayout`) wraps them; non-admins get "for admins". The old URLs (`/hosts/3`, `/settings?tab=x`, …) redirect to the same page under `/admin`. The APIs are unchanged: the deploy form still reads hosts and networks for non-admins. |
| Filters | `web/src/components/Filters.tsx`: filter values live in the URL (bookmarkable, back button works), applied with **Filter** or Enter, **Clear** resets; date ranges are browser-local and sent as UTC. |
| Jobs | Filters: environment (name contains), type, state, requested by, created from/to. Environments show by name, with "(destroyed)". `GET /api/jobs` takes the same filters after the visibility rule (own jobs plus those of visible environments), so a filter can't reveal other jobs; `GET /api/jobs/facets` gives the filter values from the jobs the user can see. |
| Audit, User errors | The same filter bar. User errors: user, where (path or page contains), message contains, source (server/browser), HTTP status, from/to, and **Export JSON** (`/api/admin/errors/export`, audited as `errors.export`), like the audit log. Audit gains the date range in the page (the API had it). |
| Catalog requests | The Requests tab has a short description and a **New request** button (the request form with a kind selector). |
| Verified | pytest 138 (filters, names, facets, visibility for a deployer without grants, error filters and export). Live on the lab stack: the UI builds (type-check); admin and `labape-dev` job lists and facets through `live/jobs.py` (the developer sees only their own jobs and `lab2`'s). |

### 28.2 O2 as built: Files filters, tab descriptions, installer OS targets

| Area | As built |
|---|---|
| Descriptions | Each Files tab (ISOs, Installers, Other files, Templates) starts with a sentence saying what its files are for. |
| Filters | Every tab: name contains, size range (MB), SHA-256 prefix, description contains, date added (from/to); Installers also by target; Templates by name, OS, KVM host and description. Client-side (the lists are small), kept in the URL with the shared filter bar (§28.1). |
| Targets (question 112) | `files.targets` (migration `0007`): a list mixing generic (`any`, `windows`, `linux`), families (`windows-server`, `windows-client`, `rhel-family`, `debian-family`) and exact OS keys (`rocky9`, `windows_server_2022`, …), defined in `app/labape/ostargets.py` and served by `GET /api/files/targets`. Uploads, URL downloads and the folder scan set a starting value from the extension (MSI/EXE → windows, DEB → debian-family, RPM → rhel-family, else any); existing installers got the same from the migration. An empty list matches nothing, so an unclassified installer is never used silently. The file page edits them with a grouped picker; the list shows them as chips. |
| Enforcement | The catalog refuses an application whose local Windows (or Linux) installer targets no Windows (or Linux) OS. Deploy forms refuse an application on a host group whose OS its local installer doesn't target, naming the installer and its targets. |
| Verified | pytest 141 (matching rules, API validation, catalog and deploy refusals, migration 0007). Live: migration applied on the lab stack, the UI builds, `live/files.py --targets` lists the vocabulary (the lab has no installers yet). |

### 28.3 O3 as built: VM power and start order

| Area | As built |
|---|---|
| Actions (questions 108, 111) | Start, Shut down, Restart and Force off, for one VM or the whole environment, by owners and admins (`POST /api/environments/{id}/power`, audited as `environment.power.<action>`). Each is an `environment.power` job the worker runs itself with `virsh --connect`, so remote KVM hosts work the same way (`app/labape/engine/power.py`). One job per environment at a time, as for deploys. |
| Clean shutdown | `virsh shutdown --mode agent,acpi`, then waits up to the timeout (default 300 s, 30-3600). A VM still running fails the job and the message offers Force off, unless the request ticked "Force off after the timeout". Force off is `virsh destroy`. |
| Start order (questions 109-110) | Host groups gain `start_order` (1-99; unset = domain controllers 1, everything else 2), `start_wait` (`agent`: wait until each VM answers a guest-agent ping, or `none`) and `start_delay_seconds`. A start goes order by order (groups with the same number together, the longest delay and "wait" if any of them waits); a VM without an agent channel isn't waited for; an agent that never answers is logged after the timeout and the start goes on. Shutdowns go in reverse order and stop at the first group that won't shut down, so servers stay up for clients that are still running. `PUT /api/environments/{id}/start-order` edits it without a redeploy; the runner keeps these keys out of OpenTofu's input. Staggering also spares the KVM host. |
| States | `GET /api/environments/{id}/vms`: each VM's state and guest-agent answer from the host's last stats, its group and start order. |
| UI | Environment page → **VMs**: the state table with per-VM buttons, the whole-environment buttons, "Force off after the timeout" and the timeout, and the start-order editor (owners). |
| Verified | pytest 146 (ordering, waits, delays, reverse shutdown, stuck VMs with and without forcing, API permissions, busy, OpenTofu input). Live on `syslog1`: shut down (9 s), start with the agent wait (18 s), restart (27 s), force off and start; rsyslog came back. `live/power.py`. |

### 28.4 O4 as built: KVM host access over SSH, details sections, metrics, install

| Area | As built |
|---|---|
| Access account (questions 113-114) | Each host gets a LABaPe access account (default `localadmin01`), separate from the service user, reached with its own key (`labape_hostadmin` in the worker home, separate from the libvirt key). **SSH access → Set up access** takes an existing admin login once: the worker signs in with it (the password goes to `ssh` through `SSH_ASKPASS` and to `sudo -S` on stdin, never on a command line, in a file that outlives the call, or in a log or the audit trail), creates or refreshes the account (key-only: password locked; authorized key; `/etc/sudoers.d/labape-<account>` with passwordless sudo, checked with `visudo -c`; added to `libvirt`), then signs in as it to check `sudo -n`. **Check** repeats the last part. A host whose sshd allows only keys refuses the password, and the error says so. Columns `ssh_address`, `ssh_port`, `ssh_user`, `access_status`, `access_error`, `access_at` (migration `0008`). |
| What's read (question 115) | `engine/hostadmin.py`, run on the host with its `python3` through `sudo -n`: facts hourly and on **Refresh now** (lsblk tree, filesystems, LVM, ZFS, mdraid, NICs with kind, speed, bridge membership and driver, `sensors -j` or thermal zones, OS release, kernel, CPU, memory, uptime, load, pending updates, reboot required, time sync, failed units, accounts with groups, admin and locked flags, groups with members, which tools exist); SMART daily (`smartctl --scan-open`, health, temperature, hours, reallocated/pending, NVMe wear); metrics every 5 minutes, kept 7 days in `host_metrics` (CPU counters, memory, filesystem use, temperatures, NIC byte counters). `host_facts` keeps the latest facts and SMART per host. Hosts are watched whether or not they're enabled for deploys. |
| Details page (R11) | Admin → KVM hosts → a host: sections on the left: **Dashboard** (warnings: SMART failures and wear, reallocated sectors, heat ≥ 85 °C, filesystems ≥ 90 %, failed services, reboot required, many updates, smartctl missing; key facts; 24-hour sparklines for CPU, memory, fullest filesystem, hottest sensor, network in/out; temperatures), **Drives and volumes**, **NICs**, **OS**, **Accounts**, **Groups**, **VMs**, **SSH access** (set up, check, Prepare this host), **Settings** (the earlier form, networks and load). |
| Accounts and groups (question 120) | Admins add accounts (with groups and an optional SSH key), set groups, set a password (sent once with `chpasswd` on stdin, never stored), lock, unlock and delete; add and delete groups and add or remove members. Root and the access account can't be locked or deleted. Each change is audited (`host.user.*`, `host.group.*`) and the facts are re-read. |
| Prepare this host (R9, question 114) | `host.install` job: copies only what the kvm profile reads (`hostadmin.INSTALL_FILES`: the installer without its update agent, rollback script, state or answer files; `secrets.vault.example.yml`; `tofu/environment.example.yml`; the two manifest examples; `scripts/lib` for the render check). Jobs bring their own workspace (tofu, ansible, packer, scripts) to `~/.labape/jobs/<id>`, so the host doesn't need those trees. Before copying, a re-run removes everything under the install folder except the installer's progress and the configuration it generated (`INSTALL_KEEP`), so older, fuller copies don't linger. `TemplatesToBuild` is always empty (templates are built from the web). The copy goes to `/opt/labape-kvm` on the host (updated in place, so the installer's progress, the host's vault and environment.yml stay), creates the service user if needed and hands it the copy (it runs ansible-vault and the CLI checks), writes the answers from the form (service user, paths, networks; no secrets) with the worker's libvirt key as `WorkerPublicKey`, and runs `install-labape.py --profile kvm --non-interactive` as root through the access account, streaming its output into the job log. On success the host's libvirt URI becomes `qemu+ssh://<service user>@<address>/system`. Install jobs run on hosts that aren't enabled yet; other jobs still wait for **Enabled**. |
| Long worker-run jobs | `runner.beat()` keeps the heartbeat of jobs the worker runs itself (power, install) and reports Cancel; before, a job longer than two minutes was marked stale and failed. |
| SMART tools (2026-10-13) | Disk health needs `smartctl`: the installer's `Prereq.Packages` installs `smartmontools` on every KVM-side profile, and **Set up access** installs it too (apt or dnf, best effort, reported in its output), so a host set up without the installer gets it. |
| Verified | pytest 151. Live on a throwaway Debian 13 VM, `kvmtest1` (environment 12, nested KVM), registered as host 3: Set up access (its sshd allowed keys only, so the test turned password sign-in on for the run; `live/host_access_vm.sh`) created `localadmin01`; every section read real data (disks, filesystems, NICs, OS, accounts, groups; smartctl absent was flagged); account and group changes worked and the access account was refused for deletion; metrics accumulated every 5 minutes. **Prepare this host** ran the whole kvm profile (packages, libvirt, Ansible, OpenTofu, Packer, vault, environment.yml, the worker's key) and then the host passed **Check** over `qemu+ssh` with no problems; a re-run took 20 s. Four bugs found on the way and fixed (Testing_Troubleshooting-Log.md). `live/host_access.py`. |

### 28.5 O5 as built: SSH and RDP in the browser, session log, recordings, auditors

| Area | As built |
|---|---|
| Gateway (question 116) | `labape-guacd` (`guacamole/guacd:1.6.0`) in the stack, on the internal network only. LABaPe is the tunnel (`app/labape/remote.py`, `api/remote.py`): the browser runs `guacamole-common-js` 1.5 (an npm build of Apache's client library) and opens `wss://…/api/remote/tunnel`; the API does the Guacamole handshake with guacd (`select`, `args`, `size`/`audio`/`video`/`image`/`timezone`, `connect` with the target's address and credentials filled in server-side, `ready`) and relays whole instructions both ways, answering the client's internal pings itself. Credentials never reach the browser. |
| Opening a session | `POST /api/remote/sessions` checks who may open what (question 118: an environment's VMs for its owners and admins; KVM hosts for admins), resolves the target, and returns a one-time token (stored hashed, cleared on use, valid two minutes). The tunnel accepts it only from LABaPe's own origin and with the session cookie of the user it was issued to. The token travels in the navigation state, never in a URL. |
| Targets | Windows VMs over RDP as the inventory's account (domain Administrator on a DC, else Administrator), NLA, certificate ignored (the VMs' own self-signed RDP certificates); Linux VMs over SSH as the admin user with the VM key; KVM hosts over SSH as their access account with the host-admin key (§28.4). **Download .rdp** gives a native client the address and user only; it asks for the password. |
| Remote Desktop on VMs | New role `windows_rdp`, in its own play for every Windows host (domain controllers too): Remote Desktop allowed, Network Level Authentication required, the built-in firewall rule group on (by resource string, any display language). `windows_rdp_enabled: false` in inventory leaves a host alone. Before this, LABaPe's Windows VMs refused RDP. |
| Idle and recording (question 117) | Settings → Remote access: record every session (off by default), close after N idle minutes (no keyboard or mouse input; default 30), keep recordings N days (default 90; the worker's daily purge). guacd writes the screen (not keystrokes) to the `labape-recordings` volume as `session-<id>`; the session page shows "recorded". |
| Session log and auditors (question 118) | `remote_sessions` (migration `0009`): who, what, protocol, address, state, start, end, why it ended, client address, recorded. A new **auditor** role (default group `labape-auditors`) reads the audit log and Admin → Remote sessions, plays recordings in the browser (`Guacamole.SessionRecording`; each play is audited as `remote.recording.view`) and downloads them; it can't open sessions or change anything. Auditors see only those two Admin pages. |
| UI | Environment → VMs: **RDP** and **.rdp** on running Windows VMs, **SSH** on Linux ones (owners and admins); a KVM host's sections: **Open SSH**; `/remote/<id>`: the full-width session with Disconnect and the state; Admin → Remote sessions: filters, the log, Play. |
| Verified | pytest 157 (protocol framing, the handshake against a fake guacd, a guacd error ends the session as failed, targets and credentials, permissions for owners, viewers and auditors, origin and token checks on the tunnel, .rdp files, recordings purge). Live on the lab, through the real tunnel with the session cookie (`live/remote_session.py`): SSH to `syslog1` and to the KVM host `kvmtest1`, RDP to a new Windows Server 2022 VM (`rdptest1`, deployed with `windows_rdp`); each drew its screen and was recorded (valid `.guac` files); RDP to `lab2`'s older `l2win1` was refused, as Remote Desktop was off there before this role (a redeploy turns it on). The session page and the recording player were type-checked by the image build, not driven in a browser. |

### 28.6 O6 as built: VM details page, accounts and groups, consoles

| Area | As built |
|---|---|
| Details page | `/environments/<id>/vms/<vm>`, linked from the VM names in Environment → VMs, with sections on the left like a KVM host's: Dashboard, Disks and volumes, NICs, OS, Accounts, Groups, Console. `GET /api/environments/{id}/vms/{vm}` returns the state and size from the host's stats, 24 hours of the VM's load samples, and everything the QEMU guest agent reports: the worker's stats now keep a full `virsh guestinfo` per running VM (OS, host name, time zone, users signed in, filesystems with sizes, disks, interfaces with addresses). No login to the VM. Anyone who may see the environment sees the page; address, accounts and consoles are for its owners and admins. |
| Accounts and groups (question 120) | Read live and changed by the environment's owners and admins (`GET`/`POST …/vms/{vm}/accounts`, `engine/vmadmin.py`), every change audited as `vm.<op>`, passwords never logged. Linux: SSH as the environment's admin user with the VM key, reusing the KVM host's collector and its account commands (`hostadmin.manage_over`); `root` and the admin user can't be deleted or locked. Windows: WinRM through a one-task Ansible play as the inventory's account (`win_powershell` with `Get-LocalUser`/`Get-LocalGroup` to read; `win_user`, `win_group`, `win_group_membership` to change); `Administrator`, `Guest`, `DefaultAccount` and `WDAGUtilityAccount` are protected, built-in groups (SID `S-1-5-32-*`) can't be deleted. A domain controller has no local accounts and says so. The image has no JSON stdout callback, so the play writes its registered result to a local 0600 file. The host page's account and group tables became shared components (`AccountsTable`, `GroupsTable`) taking the endpoint as a prop. |
| Consoles (question 119) | Through the environment's KVM host, as its LABaPe access account (§28.4), so they need that set up. **Serial** (Linux VMs; the templates run a getty on `ttyS0`): guacd's SSH to the host with `command=sudo -n virsh -c qemu:///system console <vm> --force`. **VNC** (Windows VMs, and Linux VMs with the `linux_workstation` role): libvirt's VNC listens on the host's loopback with no password, so the tunnel looks up the port (`virsh vncdisplay`), opens a one-shot listener in the API container that accepts one connection, only from guacd, within 30 seconds, and pipes it through `ssh -W 127.0.0.1:<port>` to the host; guacd connects to `labape-api:<port>`. This replaces the plan's per-VM VNC password: the display is never reachable off the host. Sessions use protocol `serial` or `vnc` in the session log, with the same audit, idle timeout and recording as O5. **virt-viewer** (optional): the Console section shows a copyable `virt-viewer --connect qemu+ssh://<access account>@<host>/system <vm>` command for people whose own key the access account accepts; LABaPe doesn't audit those. |
| Fixes on the way | `parse_guest_full` read an empty value (a Windows loopback's `hwaddr`) as the next line; the key/value pattern no longer crosses lines. |
| Verified | pytest 163 (guest info parsing, the details endpoint and console choices, console targets and the host-access requirement, owner-only accounts, Linux account commands over a fake SSH, Windows tasks through a fake Ansible run with protected accounts and `no_log`, the VNC forwarder piping one connection through a stand-in `ssh -W`). Live on the lab (`live/vm_details.py`, `live/remote_session.py`): details for `syslog1` (Rocky 9) and `rdptest1` (Windows Server 2022) with filesystems, NICs and signed-in users from the agent; accounts and groups read on both; a full round trip (add account and group, add member, lock, unlock, set password, delete both; protected delete refused; the password absent from the audit log) on `kvmtest1` over SSH and `rdptest1` over WinRM; a serial console on `syslog1` and a VNC console on `rdptest1` through the real tunnel, both drawing until closed. The page itself was type-checked by the image build, not driven in a browser. |

## 29. Stack services: syslog collector, installer answer file, primary file host (planned 2026-10-13)

The plan, decisions and open issues are in [Archive_Planning_Stack-Syslog-Installer-File-Host.md](Archive_Planning_Stack-Syslog-Installer-File-Host.md) (batches Y1-Y3, questions 121-141). This section records each batch as built.

### 29.1 Y1 as built: the stack's syslog collector

| Area | As built |
|---|---|
| Containers (questions 121, 136) | `labape-syslog`: Debian 13 rsyslog 8.2504 with `imudp`, `imtcp` (plain and TLS through the `ossl` driver), `mmutf8fix` and `ompgsql`, from `container/syslog/`. `labape-syslog-db`: `postgres:17-alpine` with `listen_addresses` empty, so only its Unix socket exists, shared through the `labape-syslog-run` volume with the collector, the API and the worker. Its first start (`db-init.sh`) creates the `syslog` database owned by `labape_syslog` (LABaPe: creates the table, reads, purges) and `rsyslog_writer` (insert only), and a `pg_hba.conf` that allows only those two (and the superuser) on the socket. With no TCP listener the roles need no passwords, so no new bootstrap secrets (design §26) were needed; the superuser uses the existing `postgres_password`. |
| Listeners (question 122) | UDP and TCP 514 and TLS 6514, published on `LABAPE_SYSLOG_BIND` (default every address). TLS is added by the entrypoint once the certificates exist; `StreamDriver.AuthMode` is `x509/certvalid` (client certificate signed by the platform CA required) unless Settings turns that off (`anon`). kvm1 had nothing on these ports. |
| Storage, retention (question 138) | One row per message in `syslog_messages` (received and reported time, sender address, host name, facility, severity, app name, proc id, msg id, structured data, message), written by an `ompgsql` action with a disk-assisted queue, so a database restart loses nothing. No plain files. The worker's daily purge deletes rows older than the retention (30 days by default, Settings; the user changed it from 90 after seeing the volume). |
| LABaPe's side (`labape/syslogstack.py`) | Every minute in the worker: the table, indexes and grants; the collector's server certificate (names `labape-syslog`, `localhost` and the collector address) and LABaPe's client certificate from the platform CA, 90 days, renewed 10 days before or when the address changes; and the rendered settings in `labape-syslog-conf` (`forward.conf`, `client-certs-optional`). The API renders too when the settings are saved. The collector's entrypoint watches the settings and certificates, checks a changed configuration with `rsyslogd -N1`, and restarts rsyslogd (it doesn't reload on HUP); a configuration that doesn't validate is reported and the running one kept. The worker's last check (schema, certificates, error) is stored for the Syslog tab. |
| Forwarding (questions 123, 139) | Admin → Settings → Syslog collector: up to 10 targets, each UDP, TCP or TLS (verify the server or not; optionally present LABaPe's client certificate), RFC 5424 (octet-counted over TCP) or 3164, filtered by facilities and a minimum severity. Each target has its own disk-assisted queue. Names and hosts are validated, so nothing reaches rsyslog's configuration unchecked. |
| Syslog tab (question 137) | `/syslog`, admins and auditors: filters for host, sender address, facility, minimum severity, app, text and time; newest first, **Older** pages back, **Live** adds new messages every 5 seconds; a row opens to its reported time, msgid and structured data. `GET /api/syslog/messages` and `/api/syslog/status` (the row count is PostgreSQL's estimate). |
| The audit trail | The sender (Settings → Syslog (audit trail)) can present LABaPe's client certificate. An install whose audit syslog was never configured is pointed at `labape-syslog:6514` over TLS with the client certificate the first time the worker finds the collector; saved settings, even "off", are left alone. This lab moved from `syslog1` to the stack collector; `syslog1` stays for testing (question 125). |
| KVM hosts (question 124) | **Logs to the syslog collector** on a host's SSH access section, and **Send its logs to the syslog collector** in the Prepare form (default on): through the access account, LABaPe installs rsyslog with its OpenSSL driver if needed (apt or dnf), puts a client certificate for the host (names: host name and SSH address), the platform root and the forwarding action (TLS 6514, disk-assisted queue) under `/etc/rsyslog.d/`, checks it with `rsyslogd -N1` and restarts rsyslog. The certificate is renewed by the worker 10 days before it expires. Migration `0010` adds `log_forward`, `log_forward_until` and `log_forward_error` to `kvm_hosts`. |
| Installer | No new step: the services come up with the stack (the update agent's `compose build` and `up -d` add them on update), and nothing needs a secret. Asking for `LABAPE_SYSLOG_BIND` and choosing the service belong to Y2. |
| Verified | pytest 177 (forward actions for every transport and filter, rendering only on change, certificate issue and renewal, settings validation, the API's roles, host forwarding on and off and its failure kept, adopting the audit trail once). Live on the lab: UDP and TCP stored; TLS without a client certificate refused (`certificate required`), then accepted once certificates were made optional; forwarding to a test listener over UDP, TCP and TLS; the audit trail over TLS with LABaPe's client certificate; kvm1 (Debian) and garagepc (Ubuntu 24.04) forwarding their own logs; the filters and the purge against the real database; the collector restarting on settings changes. Two bugs found on the way (Testing_Troubleshooting-Log.md). The pages were type-checked by the image build, not driven in a browser. |

### 29.2 Y2a as built: answers first, a reused answer file, chosen services, leaner web-driven KVM installs

| Area | As built |
|---|---|
| Answer file (question 141) | Every run reads `deploy/state/answers.<instance>.json` (`--set` and `--config-file` still win), so a re-run or an update asks only questions added since; `--fresh` asks everything again. All questions come before any step; when any was asked, the run prints every answer (new ones marked `*`) and waits for a yes, and only then saves the file. The file never holds a secret. |
| Services (question 140) | New answers, each default yes and asked only for the web stack: `InstallConjur`, `InstallSyslog` (plus `SyslogBind`, default the host's address), `InstallRemoteAccess`, `InstallUpdateAgent`, `InstallAuthentik`. **OpenBao is always installed**: the update agent's login, the boot-time unseal and the bootstrap secrets depend on it, so it is the store "at least one" means; `PlatformStore` is asked only with Conjur. New step `Stack.Services` writes `container/compose.override.yaml` putting the services not chosen under a profile nobody enables (Compose merges the file by itself, so every `compose up`, the update agent's included, honours it), and `LABAPE_CONJUR` / `LABAPE_SYSLOG_BIND` in `.env`. An install without the file runs everything, so existing installs are unchanged. `stores.sh` starts, configures and reports only OpenBao when `LABAPE_CONJUR=no` and refuses Conjur as the platform store then; `labape stores-configure --check` reports a store that isn't set up as such rather than failed. `syslogstack.present()` now looks for the database socket, since the volume is mounted even when the collector is left out. |
| Sign-in (question 130) | `InstallAuthentik` (default yes; no when an older answer file named another `AuthMode`) makes the stack's Authentik the sign-in provider (`tools/authentik-test`, kept). Otherwise `AuthMode` is asked, defaulting to `test-authentik`: a throwaway Authentik for the first sign-in. |
| Web-driven KVM installs (questions 126, 127, 129) | `CliCheckout` (default no on the kvm profile, always on for full and web): without it `Config.Secrets`, `Config.Environment` and `Config.Manifests` don't run and their questions (domain, NetBIOS name, management source) aren't asked, and `Smoke.Cli` checks libvirt, Ansible and OpenTofu only. `InstallCli` is now a question. `TemplatesToBuild` is answer-file only. The Prepare form picks networks from Admin → Networks and asks only the host's part of each (bridge, NIC, the bridge's address); the API builds the installer's network answers from the catalog (gateway defaulting to the network's first address), and after a successful install the host gets those attachments (the first, or the chosen default, as its default network). |
| Fixes on the way | `container/update` was created by Docker as root on a fresh install's first start, so `Stack.UpdateAgent` failed: `setup.sh` makes it as the service user and the step fixes its owner. The image was stamped `dev` because `git describe` ran as root on the service user's checkout ("dubious ownership"): it now passes `safe.directory`. |
| Verified | pytest 183 (the override, defaults per profile and for old answer files, conditional questions, the summary, Prepare from the catalog with its errors, attaching networks once, `stores.sh` without Conjur). Live: **Prepare on garagepc** from catalog networks: the three Config steps not applicable, the network attached, log forwarding renewed (the test attachment was removed again). **A fresh web-profile install on `stack1`** (Debian 13 VM, environment 14) from an answer file with Conjur and remote access left out and Authentik installed: OpenBao-only stores, the update agent, the stack's Authentik as sign-in provider, the syslog collector on the VM's address only, Conjur and guacd parked by the override; it stopped at the update agent (the root-owned folder, fixed), and `--resume` with no answer file reused the saved answers and finished; a dry-run re-run needed no answers; the fresh install's audit trail adopted its own collector. |

### 29.3 Y2b as built: RHEL-family KVM hosts (question 128)

| Area | As built |
|---|---|
| Scope | The **kvm profile** (what Prepare runs) on Rocky and Alma 9 or later. The full and web profiles still need Debian (the stack's runtime steps use apt); `Prereq.Os` says so. |
| OS detection | `steps.os_release()` reads `/etc/os-release` itself: `platform.freedesktop_os_release()` needs Python 3.10, and RHEL 9's `python3` is 3.9 (the installer code is kept 3.9-compatible; a test parses it with the 3.9 grammar). `os_family()`: `debian`, `rhel` (ID or ID_LIKE rhel, centos, fedora, rocky, almalinux) or unsupported. |
| Packages | dnf names for the same tools (`qemu-kvm`, `libvirt`, `virt-install`, `xorriso`, `python3-libvirt`, …, `smartmontools`), plus `policycoreutils-python-utils` (SELinux labels) and `dnf-utils` (`needs-restarting`); on 9.x also `python3.12`, because ansible-core needs 3.12 or later. The Ansible venv is built with `python3.12` there. Installed checks use `rpm -q --qf %{NAME}` (a missing package prints its message on stdout). |
| libvirt | Where `virtqemud.socket` exists, the modular daemons' sockets are enabled (`virtqemud`, `virtnetworkd`, `virtnodedevd`, `virtnwfilterd`, `virtsecretd`, `virtstoraged`, `virtinterfaced`, and `virtproxyd` for qemu+ssh clients), including their `-ro` and `-admin` sockets; otherwise `libvirtd` as before. |
| SELinux | When SELinux is enforcing or permissive, `Storage.Dirs` labels the VM and template folders `virt_image_t` and the ISO folder `virt_content_t` (`semanage fcontext`, then `restorecon -R`). |
| Host collector, Prepare | Facts on dnf hosts also read `needs-restarting -r` for "reboot required". Prepare installs `tar` first when the host lacks it (minimal RHEL images do). |
| Verified | pytest 185 (OS family, package lists per version, os-release parsing, the 3.9 grammar, the tar step). Live on `rkvm1`, a Rocky 9.8 VM with nested KVM and SELinux enforcing (environment 15, registered as host 3): Set up access (the access account, smartmontools by dnf), then Prepare from a catalog network on a pre-made bridge with service user `kvmsvc`: packages by dnf, modular libvirt, SELinux labels, Ansible on Python 3.12, OpenTofu and Packer, no command-line setup; then **Check** over `qemu+ssh` passed (QEMU 10.1.0, every tool and path found), the facts showed Rocky with dnf's update count, and its logs reached the collector. Deploying a VM onto it needs a template there first, which is Y3's file distribution. |

### 29.4 Y3a as built: the primary file host and copies to the other hosts

| Area | As built |
|---|---|
| Primary (questions 131, 135) | `kvm_hosts.is_primary` (migration `0011`, which makes the host the stack runs on, `qemu:///system`, the primary of existing installs). There is always exactly one: the first host registered becomes it, naming another moves it, and the flag can't simply be cleared. Admin → KVM hosts shows a **primary** chip; the host's Settings has **Primary file host**. |
| What a job on another host gets (question 132) | `engine/filedist.py`, called by the runner before a job runs on a remote host (it replaces the ISO-only sync): the ISOs the job installs from (`<vm storage>/.labape-isos`), the installers of the applications chosen in the environment (`<vm storage>/.labape-files/installers`; Ansible runs on that host, so `software_store_path` points there; before this, local installers didn't work for remote hosts at all) and the templates its host groups clone (into that host's library, then registered there). A copy streams host to host through the worker, lands as `.part` and is renamed when complete, and its size is checked. A file the host already has with the size (and SHA-256) LABaPe recorded isn't copied again. |
| Templates everywhere | The template list for a host includes the primary's templates it doesn't have (`from_primary`, shown as "copied from the primary file host at deploy" when creating an environment), and creating an environment accepts them. |
| Primary unreachable (question 135) | Settings → File distribution: how long a job waits for an unreachable primary (default 30 minutes), retrying every 30 seconds and saying so in the job log; then it fails. A host that already has the file doesn't need the primary at all. |
| Shares (question 133) | **Files from a share** on a host (off by default): its admin mounted the primary's file locations (NFS or SMB) at the same paths, so ISOs and installers are read there, after checking each exists. Templates are still copied into the host's own library. Mounting the share is the admin's job; LABaPe doesn't mount it or keep its credentials. |
| Fixes on the way | Scripts the jobs run on a host use that host's `python3`; on RHEL 9 that's 3.9, and `discover_dhcp_ips.py` failed on `str \| None` in a signature: the scripts using such annotations now import them from `__future__`, and a test keeps `scripts/` 3.9-clean. The installer's bridge step now also activates the bridge port: NetworkManager doesn't bring it up with the bridge, and an automatic "Wired connection" can hold the NIC. |
| Verified | pytest 190 (copy decisions: the primary itself, a share, a current copy, a copy, waiting for an unreachable primary then failing or recovering, a host that already has it; templates copied and registered, share or not; one primary; templates offered from the primary). Live: kvm1 became the primary through the migration; on **rkvm1** (the Rocky 9 host from §29.3, given a second NIC on the lab VLAN and a bridge on it), an environment from `debian13-packer-2026.10`, which only kvm1 had: the deploy copied the template's metadata and 2.0 GB image from kvm1 to rkvm1, registered it there, cloned the VM, and after the 3.9 fix the VM took a DHCP address on the lab network and Ansible configured it; the re-run didn't copy the template again. |

### 29.5 Y3b as built: the stack apart from the primary

| Area | As built |
|---|---|
| The file store (question 134) | `labape/filestore.py`: `LocalStore` when the stack runs on the primary (or there's no primary yet), as before; otherwise `RemoteStore`, which does every file operation on the primary over SSH as its service user (the user in its `qemu+ssh` URI, the login jobs use). The Files page's uploads append each tus chunk straight to a `.part` file on the primary (nothing is staged on the stack host), checksums and the final move run there; downloads from a URL stream to the primary's `.downloads/` and are checked there; the worker's scan lists the primary's locations and checksums new files there; deletes run there. An unreachable primary answers 503 on the Files page and leaves the list as it was. |
| File locations on the primary | Prepare, and making a host the primary, create LABaPe's file locations (Settings → file locations, e.g. `/data/OS_Images`, `/data/installers`, `/data/files`) on that host owned by its service user, through the access account. |
| Jobs on a stack host that isn't the primary | A job on the stack's own KVM host (`qemu:///system`) gets its files from the primary too, the same way remote hosts do (§29.4). |
| Templates back to the primary | A template built or refreshed on a host that isn't the primary is copied into the primary's library afterwards (image and metadata; the primary's template sync registers it), so the primary holds every file. A failed copy is a warning in the job log; the template stays built where it was. |
| Fix on the way | A URL download sent compressed (`Content-Encoding: gzip`) showed progress against the compressed length ("244%"); the total is now left out then. |
| Verified | pytest 194 (the remote store's listing and checksums, choosing the store by where the primary is, a tus upload landing on a remote primary and its delete, the file-location command, templates back to the primary). Live with **stack1** as a separate web stack (Debian 13 VM, §29.2) and the Rocky host **rkvm1** registered there, which became its primary: a 5 MB upload in 1 MB tus chunks landed in `/data/installers` on rkvm1 with a matching SHA-256; a download from a URL was saved on rkvm1; a file copied onto rkvm1 by hand was found by stack1's scan, checksummed there and its OS guessed; a delete removed the file on rkvm1; stack1's own `/data` stayed empty throughout. Not exercised live: jobs on a stack host that is a KVM host but not the primary, and a template build on a non-primary host (both covered by unit tests). |
| Open | ARM stack hosts (a Raspberry Pi): the image needs an arm64 build, and Conjur's image is believed to be amd64 only (the installer can leave Conjur out, §29.2). Not built yet. |

### 29.6 ARM stack hosts: the web stack on a Raspberry Pi

| Area | As built |
|---|---|
| The image | `container/Dockerfile` downloads step, OpenTofu and Packer for the build machine's architecture (`dpkg --print-architecture`), each checksum pinned per architecture (step's arm64 sum from its release `checksums.txt`; OpenTofu's and Packer's from their `SHA256SUMS`). Packer plugins and the OpenTofu provider mirror already follow the platform. Every other image in the stack has an arm64 build except Conjur's (`cyberark/conjur` is amd64 only). |
| The installer | `cpu_arch()` (amd64/arm64) picks the OpenTofu and Packer downloads. On ARM `InstallConjur` defaults to no, and `Stack.Services` refuses it (OpenBao is the platform store). A host without `/dev/kvm` gets `devices: !reset []` for the worker in `compose.override.yaml` (Docker won't start a container whose device is missing). `Stack.Runtime` uses a working Docker that came from elsewhere (Docker's own `docker-ce` packages) instead of adding Debian's conflicting `docker.io`. On Python older than 3.12 (Debian 12), `Tools.Ansible` installs ansible-core 2.19, enough for the vault on a web host (jobs run Ansible on the KVM hosts or in the image). |
| Always a primary | A new install's database is created at the latest schema, so migration 0011 never picks a primary, and `labape bootstrap` registers hosts directly: the Pi's stack had none, and its Files page fell back to local paths. `filedist.ensure_primary` (the stack's own host, else the first registered) now runs at the end of bootstrap and every minute in the worker. |
| Host access | Set up access adds the access account to the first group in sshd's `AllowGroups` when it isn't in one (Raspberry Pi OS images use `AllowGroups AllowSSH`), and warns when an `AllowUsers` list leaves it out. |
| Remote destroys | Deleting a VM's disk ran `sudo rm` as the host's service user, which has no sudo rule on hosts LABaPe prepares, so destroys there failed (found tearing down a test environment): it now tries a plain `rm` first (the VM folder is the service user's), `sudo` only if that didn't work. |
| Verified | pytest 196. Live: a **Raspberry Pi 5** (8 GB, NVMe, Debian 12 bookworm with Docker CE 28 already installed) at 172.21.21.16, reached through Set up access only (no Prepare): the web profile from the branch completed every step, building the image natively on arm64 (step, OpenTofu and Packer for arm64), with OpenBao alone (Conjur parked), the stack's Authentik, the syslog collector, guacd and the update agent; about 2.5 GB of RAM in use. The Pi's stack registered **garagepc** (handed over from the main LABaPe, where it's now disabled), its Check over `qemu+ssh` passed, garagepc became its primary, a 4 MB upload landed on garagepc with a matching SHA-256 and the Pi's disk stayed empty, a delete removed it there, and the Pi's audit trail went to its own collector. Not done: a VM deploy from the Pi, because garagepc has no VM network (its one NIC carries management; `br1` is an empty leftover from the old example answers). The amd64 build was rechecked in the lab. |
| Host name change (2026-10-14) | The Pi moved from its address to `director.jlm.abq` (opening the site by name had failed with Firefox's `SSL_ERROR_INTERNAL_ERROR_ALERT`: Caddy only had a certificate for the address). Three things made that a supported re-run (deploy/README.md, "Changing the web interface's host name"): `container/ca/init.sh` merges `LABAPE_CA_DNS` into `ca.json`'s names on every start, so step-ca answers to the new name (VMs renew at `https://<name>:9444`); `tools/authentik-test/deploy.sh` updates `LABAPE_HOSTNAME` in its `.env` and sets the provider's redirect URI (the blueprint creates the provider once, `state: created`, so a rotated client secret sticks, which also froze the URI); `Stack.Setup` skips the engine-secret copies once the platform store holds them. |
| A VM from the Pi | garagepc's management connection moved onto a bridge (`br0` over `eno1`, keeping its MAC so DHCP kept 172.21.20.151; done with a 3-minute automatic rollback that was cancelled once it answered; the leftover `br1` removed). The Pi's stack got a static-only network `lab-mgmt` (172.21.20.0/22, pool 172.21.23.240-172.21.23.254, the range the user dedicated) on `br0`, the Debian 13 template copied from kvm1, and deployed `pivm1` from the Pi: 172.21.23.240, Ansible with no failures, 140 s. |
| Web certificate (2026-10-14) | Chrome didn't trust the Pi's site: the installer's default `Tls=internal` had Caddy issue it from Caddy's own CA, a root nobody imports, while kvm1 had been switched by hand to `Caddyfile.labape-ca` (ACME from the platform CA). The installer's `Tls` answer now offers `labape-ca` and defaults to it (`Stack.Setup` sets `LABAPE_CADDYFILE`). Checked against Chrome's rules on the Pi after the switch: the leaf names the host in subjectAltName (Chrome ignores the CN), has the serverAuth EKU, an ECDSA P-256 key with SHA-256, a 24-hour validity (far under 398 days) and isn't a CA; the root is CA:TRUE with keyCertSign; the chain verifies against the root the Pi publishes at `/api/ca/root.crt`. Chrome trusts it once that root is in the OS store (on Windows: Trusted Root Certification Authorities). Each stack has its own platform CA, so the Pi's root is a separate import from kvm1's. |


## 30. Monitoring the stack hosts, and stacks watching each other (built 2026-10-14)

Questions 142-145 (Planning_Questions.md): a role per host with sections to match; the stacks watch each other; alerts on the dashboard, to syslog and by email; history as for KVM hosts; the access account through Set up access.

| Area | As built |
|---|---|
| Roles | `kvm_hosts.role`: `kvm`, `stack` (a LABaPe web stack only) or `both`, and `stack_url`, the stack's https address (migration `0012`; the host a stack runs on, `qemu:///system`, becomes `both`). A host's page shows **VMs** for KVM roles and a **Stack** section for stack roles; Prepare is hidden for stack-only hosts. The hosts list marks stack hosts. A stack host's record decides which stack it is: the one whose `stack_url` names this stack's own host (or, without an address, the local libvirt host) is this stack; the others are peers. |
| What's read | Every host with its access account gets the facts, metrics (every 5 minutes, 7 days) and SMART (daily) of §28.4. A stack host also has its containers read every 5 minutes (state, health, restart count; only LABaPe's compose projects, `labape` and `authentik-test`) and its volumes hourly, over the access account (`engine/stackmon.py`, `stack_status`). This stack checks itself every minute: the newest backup, the web, CA intermediate and syslog collector certificates, and its two databases' sizes. Every peer stack is probed every minute: `/api/health`, its response time, and its certificate. |
| Alerts (question 143) | Each minute everything wrong becomes an alert with a stable key (digits in the message don't change it): the host warnings of §28.4 (warning and error levels), stack hosts' temperatures and filesystems against Settings → Monitoring (80 °C and 85 % by default: a Pi 5 slows down at 85 °C), containers that aren't running, are unhealthy or restarted in the last 15 minutes, a backup older than 26 hours, certificates with less than 7 days or a quarter of their lifetime left (the web certificates last 24 hours and are renewed automatically, so only a failed renewal counts), and a peer that missed two probes in a row. `monitor_alerts` records when each started and cleared; a start or clear goes to syslog (through the audit trail's sender, `monitor.alert.started` / `.cleared`, warning or error severity) and, in one email per pass, to Settings → Monitoring's recipients. The dashboard shows open alerts, the latest cleared ones and each peer's state (`/api/dashboard/monitoring`, admins and auditors). |
| Fixed on the way | Compose merges a service's `profiles` with an override's, so the installer's parked Conjur kept its `stores` profile and any later `compose up` started it: on the Pi it crash-looped (an amd64 image on arm64). The override now uses `profiles: !override ["off"]`. |
| Verified | pytest 203. Live: kvm1's LABaPe watches the Pi as a peer (`pi1`, role stack, `https://director.jlm.abq`): its 12 LABaPe containers, volumes and probe (10 ms); the first pass's false alarms (other projects' exited containers, 24-hour certificates) led to the project filter and the lifetime rule and were recorded as cleared. The Pi's LABaPe checks itself (`director`, role stack) and probes kvm1. Stopping the Pi's guacd raised `pi1: container labape-labape-guacd-1 is exited` on kvm1's dashboard within a check and sent it to syslog; starting it again cleared it, also sent. Email wasn't tried live (no SMTP server set). The Pi's own container list waits for Set up access on its `director` record. |

## 31. Alert handling (M2, built 2026-10-14)

Batch 1 of the order in Planning_Backlog.md: acknowledging and silencing the alerts of §30, quiet during
maintenance, an alert when alert emails fail, and one kept-open syslog connection.

| Area | As built |
|---|---|
| Acknowledge | An admin marks an open alert as seen (`POST /api/dashboard/monitoring/alerts/{id}/ack`, `{"ack": false}` undoes it). The alert shows who and when, outlined instead of filled. It changes no announcement and ends with the alert: the same condition starting again is a new, unacknowledged alert. |
| Silence | An admin silences an alert's condition (its key, §30) for 1 hour to 30 days with a reason (`POST /api/dashboard/monitoring/silences`, `DELETE .../silences/{id}` ends it). No syslog or email for that key's starts while it lasts; the alert is still recorded and shown, marked "silenced until". `monitor_silences` (migration `0013`). |
| When a start is announced | `monitor_alerts.announced_at` (migration `0013`; existing rows were set to their start). A start is announced once, at the first pass where it's neither silenced nor in maintenance mode, so one that began in either is announced when that ends if it's still open; one that started and cleared inside it never is. A clear is announced exactly when its start was, silence or not, so whoever got the start also gets the all-clear. |
| Maintenance mode | No starts are announced while it's on (§23.3); the dashboard says so. Acknowledging and silencing stay allowed (`/api/dashboard/monitoring/` is in maintenance mode's allowed paths). |
| Email failures | `announce` returns the email's result; a failure is kept in the setting `monitor.email_state` and raises the alert "alert emails can't be sent: …" (key `monitor:email`, error). That alert goes to syslog only. While it's open the worker logs in to the SMTP server every 5 minutes without sending (`notify.smtp_check`); success, or email being turned off, clears it. |
| Syslog connection | The audit sender keeps one TCP or TLS connection to the collector (`notify._Stream`) instead of one per event, closes it after 60 seconds without events, reopens it when the settings change, and checks it before each send: readable data on the quiet connection is either the collector closing it (reconnect) or TLS 1.3 session tickets (read and kept). A failed send reconnects once. The Settings page's test send still uses its own connection. Backlog item from Testing_Troubleshooting-Log.md, Y2a item 3. |
| UI | The dashboard's Alerts panel: Ack/Unack and Silence (a dialog: how long, reason) on each open alert for admins, the active silences with End, and a note while maintenance mode holds announcements. Auditors see all of it, read-only. `tools/helpers/live/monitoring.py` gained `ack`, `unack`, `silence` and `end`. |
| Verified | pytest 209. Live on kvm1: stopping the Pi's guacd opened `pi1: container labape-labape-guacd-1 is exited` (announced); Ack and a 1-hour silence showed on it; starting guacd cleared it and the clear reached syslog under the silence; the ack and silence are audit events. The kept-open connection first reconnected on every event: `wrap_socket` detaches the plain socket, so the liveness check's `select()` on it failed (Testing_Troubleshooting-Log.md, M2 item 1). Fixed and re-run against the stack's rsyslog over TLS: 4 events on 1 connection, all 4 stored. Email failure is unit-tested only (no SMTP server in the lab). |

## 32. Full-stack backup (B1, built 2026-10-14)

Batch 2 of the order in Planning_Backlog.md. Before it, the daily backup was only LABaPe's database
(`engine/maintenance.py`), so a lost stack host meant a new CA, new secrets and a new Authentik.
This is the first half of the answer to "Authentik high availability" (too complex and fragile for a
lab; question 146's note): one archive that rebuilds the stack. B2 is the other half, the install
from it.

| Area | As built |
|---|---|
| Who takes it | The host's update agent (`action: full_backup`), running `deploy/stack-backup.sh` as the service user. Only the host reaches everything: the containers can't see `.env`, the bootstrap secrets, the stores' secrets, Authentik's separate compose project or the agent's own config. The agent takes one request at a time, so a backup never runs during an update or a rotation. Nothing stops, and maintenance mode isn't used: each database is a `pg_dump` (consistent on its own), and the volumes are copied live. |
| What's in it | `db/`: LABaPe, the syslog collector (setting, on by default), Authentik and Conjur (when they run). `volumes/`: the platform CA, OpenBao's data, LABaPe's data volume (job logs; not its own database dumps), the collector's certificates and Caddy's data; the session recordings by setting (off by default: they can be very large). `files/`: `container/.env`, the bootstrap secrets (`container/secrets/` or the memory-only `LABAPE_SECRETS_DIR`), the stores' secrets and certificates, `config/`, `engine-secrets/`, Authentik's `.env`, the installer's saved answers and `~/.config/labape` (without the backup key). `manifest.json`: version, commit, database revision, and every file's size and SHA-256. On kvm1: 35 files, 56 MB, about 30 seconds. |
| Encryption | The tar stream goes through `labape seal` inside the LABaPe image (`app/labape/seal.py`), so the host needs nothing but the container runtime: AES-256-GCM in 1 MiB chunks, each archive's key derived from the backup key with HKDF and a random salt, chunk numbers in the nonces and a last-chunk flag in the associated data, so a reordered, cut-short or extended archive won't open. |
| The backup key | 32 random bytes written as base32 in groups of four, made on the first run at `~/.config/labape/backup.key` (0600, `LABAPE_BACKUP_KEY` overrides). It's never in an archive and never passes through the API: Settings → Full stack backups shows the command that prints it (`sudo -u <service user> cat <key file>`) until an admin records that a copy is kept elsewhere ("I saved it", audited). |
| The check | After sealing, the archive is opened again and every file's hash compared with the manifest, and the LABaPe and Authentik dumps are loaded into a scratch database (`labape_restore_check`, dropped afterwards) in their own PostgreSQL. The result is in the archive's sidecar, `<archive>.json` (the manifest without the file list, plus size, check, key file and service user; no secrets). Exit code 3 means the archive was written but failed its check. |
| Where | `/data/backups/stack` by default (Settings → Full stack backups): a host path under the data root, which the containers see at the same path, so the web interface lists and downloads them. The data root is usually root's; the script makes the folder through the container runtime (the service user is already in the `docker` group) and gives it to the service user, mode 750. Archives older than the retention (14 days) are removed after a backup; the newest always stays. |
| Schedule | Daily at 03:00 UTC (after the 02:30 database dump) by the worker (`engine/stackbackup.py`), once per day: the time it asked is kept in the setting `backup.stack_requested_at`, and when the agent is busy it asks again on a later pass. Back up now does the same at once; 409 when the agent is busy or not installed. |
| Monitoring (§30) | This stack's self check reads the newest sidecar: "no full stack backup in the last 26 hours" (warning) and "the latest full stack backup (…) failed its check" (error). |
| API and UI | `GET /api/admin/backups/stack` (archives, agent, its status when the last action was a backup, the key file and whether a copy was recorded), `POST /api/admin/backups/stack`, `POST /api/admin/backups/stack/key-saved`, `GET /api/admin/backups/stack/{name}` (download, audited); all admin. Settings: a Full stack backups section above the database backups (renamed Database backups). `tools/helpers/live/stack_backup.py`. `agent.json` now records the service user and home. |
| Root's files | An installer run with sudo leaves Authentik's `.env` and its saved answers as root's 0600 files. The script reads what the service user can't through a root container, finds Authentik's PostgreSQL by its compose labels (compose would need that `.env`), and a part that's set up but missing fails the check. |
| Verified | pytest 220. On kvm1, by hand and then from the web interface through the agent: 14 parts, 35 files, 56 MB, the hash check clean, LABaPe (30 tables) and Authentik (230 tables) loaded into scratch databases. On the Pi (arm64, installed with sudo): 38 files including the installer's answers, both databases loaded. Three bugs found on the way (Testing_Troubleshooting-Log.md, B1). |

## 33. Install from a backup (B2, built 2026-10-14)

Batch 3 of the order in Planning_Backlog.md: the recovery path for a lost stack host, and what replaces
Authentik high availability (too complex and fragile for a lab). A new machine becomes the stack that
was backed up (§32): the same web address, platform CA, secrets, OpenBao, Authentik users and LABaPe data.

| Area | As built |
|---|---|
| Usage | `sudo deploy/install-labape.py --from-backup ARCHIVE --backup-key-file KEY [--restore-drill] [--set NAME=VALUE ...]`. The installer opens the archive itself (app/labape/seal.py loaded by path, with `python3-cryptography`, installed when missing) and takes the answers the stack was installed with (`files/installer/state/answers.*.json`) at lower precedence than `--set` and `--config-file`. An archive from a stack that wasn't installed with the installer (kvm1) has none: give them with `--config-file` or at the prompts. Templates aren't in a backup, so `TemplatesToBuild` is empty for the run (build them later). |
| Order | The normal install runs first (packages, Docker, a fresh stack, Authentik, OpenBao, the update agent), then the new step **Stack.Restore** (after Stack.UpdateAgent, before Stack.Bootstrap; NotApplicable without `--from-backup`) runs `deploy/stack-restore.sh`, which replaces the fresh stack's state. A fresh stack first, then a replace, costs a few minutes but uses only the installer's tested steps, and the same script can put a backup back onto an existing stack. |
| `deploy/stack-restore.sh` | As root (the installer leaves some of these files root's): (1) open the archive with the LABaPe image and check every file against the manifest; (2) refuse a backup whose database revision isn't in this checkout (newer than the code); (3) stop the stack and Authentik; (4) put back `.env`, the bootstrap secrets, the stores' secrets and certificates, `config/`, `engine-secrets/`, Authentik's `.env`, the agent's config and the backup key; each file keeps the owner and mode of the one it replaces, else the service user's 0600; (5) replace the volumes (with compose's labels, so `compose up` adopts them); (6) load each database into a fresh one (`DROP DATABASE ... WITH (FORCE)`, `pg_restore` keeping owners, which the fresh volumes' init scripts created) and set each role's password to the restored one; (7) `--maintenance`; (8) `stores/stores.sh unseal` as the service user (OpenBao opens with the restored unseal key, the memory-only secrets are refilled from it, the stack starts), Authentik up, then `/api/health` within 5 minutes and `labape health --deep`. |
| Host-specific values | Kept from the new host, not the backup: `LABAPE_SYSLOG_BIND` and `LABAPE_STORES_BIND` (addresses of this machine) and `LABAPE_SECRETS_DIR` (`/run/user/<uid>/labape-secrets`: the service user's uid may differ). The agent's `agent.env`/`store.env` get the old home and repo paths replaced (the manifest now records the service user, uid, home and repo). Answers that name the old machine's addresses (e.g. `SyslogBind`) are overridden with `--set`. |
| Passwords | The new install's PostgreSQL volumes were made with its own passwords; the restored secrets hold the old ones. After each load the role's password is set from the restored secret (`postgres_password` for LABaPe and the collector, `conjur_db_password`, Authentik's `PG_PASS`), so nothing needs the new ones. |
| Drills | `--restore-drill` (`--maintenance`) turns maintenance mode on before anything starts, so a copy on a second machine claims no jobs and runs no schedules (rotations, backups) against the real KVM hosts; it still reads their facts over SSH. Point the web address at the copy only on the copy (`/etc/hosts`), or both answer to the same name. `tools/helpers/live/relay_host_to_vm.sh` copies an archive (and the key) from a host to a test VM through the worker when they can't reach each other; only sizes and hashes are printed. |
| Verified | pytest (see the PR). Live drill: the Pi's (arm64) archive was rebuilt on a fresh Debian 13 amd64 VM (environment `restore1`, destroyed afterwards) with `install-labape.py --from-backup ... --restore-drill --set SyslogBind=0.0.0.0`; the archive and key went from the Pi to the VM through the worker (`relay_host_to_vm.sh`). The copy then matched the Pi: platform CA root fingerprint, LABaPe's hosts (garagepc, director, kvm1 with their roles), users, environments and the 53 audit rows before the backup, Authentik's 9 users, a web certificate from the same intermediate, OpenBao initialized and unsealed with the restored key, the same OIDC issuer; deep health passed and Smoke.Web saw both sign-in providers; maintenance mode on. The service user's uid differed (1005 on the Pi, 1000 on the VM) and the memory-only secrets folder followed it. Two bugs found (Testing_Troubleshooting-Log.md, B2) |

## 34. Linked stacks (L1, built 2026-10-14)

Batch 4 of the order in Planning_Backlog.md (review items 10, 11 and 13): two LABaPe stacks link with a
one-time code and read each other's summary. L2 builds the overall dashboard on it, B3 the backup
copies.

| Area | As built |
|---|---|
| The code | Admin → Linked stacks → Create link code on stack A (`POST /api/admin/links/invite`): `LABAPE-LINK-1:` + base64 of A's address, the SHA-256 fingerprint of A's platform CA root and a random secret. It works once, for an hour; only its hash is kept (`stack_link_invites`). It goes to B's admin out of band. |
| Linking | On B, Link with a stack (`POST /api/admin/links`): B fetches A's CA root (`GET /api/link/ca`, public), compares it with the fingerprint in the code, and from then on talks to A over HTTPS checked against that root (plus the system roots, for a stack with a public certificate). B sends `POST /api/link/join` with the code's secret, its own address and CA root, and a fresh token for A to call B with; A checks the code (made here, unused, unexpired), records B and answers with the token B is to call A with. Each side keeps the token it calls with in its secret store (`link.<id>.outbound`) and only the SHA-256 of the token it's called with (`stack_links.inbound_hash`). Refused: a stack's own code, a second link to the same address, a CA that doesn't match the code. |
| Not client certificates | The plan said client certificates; tokens over pinned TLS were built instead. Client certificates would have needed a separate published port (Caddy asks for client certificates per site, and asking on the main site makes browsers with certificates installed prompt every user) and a Caddy reload per link. A per-direction 256-bit token over TLS pinned to the peer's CA, bootstrapped by a one-time code that carries the CA's fingerprint, gives the same assurance. |
| What a peer reads | `GET /api/link/summary` with `Authorization: Bearer <token>` (401 otherwise): read-only (question 147), the stack as a whole: name, address, version, maintenance state, every KVM host (role, CPUs, memory, VM disk, VMs running/defined), totals of the enabled ones, the environments not destroyed (name, status, host, owner) and counts by status, running/queued jobs and failures in the last day, and the open alerts. No secrets, no per-user filtering: it's for the other stack's admins and auditors. |
| Polling and staleness | Every minute, in the monitoring thread (§30), each link's summary is read and kept (`stack_links.summary`) with when it last answered. A link with no answer for 3 minutes is stale: its last data stays, marked stale (review item 13). After Settings → Monitoring's peer failures (2) in a row it's an alert, "linked stack … isn't answering (…)", with the dashboard, syslog and email handling of §30-31. A 401 from the peer reads "the other stack no longer accepts this link (removed there?)". |
| Unlinking | Unlink (`DELETE /api/admin/links/{id}`) tells the peer first (`POST /api/link/leave` with the token; best effort) so it drops its side too, then removes the link and its token. Linking again takes a new code. |
| UI | Admin → Linked stacks (admins; auditors read): this stack's address and CA fingerprint, each link's status (ok, stale, not answering, not reached yet), last answer, a one-line summary, Check and Unlink; Create link code (shown once, with Copy) and Link with a stack. `tools/helpers/live/links.py`. All link actions are audited (`link.invite`, `link.add`, `link.join`, `link.leave`, `link.remove`). |
| Verified | pytest 228. Live: kvm1 made a code; the Pi linked with it (the code went from one to the other in a shell variable, never shown). Each side then read the other's summary over TLS pinned to the other's CA: kvm1 sees the Pi's hosts director, garagepc and kvm1 and its environment; the Pi sees kvm1's four hosts. The link stays for L2. |

## 35. The overall dashboard (L2, built 2026-10-14)

Batch 5 of the order in Planning_Backlog.md (review item 12, questions 147 and 148): on every linked
stack, the dashboard's **All stacks** panel above the alerts, for admins and auditors, shown once a
stack has a link (§34).

| Area | As built |
|---|---|
| What it shows | Totals across every stack (how many answer, distinct hosts, CPUs, memory used of total, VM disk free of total, VMs running of defined, environments, running and queued jobs, open alerts), then a card per stack: its name (opening that stack's own dashboard in a new tab, with its own sign-in; this stack's opens here), answering / stale / not answering / not reached yet, maintenance, version, hosts, VMs, environments, jobs (running, queued, failed today), open alerts with the first two. A stack that stopped answering shows its last summary "as of" when it last answered. View-only (question 147); actions are on each stack. Every stack shows it (question 148): each polls its own links. |
| API | `GET /api/dashboard/stacks` (auditors and admins): this stack's summary read live plus each link's last one, with its status, and the totals; `{"stacks": [], "totals": null}` without links. |
| Shared hosts | Two stacks can both have a record for the same machine (kvm1 is a KVM host of the Pi's stack too). Each host in a summary now carries `machine`: its own host name from its facts (§28.4), else its libvirt address, else this stack's host for `qemu:///system`. The totals count each machine once, with its newest reading, and only enabled ones add capacity. A record without facts keyed by address (kvm1's old, disabled `kvm1-ssh`) still counts as a separate host; it adds no capacity. |
| Verified | pytest 229. Live, kvm1 and the Pi linked: 2 of 2 answering, 4 machines, 60 CPUs (kvm1's 56 and garagepc's 4, kvm1 counted once although both stacks list it), 3 environments; each card with its version and counts. |

## 36. Full backups to linked stacks (B3, built 2026-10-14)

Batch 6 of the order in Planning_Backlog.md (review item 6, question 149): each stack's newest full
backup (§32) is also kept by every linked stack (§34), on that stack's primary file host (§29.4), so
losing a stack host and its own disks doesn't lose its backups.

| Area | As built |
|---|---|
| Sending | The worker, in a background thread each loop (one at a time), takes the newest archive that passed its check and sends it to every link that doesn't have it yet: `POST /api/link/backups/{name}` on the peer, with the link's token, the file's SHA-256 (computed once per archive) and the sidecar without the key file's path. A failed send is tried again after 10 minutes. Settings → Full stack backups → Copy to linked stacks (on by default). What each link last got, and any error, is the setting `backup.stack_sent`. |
| Receiving | The peer's API streams it into `<backup folder>/../linked/.incoming/` (507 when there isn't 1.2 times its size free), checks size and SHA-256 (400, send again, when they differ), and queues a `backup.store_linked` job, which copies it (and its sidecar, with where it came from) to `<primary's VM storage>/.labape-files/linked-backups/<peer>/` on the primary file host through file distribution's copy, then removes that peer's copies older than this stack's retention (the newest stays). The copies are listed in the setting `backup.linked_copies`. Received archives stay sealed with the sending stack's backup key: holding one tells this stack nothing. |
| Restoring from a copy | Copy the file from the primary file host to the new machine and install from it (§33), with the lost stack's key. |
| UI | Settings → Full stack backups: a chip per link with what it was last sent (or that it failed and will retry), and a table of the copies this stack keeps for others (peer, archive, size, taken, host and path). `tools/helpers/live/stack_backup.py` prints both. |
| Verified | pytest 231. Live, kvm1 and the Pi linked: the Pi's newest archive went to kvm1 and is kept on kvm1 (its own primary) under `/data/VMs/LABaPe/.labape-files/linked-backups/director.jlm.abq/`; kvm1's (56 MB) went to the Pi, whose primary is garagepc, and is kept there under `/data/VMs/LABaPe/.labape-files/linked-backups/172.21.20.11/` (job `backup.store_linked` succeeded). kvm1's first try got 405 because the Pi wasn't updated yet; the retry 10 minutes later went through. |

## 37. API tokens (A1, built 2026-10-14)

Batch 7 of the order in Planning_Backlog.md (review item 7): scripts and CI call the API with a token
instead of a browser sign-in.

| Area | As built |
|---|---|
| Kinds | **Personal** (`lpat_…`), any signed-in user: acts as its owner, with the roles chosen for it (none chosen: all of the owner's), but never more than the owner has when it's used (`owner roles ∩ token roles`), so a lost group takes effect with the owner's next sign-in. **Service** (`lpst_…`), admins only: exactly the roles the admin gave it, acting as `svc:<name>`, not tied to anyone's groups (A2's metrics scraper). |
| Storage and lifetime | `api_tokens` (migration `0015`): only the SHA-256 and the first 12 characters (to recognise it in lists); the secret is shown once. Every token expires, 1 to 365 days (default 90). Revoking is immediate. A token can't make tokens, and neither can a break-glass session. |
| Use | `Authorization: Bearer <token>`; `current_principal` checks it before the session cookie, so every existing endpoint and role check works unchanged (`Principal.via_token`). Expired, revoked or unknown: 401. The token's last use and address are kept (written at most once a minute). Every write made with a token (any method but GET/HEAD/OPTIONS) is an audit event, `token.use`, with the method and path, besides the endpoint's own audit; making and revoking are `token.create` and `token.revoke`. Reads aren't audited one by one (a metrics scrape every 15 seconds would flood the log). |
| API and UI | `GET /api/tokens` (mine; admins `?all=true`: everyone's and the service tokens), `POST /api/tokens`, `DELETE /api/tokens/{id}` (own personal tokens, or any for admins). The user menu's **API tokens** page: the list (name, owner, kind, roles, prefix, expiry, last use, state), New token (name, kind for admins, roles, expiry) and Revoke. `tools/helpers/live/tokens.py`. |
| Verified | pytest 234. Live on kvm1 (`tokens.py roundtrip`): a personal token read the API without a session (200, admin settings too as labape-admin is admin), couldn't make another token (403), was refused at once after revoking (401); the audit log has the create, the token's write attempt and the revoke. |

## 38. Prometheus metrics (A2, built 2026-10-14)

Batch 8 of the order in Planning_Backlog.md (review item 9), the last before release v0.3.0
(question 150).

| Area | As built |
|---|---|
| Endpoint | `GET /api/metrics`, Prometheus text format 0.0.4, auditors and admins, so normally read with an admin's **service token** with the auditor role (§37): `Authorization: Bearer lpst_…` (Prometheus `authorization: {credentials_file: …}`). Gauges only, from what LABaPe already collects: a scrape runs no probes and costs a few queries. Written by hand (no new dependency); integers exactly, label values escaped. |
| Metrics | `labape_info{version}`, `labape_maintenance`; per KVM host (`host`, `role` labels): `labape_host_enabled`, `labape_host_up` (load read in the last 10 minutes without an error), `_cpus`, `_cpu_percent`, `_memory_bytes`, `_memory_used_bytes`, `_vm_disk_bytes`, `_vm_disk_free_bytes`, `_vms_running`, `_vms_defined`, `_collected_timestamp_seconds`; `labape_environments{status}`, `labape_jobs{state}` (queued, running), `labape_jobs_failed_24h`, `labape_alerts_open{level}`; per linked stack `labape_link_up{stack}` and `labape_link_last_ok_timestamp_seconds`; `labape_backup_last_timestamp_seconds{kind="stack"|"database"}` and `labape_backup_last_check_ok{kind="stack"}`. |
| Verified | pytest 235 (every line parses; a viewer token gets 403, none 401). Live on kvm1 with a throwaway auditor service token (`tokens.py scrape`, revoked afterwards): 100 lines, kvm1's 56 CPUs, memory, VM storage and 4 of 5 VMs running, environments by status. |

## 39. VM snapshots (V1, built 2026-10-14)

Batch 9 of the order in Planning_Backlog.md (review item 3).

| Area | As built |
|---|---|
| Kind | libvirt **internal** snapshots (`virsh snapshot-create-as --atomic`): kept inside the VM's own qcow2 overlay, with the memory of a running VM, so a revert brings it back running where it was. LABaPe's VMs are BIOS with qcow2 disks (checked on kvm1: libvirt 11.3), which is what internal snapshots need; a UEFI (pflash) VM is refused with that reason. The disk file grows by what changes after a snapshot, visible in the VM's allocated disk and the host's free space. |
| Jobs | Take, revert and delete are `vm.snapshot` jobs (the environment's owners and admins, a deployed environment with no other job running), audited as `vm.snapshot.<action>`. Every job ends by reading the VM's snapshots back from libvirt (`sync`): the API can't reach libvirt (only the worker has the socket and the hosts' SSH), so the list it serves is the worker's last read, with its time; Refresh (`POST …/snapshots/refresh`) is a quick `sync` job. A snapshot made outside LABaPe (with virsh) shows up on the next read, without notes. |
| Data | `vm_snapshots` (migrations `0016`, `0017`): the snapshot as last read (taken, the VM's state then, parent, current) plus LABaPe's notes (description, who, automatic); per VM, the setting `snapshots.vm.<env>.<vm>` says when it was read and whether the VM can have snapshots. |
| Automatic | Settings → VM snapshots → "Snapshot every VM before a deploy changes it" (off by default): a deploy of an environment whose VMs already exist (a re-deploy, added applications or services) first takes `auto-before-job-<id>` of each, keeping the newest 3 (1-20) automatic ones per VM. UEFI VMs are skipped with a note in the job log; any other failure stops the deploy before it changes anything. |
| Destroying | `safe_undefine` (the engine's one teardown path) now passes `--snapshots-metadata`: libvirt refuses to undefine a domain with snapshots otherwise ("cannot delete inactive domain with N snapshots"), which the live test hit. The snapshots themselves go with the VM's disk file. |
| UI | VM page → **Snapshots**: the list (name, taken, the VM's state then, by, description, current and automatic marks), "as of" with Refresh, Take snapshot (name, description), Revert (confirms what's lost) and Delete. Settings → VM snapshots. `tools/helpers/live/snapshots.py`. |
| Verified | pytest 238 (a fake virsh). Live on a throwaway Debian VM on kvm1: took `before-marker`, wrote a file, reverted: the file was gone and the VM was running with its uptime kept (memory restored); the list showed libvirt's time, "running" and current; with the setting on, a re-deploy took `auto-before-job-130` first; destroying the environment with two snapshots failed (fixed above) and then succeeded. |

## 40. Environment leases (E1, built 2026-10-14)

Batch 10 of the order in Planning_Backlog.md (review item 4).

| Area | As built |
|---|---|
| Off by default | Settings → Environment leases. The end of a lease can destroy VMs, so nothing expires until an admin turns leases on; then new environments get the default lease (30 days) and existing ones have none until an owner or admin sets one. A fresh deploy of a destroyed or new environment starts a fresh lease (an old, ended one would shut it down at once). |
| The lifecycle | The worker, every loop outside maintenance mode (`leases.tick`): reminder emails to the owners (the creator and user grants with owner level that have an email) at each "days before" (7 and 1 by default; each once); at the end, an `environment.power` shutdown of every VM (force off after 5 minutes), requested by `lease`; after the grace period (7 days), an `environment.destroy` job, or, with "keep it shut down", nothing more. Each step is recorded in `environments.lease_state` and mailed. An environment with a job running waits for the next pass. |
| Extending | Owners: +7 or +30 days from the later of now and the current end, at most "longest from now" (180 days) ahead; extending an ended lease starts it again (the VMs stay off until someone starts them). Admins: any end, or no expiry (`POST /api/environments/{id}/lease` with `days`, `until` or `clear`), audited as `environment.lease`. |
| Data and API | `environments.expires_at` and `lease_state` (migration `0018`); each environment's `lease` in the API: enabled, expires_at, phase (none, active, expiring inside the first reminder, ended, destroying), destroy_at, max_days. |
| UI | Environment page: a lease line (ends when, then what; ended; being destroyed) with +7 days, +30 days and, for admins, No expiry. Environments list: an Expires column. Settings → Environment leases. `tools/helpers/live/leases.py`. |
| Verified | pytest 242. Live on kvm1 with a throwaway environment: leases on with grace 0 and destroy, its lease set to end 3 minutes later: the reminder was recorded, at the end job 136 shut its VMs down (requested by `lease`), then job 137 destroyed it. Leases were turned off again afterwards (the lab's environments have no expiry). |

## 41. Quotas (E2, built 2026-10-14)

Batch 11 of the order in Planning_Backlog.md (review item 5).

| Area | As built |
|---|---|
| What a quota is | Limits on vCPUs, memory (MB), VM disk (GB) and the number of environments, each optional (empty: no limit), for a **user**, a **group**, or **everyone** (the default). `quotas` (migration `0019`). |
| Which applies | A user's own quota; else the most generous of their groups' quotas, limit by limit (no limit wins); else the default; else none. Admins have none. |
| Usage | What the user's environments ask for, counting every environment they created that isn't destroyed: their host groups' count times CPUs, memory and disk (`demand_of`, the same sums as the host capacity check), and how many there are. |
| When it's checked | Creating an environment (after the host is chosen, before capacity) and deploying a destroyed one again: 403 with "Over your quota (where it comes from): this would use N of M …". Environment specs aren't edited after creation, so those are the only two points. |
| API and UI | `GET /api/quotas/me` (anyone: limits, where they come from, usage); `GET/POST /api/admin/quotas`, `DELETE /api/admin/quotas/{id}` (admins; a POST creates or replaces the quota for that user, group or the default), audited as `quota.save`, `quota.delete`. Admin → **Quotas**; the Environments page shows "Your quota: N of M …". `tools/helpers/live/quotas.py`. |
| Verified | pytest 245. Live on kvm1: labape-dev (a deployer with one environment) given 0 environments saw it in `/api/quotas/me`, and creating another was refused, "2 of 0 environments"; the quota was removed afterwards. |

## 42. An environment's own accounts (E3, built 2026-10-14)

Batch 12 of the order in Planning_Backlog.md (question 113): accounts beyond the built-in admins,
created at deploy, on the credentials sheet, rotatable.

| Area | As built |
|---|---|
| What | The environment's spec gets `accounts`: **domain** users (on its domain controller, in `CN=Users,<domain>` unless an OU is given; needs a DC in the environment) and **local** users on the hosts of chosen roles (Windows and Linux; not domain controllers, which have no local accounts). Names: a lowercase letter, then letters, digits, `.`, `-`, `_`, up to 20 (the Windows limit), not a built-in one (Administrator, root, labape, …). Groups are optional. `app/labape/env_accounts.py`. |
| Passwords | Each gets a generated 20-character password (Windows complexity) when added, kept as `environment/<id>/account/<name>` in the secret store; taking an account off the list forgets its password (the account stays on the VMs: the VM page's Accounts can remove it). |
| Deploy | The job's directory manifest (the engine's directory objects, Design_Directory-Objects.md) gets the accounts added to the global one's (`domain_users`, `local_users`, each with `password_vault_key: labape_account_<name>_password`), and the passwords are laid over the job's vault copy next to the R4 ones, so the existing `domain_directory`, `windows_common` and `linux_common` tasks create them. After the deploy the credentials sheet gets an **Environment accounts** section (domain ones as `NETBIOS\name`). |
| Rotation | A policy per account, `env.<id>.account.<name>` (kind `env_account`, weekly by default, in Admin → Secrets like the R4 admin accounts): a new password set on the account's VMs (`microsoft.ad.user` on the first DC for a domain account, `ansible.windows.win_user` or `ansible.builtin.user` with a SHA-512 hash for local ones), stored and put on the sheet only once every host took it; the attempt is kept as a rotation candidate meanwhile. |
| API and UI | `GET/PUT /api/environments/{id}/accounts` (owners change it; audited as `environment.accounts`), applied by the next deploy. Environment page: an **Accounts** panel (the list; Edit: name, kind, the roles' hosts for local ones, groups). `tools/helpers/live/env_accounts.py`. |
| Verified | pytest 249. Live on a throwaway Debian environment: a local account `tester` on linux_server was added and the environment redeployed: `id tester` on the VM, a SHA-512 hash in `/etc/shadow`, and the sheet's Environment accounts section; Rotate now changed the stored password and `/etc/shadow` on the VM. The domain path (it needs a Windows DC) is covered by the unit tests and reuses the engine's `domain_directory` task as it is. |

## 43. Best-fit placement and templates ahead of need (P1a, built 2026-10-14)

Batch 13 of the order in Planning_Backlog.md, first part: §15's "best fit" for a whole environment,
and the automatic template copies. Spreading one environment over several hosts (P1b) and live
migration (P2) wait on questions 151 and 152.

| Area | As built |
|---|---|
| Ranking | `rank_hosts` (api/environments.py): every enabled KVM host (not stack-only ones) that passes the same checks a chosen host gets (its networks for these host groups, the templates on it or on the primary to copy, the RAM commitment limit), best first by memory committed with this environment added, plus vCPUs per CPU / 16, plus 0.15 per queued or running job there, plus 0.1 per template still to copy; a host without a load reading in 10 minutes ranks after the rest. Hosts that don't fit say why. |
| Use | `POST /api/environments/placement` (deployers: the ranking, nothing created); `POST /api/environments` with `kvm_host_id: null` takes the best (422 with each host's reason when none fits), for scripts and API tokens. The new-environment form's **Suggest best host** ranks the hosts for the groups as filled in, picks the first and shows the list with reasons (the form keeps a host chosen, because its network and template lists are per host). |
| Templates | Settings → File distribution → "Copy every template to every KVM host daily" (off: templates are large): a `files.sync_templates` job once a day copies each template in the primary's library to every enabled host that lacks it (file distribution's `ensure_template`, host to host through the worker); one copy failing doesn't stop the others. |
| Verified | pytest 252 (two hosts ranked by load, running jobs counted, a full host refused with its reason, `null` placing on the best, no host fitting, the daily schedule). Live on kvm1 (one enabled KVM host in that stack): the ranking with its reason, and an environment created with `kvm_host_id: null` placed on kvm1 and deployed (job 142); destroyed afterwards. |

## 44. A logo (built 2026-10-14)

Part of batch 16 of the order in Planning_Backlog.md (question 68: "name only now; a logo later").
Kerberos, the other half, waits on question 154; sign-on between stacks (F1) on question 153.

| Area | As built |
|---|---|
| What | Settings → Branding: Upload logo (PNG, JPEG or WebP, at most 256 KB; checked by its first bytes), Replace and Remove. It shows in the top bar before the name, above "Sign in to …", and as the browser tab's icon. |
| Why not SVG | An SVG can carry scripts, which would run in LABaPe's origin if someone opened the logo's URL; the raster formats can't. The logo is also served with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`. |
| Storage and API | The setting `branding.logo` (type, the image in base64, a version: the first 12 hex digits of its SHA-256, used to bust caches); `GET /api/branding` gives `logo` (that version, or null), `GET /api/branding/logo` serves it (public: the sign-in page needs it; cached a day), `PUT`/`DELETE /api/admin/settings/branding/logo` (admins; audited `branding.logo`, `branding.logo.remove`). |
| Verified | pytest 253 (an SVG refused, an oversized image refused, PNG and WebP served with their types and the headers, removal). Live on kvm1 (`tools/helpers/live/logo.py roundtrip`): a generated PNG uploaded, served publicly byte for byte with the sandbox policy, listed in `/api/branding`, then removed. |


## 45. One environment over several KVM hosts (P1b, built 2026-10-14)

Batch 13b of the order in Planning_Backlog.md: §15's per-group host. Question 151 (2026-10-14):
build it now and test it with a nested KVM VM on kvm1 as the second host. Live migration (P2)
follows (question 152).

| Area | As built |
|---|---|
| Spec | A host group may carry `kvm_host_id`; without one it goes on the environment's own host (its "home"). The API drops the field when it names the home, so a one-host environment's spec is unchanged. A group's host must be enabled and not stack-only. Placement is fixed at create (there's no spec edit), so redeploys keep VMs where they are. `app/labape/spread.py` holds the mapping (VM → host, the hosts in order, each part's workspace). |
| Checks | Per group, against its own host: the network (the host must carry the group's catalog network; the catalog network is what says the hosts share one lab segment), the template library (or the primary's, copied at deploy), VM names (unique per libvirt host, now checked against every environment's VMs on that host, wherever their home is), capacity (each host with its own groups). Static addresses come from the group's host's pool of the network; allocations stay network-wide, so no address is used twice across hosts. Best fit (§43) places only the groups that don't name a host. |
| Engine | A spread environment's job runs in parts (`runner.plan_parts`). Deploy: each host's OpenTofu part on that host, in its own workspace (the home keeps the environment's name, so environments from before keep their state; another host's is `<name>--h<id>`; the home always runs its part, §46), `deploy.sh --no-ansible --hosts-out hosts.part.json`, in `environments/<name>/engine-h<id>`; then, on the home, `deploy.sh --hosts-from hosts.merged.json` builds the inventory from every part's VMs and runs Ansible once (it reaches them all over the shared lab network). Destroy: each part's `destroy.sh`, the home last; a failing part doesn't stop the others. Refresh addresses: each part's `refresh-addresses.sh --hosts-out`, then the inventory on the home (`--hosts-from ... --no-ansible`). Each part gets its own files from the primary (its groups' templates and ISOs; the installers go to the host that runs Ansible). The job log marks each part. |
| VM operations | Power jobs reach each VM on its own host (`power.Vms` with per-VM URIs); a VM's snapshots run as a job on its host; the snapshot before a deploy takes each VM on its host; VM details, its load series and its consoles (serial, VNC) come from and go through its own host; promote to template runs on the VM's host with its part's workspace; the dashboard and the environment's VM list read each VM's state from its host's stats (the list shows a KVM host column when the VMs are on more than one); users see the hosts their environments use. |
| UI | New environment: each host group has a **KVM host** choice ("Same as the environment", or another enabled host) when there's more than one host; its networks and templates follow that host. |
| Verified | pytest 261 (the helpers, per-host checks and VM names, the parts of a deploy, destroy and refresh and the merge, a missing part, power per host, the reused template row, the DNS kept). Live: a nested KVM host `nkvm1` (environment 22 on kvm1: Debian 13, 8 vCPUs, 16 GB, a second NIC on `br1` bridged as `br-lab` inside; host record 2), set up with access setup and Prepare (kvm profile, `Prereq.NoSleep` ran, sleep masked by the access setup). Environment `spread1`: `sph1` on kvm1, `spo1` on nkvm1 (job 151: three parts, OpenTofu on each host, Ansible once from kvm1 over both, 0 failed); the VM list and details showed each VM's host and console host; a restart reached both hosts (job 152); the address refresh rebuilt the inventory from both parts (153); a snapshot of `spo1` ran on nkvm1 (154, 155); the destroy removed nkvm1's part first, then kvm1's (156), and left no VM behind. Bugs found on the way: Testing_Troubleshooting-Log.md. |
| Not yet | Moving a deployed group to another host (P2, live migration, question 152). The worker's per-host job limit counts only the home host for a spread job. |

## 46. Moving a host group to another KVM host while it runs (P2, built 2026-10-14)

Batch 14 of the order in Planning_Backlog.md (decision 23, question 152: after P1b). Builds on §45.

| Area | As built |
|---|---|
| What moves | A host group: each of its VMs, one after the other, to another enabled KVM host that carries the group's network. The VMs must be running (a shut-off VM is refused: start it first) and have no snapshots (libvirt can't move them). |
| How | `engine/migrate.py`, job `environment.move` (run by the worker, which holds a libvirt connection to both hosts): the seed CD-ROMs are emptied; the destination's disk files are created empty at the same virtual size; `virsh migrate --live --persistent --undefinesource --copy-storage-all` with a definition rewritten for the destination (its bridge for the network, its VM storage path, the same MAC), memory and disks streamed host to host (`--migrateuri`/`--disks-uri tcp://<destination address>`, the host's SSH address). Then the VM is tagged with the destination part's workspace, the source's old disk and seed files are removed, the group names its new host (§45), and a deploy is queued. A failure leaves the VM running on the source and removes the empty destination files; a re-run skips VMs already moved. |
| Reconcile | The queued deploy: the destination's part adopts the running VM (`existing-domain.sh`: running and tagged for this workspace = nothing to create), and the source's part drops its record (`destroy-vm.sh` finds no VM there). The pre-flight address check skips a VM that already runs on the host with this workspace's tag (its address answers because it is that VM). The home host always runs its part now, even with no groups left on it, so its state forgets moved VMs and a destroy deletes its workspace. The clone MAC is derived from the environment's name, not a part's workspace, so a moved VM's MAC matches what OpenTofu computes. |
| API and UI | `POST /api/environments/{id}/move` {group, kvm_host_id} (owners of a deployed environment; the destination gets the checks a new group there would: enabled, network, VM names, capacity, template). Environment page, VMs: **Move host group** (group, destination, Move, with a confirmation). |
| Verified | pytest 265 (the destination definition, a move's commands and the files created and removed, the refusals, the job naming the new host and queueing the deploy). Live with kvm1 and the nested host nkvm1 (§45), environment `movetest`: `sph1` moved kvm1 → nkvm1 in 40 s (job 158) and back in 70 s (161), up the whole time (booted 16:54, up 13 minutes after both moves); each follow-up deploy adopted it (0 changed in Ansible); the old files were gone; the destroy left nothing. |
| Not yet | Moving a shut-off VM (copy the disk, define it there); moving a VM with snapshots; a single VM out of a group. |

## 47. Sign-on between linked stacks (F1; F1a built 2026-10-14)

Batch 15 of the order in Planning_Backlog.md. Question 146 chose OIDC sources (each stack's Authentik
offers "Sign in with <the other stack>"); question 153 chose (a): each Authentik served with a
certificate from its stack's platform CA, so a linked stack, which pins that CA (§34), can verify it.

### 47.1 F1a: Authentik over HTTPS (built)

| Area | As built |
|---|---|
| Serving | The stack's Caddy serves Authentik on port 9445 (`LABAPE_AUTH_PORT`) under the web interface's name and with its certificate: from the platform CA with `Caddyfile.labape-ca`, Caddy's own otherwise. It proxies to `LABAPE_AUTHENTIK_UPSTREAM` (default `host.docker.internal:9000`, Authentik's plain-HTTP port on the same host, through `host-gateway`). A new port, not Authentik's own 9443, so a stack whose Authentik isn't restarted by an update doesn't lose its web interface to a port clash. Authentik sees `X-Forwarded-Proto: https` from a private address (a trusted proxy by default) and publishes an HTTPS issuer. |
| LABaPe's sign-in | `LABAPE_OIDC_ISSUER=https://<web name>:9445/application/o/labape/` (`tools/authentik-test/deploy.sh --configure-labape` writes it for new installs). The OIDC client (discovery, token, test connection, the client-secret check) and the Authentik group search verify TLS with the system roots plus the stack's platform CA root (`ca.trust()`). Browsers sign in over HTTPS; before, sign-in went over plain HTTP on 9000. |
| Existing stacks | Set `LABAPE_OIDC_ISSUER` as above in `container/.env` and recreate `caddy`, `labape-api` and `labape-worker` (User_Docs/Web-Interface-Admin.md). |
| Verified | pytest (the platform root added to the trust, the system roots without it). Live on kvm1: Caddy recreated with 9445; the discovery document over `https://172.21.20.11:9445`, verified against kvm1's platform root, names the HTTPS issuer; the web interface unchanged; LABaPe switched to the HTTPS issuer and the test users signed in through it; the Authentik group search worked over HTTPS. |

### 47.2 F1b: OIDC sources between linked stacks (built)

Questions 156 (b: a user keeps their role groups) and 157 (an admin turns it on per link, on each side).

| Area | As built |
|---|---|
| Turning it on | Admin → Linked stacks → **Sign-on** → Turn on (`PUT /api/admin/links/{id}/sign-on`), on stack A for link B: B's users may then sign in to A. A asks B over the link (`POST /api/link/sso/provider`, the link's token); B's update agent makes an OAuth2 provider and application `labape-link-<A>` in B's Authentik: a **public client with PKCE** (no client secret to carry between stacks), the redirect URI A's source callback only (checked: https, A's host name, `/source/oauth/callback/`), the LABaPe provider's flows, signing key, grant types and openid/email/profile mappings (profile carries the groups). Then A's update agent makes the OpenID Connect source `labape-link-<B>` in A's Authentik and adds it to the identification stage, so A's sign-in page offers "<B>". Off removes both (B's side even if B doesn't answer). The state (setting up, on, failed with the agent's message) shows per link. |
| Users | Named `<user>@<B>` (a source property mapping), so they never collide with A's own; enrolled once (`default-source-enrollment`), then `default-source-authentication`. `group_matching_mode=name_link`: B's `groups` claim joins them to A's groups of the same names, so B's admins are A's admins (question 156 b). |
| Trust | A's Authentik verifies B's Authentik over HTTPS with B's platform CA root from the link: `REQUESTS_CA_BUNDLE=/labape-trust/ca-bundle.pem` in `tools/authentik-test/compose.yaml`, the image's own roots plus `trust/peers/<source>.pem` (gitignored, on the host). |
| Host side | `tools/authentik-test/sso.sh` (the agent's action `authentik_sso`; also `--bundle-only`) writes the bundle, starts Authentik with it if needed and runs `sso.py` with `ak shell` in the worker. `deploy.sh` writes the bundle before starting Authentik and, run as root (the installer), hands `.env` and `trust/` to the checkout's owner, because the agent runs as the service user. |
| Verified | pytest 273 (names, the HTTPS requirement, the callback check, the peer and local requests, the agent's result, a busy agent, the endpoint). Live between kvm1 (172.21.20.11) and the Pi (director.jlm.abq), both on this branch: on kvm1, sign-on from the Pi: the Pi's agent made its client, kvm1's agent the source; `live/linked_login.py` signed in to kvm1's LABaPe as the Pi's `labape-admin` (`labape-admin@director.jlm.abq`, groups `labape-admins`, role admin), first through enrollment, then through the returning-user flow. The other direction from the Pi the same way (`labape-admin@172.21.20.11`, admin). Off then on again on the Pi: the source removed and remade. Both left on. |
| Upgrading a stack | After updating: reinstall the agent (`deploy/update-agent/install.sh`; an update doesn't replace the running agent), and in `tools/authentik-test/` as the service user `bash sso.sh --bundle-only && docker compose up -d` (if `.env` there is root's, give it to the service user first). Admin guide §9. |

## 48. Lists, secrets and settings round (2026-10-14; Planning_Lists-Secrets-Settings.md)

### 48.1 L2: filters and the file's OS at upload (built)

| Area | As built |
|---|---|
| Files | Every tab has an **OS** filter; its choices are the OSes that tab's files are for (an ISO's `os_key`; installers' and other files' `targets`). The upload and **Download from URL** ask for the file's OS first: an ISO's OS (empty: guessed from the name, as before), an installer's targets (required; the upload button waits for them), another file's targets (optional). The API takes them as tus metadata (`os_key`, `targets` comma-separated) and in `POST /api/files/download` (`os_key`, `targets`), checked against the target vocabulary (`api/files.os_choice`); an installer without targets is refused (422, field `targets`). Other files' targets can be edited on their page. CSV export comes with L1's export dialog. |
| Catalog | Each tab filters by name (or display name), platform, description or notes and, for admins, status; in the browser, kept in the address. |
| Networks | Filters by name or description, address (network, gateway, pools, DNS), addressing, VLAN and, for admins, enabled or disabled. |
| Verified | pytest 274 (the OS at upload: refused without targets, unknown targets, targets kept, an ISO's OS beats the guess, other files' targets; the download test names its targets). Live on kvm1: the build type-checked the UI; an installer upload without an OS was refused with the message; another file uploaded with `linux,rocky9` kept them; it was deleted afterwards. |

### 48.2 L1: pages, page size and exports (built)

| Area | As built |
|---|---|
| Page size | `users.preferences` (migration 0021), `GET/PUT /api/auth/me/preferences` (`page_size` 5-1000, 50 by default). `components/Paging.tsx`: `usePageSize()` (loaded once, shared by every list, saved on change), `Pager` (rows a-b of n, previous/next, rows per page 25/50/100/250 or **Other…**). |
| Lists | Environments, Files and Catalog page in the browser (their lists are small and already filtered there). Jobs pages on the server: `GET /api/jobs?limit&offset` with the matching total in `X-Total-Count` (the API client's `getPage`). Syslog pages by message id (the newest page first; Next asks for the messages before the page's last), at the user's page size; **Live** adds new messages on the first page only. |
| Exports | `ExportButton`: this page, rows from-to (1-based, of the filtered list; question 159) or all, built in the browser and saved as a file. Environments CSV (name, status, KVM host, hosts, addresses, created by/at, expiry, updated); Files CSV per tab (name, OS, size, SHA-256, description, path, added by/at); Jobs JSON or CSV (the API's job objects: parameters and result, no logs; fetched 1000 at a time; question 160). |
| Environments | Filters: name, status, KVM host, OS (a host group runs it), created by, created since/until; in the address. |
| Verified | pytest 276 (the preference's default, change and limits; Jobs pages and totals). Live on kvm1: the UI built (type-check); `labape-dev`'s page size 50 → 25 → back; Jobs `limit=50&offset=50` gave 50 rows of 166, starting below the first page. |

### 48.3 D1: loop devices and sensors (built)

| Area | As built |
|---|---|
| Drives | Disks and partitions leave out `loop` devices; **Show loop devices (n)** (shown when there are any) brings them back. |
| Sensors | The access-account setup script installs `lm-sensors` (apt) / `lm_sensors` (dnf) next to smartmontools, when `sensors` is missing; the installer's `Prereq.Packages` lists them for both families. The host page notes a missing `sensors` (only the kernel's thermal zones then). |
| Verified | pytest (the setup script installs both). Live: access setup on nkvm1 installed `sensors` (it had none) and its page had no warnings; the Pi showed the new note until lm-sensors was installed; installing smartmontools on kvm1 showed its first SMART reading: `/dev/sdc` (in `md10`, `/data`) has 76 reallocated and 4032 pending sectors, reported to the user. |

### 48.4 N1: every name of the stack, one certificate (built)

Question 158: one combined certificate.

| Area | As built |
|---|---|
| Names | `LABAPE_HOSTNAME` (the main name, also the OIDC redirect address) plus `LABAPE_ALT_NAMES` (comma-separated), which `web-tls.sh setup` fills once from the host: `hostname -s`, `hostname -f`, the default route's source address; the main name and repeats left out. Editable in `container/.env`. |
| Certificate | `container/caddy/web-tls.sh`: an EC P-256 key and a CSR with every name as a SAN (IP addresses as IP SANs), signed by `labape ca-sign` (the platform CA, 30 days) into `caddy/tls/web.crt` and `web.key` (mounted read-only at `/labape-tls`). `renew` (the user timer `labape-web-tls.timer`, daily) signs again when 10 days are left or the name list changed, then `caddy reload --force`. |
| Serving | `Caddyfile.names`: `:443` (the web interface) and `:9445` (Authentik) with that certificate whatever name arrives, `:80` redirects to HTTPS, the break-glass listener as before. `setup` switches `LABAPE_CADDYFILE` only after the certificate exists, so Caddy never starts without one. |
| Installer | Step `Stack.WebTls` after `Stack.Up` (it needs `labape-api` to sign), only with `Tls` `labape-ca`; existing stacks run `web-tls.sh setup` once (admin guide §9). |
| Not yet | Editing the names from the web interface (they're host configuration; `.env` and `renew`). |
| Verified | pytest (the step only with the platform CA, its command, its place). Live on kvm1: setup found `OZ` and `OZ.jlm.abq` beside `172.21.20.11`, one certificate `DNS:OZ, DNS:OZ.jlm.abq, IP:172.21.20.11`, timer active; each name on 443 and 9445 answered 200 verified against the platform root; HTTP redirected; sign-in unchanged; `renew` not due did nothing; a name added to `LABAPE_ALT_NAMES` was in the renewed certificate and served (Caddy reloaded), then removed again. |

### 48.5 S1: secret health, recoverable or not (built)

Question 161: checks every quarter of the rotation period, at least daily, none for a secret rotating daily or more often; Recoverable yes = LABaPe recovers it when a check fails; no = if it's lost there's no way back.

| Area | As built |
|---|---|
| Model | `secret_policies.check_status` (ok, recovered, recovering, failed, unchecked, none), `check_error`, `last_checked_at`, `next_check_at` (migration 0022). |
| Checks and recovery | `engine/secret_health.py`, `HEALTH` per kind: data key and master key (every built-in secret decrypts; no), PostgreSQL (the database answers; yes, the agent sets a new one), CA password (the CA signs a throwaway request; no), vault password (the vault opens; yes, an earlier kept password that opens it), VM key (intact, matches its public key; yes, a rotation), worker key (each remote host accepts it; yes, the host's access account authorizes it again), directory token and OIDC client secret (Authentik accepts them; yes, a rotation with the elevated account), SMTP (the server accepts the login; no), the stores' logins (the store accepts it; yes, the agent's new `stores_setup` action runs `stores.sh setup`), member servers' Administrator (WinRM sign-in; yes, the last attempted value or the domain admin sets it again), domain Administrator (no: only the last attempted value is tried), an environment's own accounts (no check; yes, a rotation sets them as admin). "Not in use" when there's nothing to check; unreachable targets are inconclusive. |
| Scheduling | The worker's loop queues `secret.check` jobs for due policies (the first checks spread over an hour); a failure is checked again after an hour. A failed, unrecovered secret is an error alert (`secret:<name>`) through monitoring (dashboard, syslog, email). |
| API and UI | Policies carry `recoverable`, `recovery`, `checkable`, `check_every_hours` and the check fields; `POST /api/admin/secrets/{id}/check` (admins; 409 while a check or rotation runs). Secrets: **Recoverable** (Yes/No, how in the tooltip) and **Health** (status, last/next, **Check now**). |
| Verified | pytest (intervals, flags, ok/none/failed/recovered/recovery failing, recovery by rotation, scheduling, the endpoint). Live on kvm1, **Check now** on every secret: all ok (98 built-in secrets decrypt; the CA signs; the vault opens; Authentik, OpenBao and Conjur accept their logins; lab2's Administrator and domain admin sign in) except SMTP (not in use) and two policies of destroyed environments (not deployed). Recovery: the worker's key removed from nkvm1's `authorized_keys`; the check failed, the access account put it back, the check passed ("recovered"), after the OpenSSH penalty fix (Testing_Troubleshooting-Log.md). |

### 48.6 S2: secrets in sections, environment filters (built)

| Area | As built |
|---|---|
| Sections | Secrets has tabs **Stack**, **KVM hosts**, **Environments** (`?section=`). Each shows its rotation policies (with S1's health) and its stored secrets. Policies: the worker key under KVM hosts, environment kinds under Environments, the rest under Stack. Stored secrets: `api/secrets.classify` sorts by name (`environment/<id>/...` → Environments; the rest → Stack). The KVM hosts tab also lists each host's LABaPe access account and its state. |
| Environment secrets | Each row carries the environment (and its status, or "gone"), its VMs (from the spec: member servers for the local Administrator, domain controllers for the domain admin and domain accounts, the roles' hosts for local accounts, all for the sheet and inventory), local or domain, and password, key or document; a rotation's kept attempt is marked. Filters: environment, store, VM, local or domain, password or key, changed since/until. |
| Cleared values | A cleared secret is an encrypted empty string, so "cleared" now comes from the decrypted value; spent job tokens and destroyed environments' values are hidden unless **Show cleared** is ticked. |
| Fix | A destroy cleared the Administrator passwords, sheet and inventory but not the environment's own accounts or kept rotation attempts; `clear_env_passwords` now clears everything under `environment/<id>/account/` and `rotation-candidate/` too. |
| Verified | pytest (sections, environment details per kind, a destroy clearing every password of its environment only). Live on kvm1: 80 cleared rows hidden of 104; the environment rows show lab2's domain admin on `l2dc1` (domain), its Administrator on `l2win1` (local), sheets and inventories with every VM. Found a destroyed environment's (accttest1) account password still stored from before the fix. |

### 48.7 S3: Settings in sections (built)

Question 162. Settings shows one section at a time, chosen from a list on its left (`?section=`): Sign-in (Directory, VM accounts), Stack (Maintenance window and Maintenance now, Monitoring, Branding, History, Syslog (audit trail), Email, Syslog collector), Updates and backups (Full stack backups, Database backups), Environments (Environment leases, VM snapshots, Templates), Files (File distribution, File locations, Downloads), Secrets (Default store, OpenBao, Conjur), Remote access. Each card's section comes from `GROUP_OF` (by title) through a React context; the cards and their saves are unchanged. Verified: the UI built; every card's title is in the map (checked by script).

### 48.8 A1: recovery pairs (built)

| Area | As built |
|---|---|
| Accounts | `labape-admin2` (`vmaccess.RECOVERY_ADMIN`): in the domain (Domain Admins, password never expires; `domain_controller` role, once per deploy) and on each Windows member (Administrators; `windows_common`, not on DCs). Their passwords are LABaPe's, per environment (`environment/<id>/domain_admin2`, `windows_admin2`), made before the deploy (`ensure_recovery_passwords`) and passed as `labape_recovery_domain_password` / `labape_recovery_local_password`; a deploy sets them again. CLI deploys without them skip the tasks. |
| Rotation | New kinds `domain_admin2`, `windows_admin2` (policies per environment, weekly, first due half a period after their partner's): set while signed in as Administrator, then checked by signing in as `labape-admin2`. |
| Recovery | `reset_with_pair`: Administrator's stored password set again while signed in as `labape-admin2`, and the reverse. S1's order for the Administrators: the last attempted value, the pair, then (member servers) the domain admin. The domain Administrator is now Recoverable: yes. Sign-in as `labape-admin2` uses the plain name (WinRM Basic; Testing_Troubleshooting-Log.md). |
| Verified | pytest (passwords made once and passed to the deploy, the pair resetting each way with the right sign-in, the second admin's check, four kinds per environment). Live on lab2: a redeploy made both accounts; all four lab2 secrets checked ok; the domain's `labape-admin2` given an unknown password (`live/break_password.py`) was recovered by Administrator; `l2win1`'s Administrator given an unknown password was recovered by its `labape-admin2`. The domain Administrator itself wasn't broken live (the lab's DC). |

### 48.9 A2: a password of its own per VM (built)

| Area | As built |
|---|---|
| Names | `vm_passwords.name()`: `environment/<id>/vm/<vm>/windows_admin` (Administrator, once the VM has it), `windows_admin.pending` (the one a deploy is giving it), `windows_admin2` (its `labape-admin2`), `account/<name>` (each of the environment's local accounts, per VM). The environment-level `windows_admin` stays the bootstrap password new VMs start with (the template's answer file); domain controllers keep the domain's. |
| Deploy | `prepare` merges each VM's values into the workspace's `host_vars/<vm>.yml` (0600; `write_host_vars`): `ansible_password` when the VM has its own, `labape_admin_password` (its own or the pending one), `labape_recovery_local_password`, `labape_host_passwords`. `site.yml`'s first play includes `playbooks/tasks/labape_admin_password.yml` for WinRM hosts: it decides once whether to change (a block's `when` is checked again before each task, after `ansible_password` changed, which skipped the rest of the first try), probes for an earlier run that already changed it (WinRM "rejected" → use the new one), waits for the VM, changes Administrator in the same WinRM shell with ADSI `SetPassword` (its reply is refused, since Basic auth sends the password with every request; a detached change lost the account database once the shell closed, Testing_Troubleshooting-Log.md), goes on with the new one and leaves a marker in `labape-applied/<vm>`; if the new one never signs in, it goes back to the old one and fails with the change's error. Rotation's `_set_password` changes in the same shell too. `_finish` promotes the pending value of each marked VM (`promote`), also when the deploy fails later, and writes them into the stored inventory (`apply_to_inventory`). A VM without a marker keeps its old password. |
| Local accounts | `windows_common` and `linux_common` take each account's password from `labape_host_passwords[name]`, falling back to the environment-level one (CLI deploys). `env_accounts.rotate` gives each VM its own. |
| Sheet | The Windows section points at a per-VM list, "Windows Administrator, per VM" (`apply_to_sheet`); local accounts are listed per VM. |
| Rotation and S1 | `windows_admin` and `windows_admin2` rotate per VM, each from its own stored value (`_rotate_member_admins` passes `labape_new_pw` as a host var); a VM that doesn't take it keeps its old one. S1 checks and recovers per VM: the VM's rotation candidate, then its pair, then the domain admin. Destroy clears `vm/` too. |
| Verified | pytest (per-VM values made once and written to host_vars, promote only with a marker, the inventory and sheet per VM, local accounts per VM, rotation and S1 per VM, the task file's order with the decision made once). Live on lab2: three deploys failed on the change itself (see Testing_Troubleshooting-Log.md); after the fix, a redeploy (job 201) gave `l2win1` its own Administrator password, promoted it (the environment's is refused there now), and finished; the sheet lists it per VM; checks for Administrator, `labape-admin2` (local and domain) and the domain Administrator all ok; **Rotate now** on `windows_admin` (job 206) changed `l2win1`'s own and signs in with it. The earlier-run heal path (a VM already on its pending password) wasn't exercised live. |

## 49. People's and VMs' access to environment secrets (V1, V2)

Questions 163-166; plan in Planning_Lists-Secrets-Settings.md. Built in parts: V1a (the layout and the environment page), V1b (OpenBao's own UI and CLI), V2 (VMs).

### 49.1 V1a: a folder per environment, and the environment page's secrets (built)

| Area | As built |
|---|---|
| Layout | `stores/layout.py`. LABaPe's names stay `environment/<id>/...`; in OpenBao (only, question 166) they sit at `env/<environment name>/user/<name>` (what owners store, shared: LABaPe name `environment/<id>/user/<name>`), `env/<name>/vm/<vm>/<name>` (owners', for one VM: `uservm/<vm>/<name>`) and `env/<name>/accounts/...` (everything LABaPe generates: Administrators per VM, the domain admin, recovery pairs, the environment's own accounts, the credentials sheet, the inventory). Names are unique and an environment's row stays after a destroy, so a folder never changes hands. Other stores keep LABaPe's names. |
| Store calls | `stores.ext_get` / `ext_put` / `ext_delete` apply the layout; every direct store call goes through them (`put_secret`, `get_secret`, `move`, the destroy and link clean-ups). A secret still under its pre-V1 name is moved when it's read (written at the new place, read back, old copy deleted); a write deletes any old copy. LABaPe's own policy (`labape/data/*`) already covers `env/`. |
| One place per environment | A new environment secret goes to the store the environment's secrets are already in (`security.env_store`, the most used), and only an environment without any follows Settings → Secret stores' default. So a moved environment stays together, which OpenBao's folders and policies (V1b, V2) rely on. |
| Page | `api/env_secrets.py`, `EnvSecrets.tsx` on the environment page, for owners and admins (others 403; those who can't see the environment 404): the secrets in three groups (stored by owners, shared; for one VM; LABaPe's), each with its VM, last change, store and place (`env/lab2/...`), never a value in the list. **Show** and **Copy** fetch the value (`POST .../secrets/reveal`); **Store a secret** (name, the whole environment or one VM, value), **Replace** and **Delete** for the owners' own. LABaPe's generated ones change only through rotation (Delete answers 409); a deploy's or rotation's attempt (`.pending`, `rotation-candidate/`) isn't listed. Audit: `environment.secret.show|copy|store|replace|delete` with the name, never the value. A destroy clears `user/` and `uservm/` too. |
| Verified | pytest (the layout, an old name moved on read, a write leaving no old copy, a new secret joining its environment's store, the API's permissions, validation, listing, reveal, replace, delete and audit). Live on kvm1: lab2's nine secrets moved to OpenBao (`live/env_secrets_move.py`), all at `env/lab2/accounts/...` with the same values and nothing left under `environment/3/`; secret checks for both Administrators, both recovery admins ok after the move; an owner secret shared and one for `l2dc1` stored, replaced, read back and deleted (`live/env_secrets.py`), landing at `env/lab2/user/` and `env/lab2/vm/l2dc1/`. |

### 49.2 V1b: OpenBao's own UI and command line, with the stack's sign-in (built)

| Area | As built |
|---|---|
| Turning it on | `LABAPE_OPENBAO_PEOPLE=yes` and `LABAPE_OPENBAO_BIND=0.0.0.0` in `.env`, then `stores/stores.sh setup`. OpenBao's UI is on (`config.hcl`); the port is published on every address (a single host address would drop the loopback publish the host's own tools use: `stores.sh` refuses one). `configure_people` makes a confidential OIDC client `labape-openbao` in the Authentik installed with LABaPe (`sso.py client_add`; its secret in `stores/secrets/openbao_oidc_secret`, passed by environment only), mounts `auth/oidc` (discovery at that application's own issuer, trusting the platform root), role `people` (user claim `preferred_username`, groups claim `groups`, redirects for the UI at each name the certificate carries and `localhost:8250` for `bao login -method=oidc`, token 8 h, policy `default` only). It tells LABaPe the address people use (`people_url`). Elsewhere: an OIDC client made by hand, `LABAPE_OPENBAO_OIDC_CLIENT_ID` (and `LABAPE_OPENBAO_OIDC_ISSUER`). |
| Who writes what | `stores.sh people-policies <env>...` (the update agent's `openbao_people` action), with the root token: each environment's policy `labape-env-<name>` from a fixed template (read its folder; create, update, delete `user/` and `vm/`), `labape-env-admins` (read every folder), and the groups `labape-env-<name>` and `labape-admins` carrying them; stale ones removed. Each name is checked against the environment name rule. LABaPe (`openbao_people.py`) keeps only the members: owners who are users as identities (the entity OpenBao made at their first sign-in, found by alias, else `labape-user-<name>` with an OIDC alias), owner groups as external groups `labape-group-<g>` aliased to the Authentik group name, LABaPe's admins (role map groups and admin bindings) in `labape-admins`. It asks the agent whenever the environments it wants differ from the policies OpenBao has (or a group doesn't carry its policy). |
| Why the split | LABaPe's policy can't write a policy or set a group's `policies`: OpenBao applies an `allowed_parameters` glob to a string only, and a comma list in a string (`labape-env-x,labape-agent`) passes it; and a LABaPe that both wrote policy text and filled the groups could grant itself anything (the agent's `labape-host/`, say). Now a compromised LABaPe can at most put itself in a group that reads an environment folder, which it reads anyway. Residual: its alias rights take any `canonical_id`, so it could alias a user name to an entity or group outside its names; nothing outside them carries a policy in the stack's OpenBao. |
| In step | The worker syncs every 5 minutes (`openbao_people.schedule`), and an admin can sync now (`POST /api/admin/openbao-people/sync`, audited). A sync also adopts secrets people store directly in OpenBao (`env/<name>/user/`, `vm/<vm>/`) into the catalog, so the environment page lists them, and forgets ones deleted there. A destroyed environment, or one moved out of OpenBao, loses its policy and group at the next sync. |
| Page | The Secrets panel shows OpenBao's address, mount and folder and the `bao` command, when people access is on and the environment's secrets are in OpenBao; its link opens OpenBao's sign-in with OIDC chosen (`/ui/vault/auth?with=oidc%2F`), as does Authentik's library tile (`meta_launch_url`). The listener doesn't ask for client certificates (`tls_disable_client_certs`; browsers offered smart cards). A newly connected stack OpenBao becomes the default store for new environments (`stack.configure`). |
| Verified | pytest (the sync keeps owners, reuses an entity OpenBao made, maps groups by name, never sends `policies`, asks the agent only when the set differs, adopts and forgets; LABaPe's policy grants no policy rights; the template's paths and name check). Live on kvm1: the setup made the Authentik client and the OIDC mount; the agent wrote lab2's policy and group; signed in with OIDC (`live/openbao_people.py`, the `bao login -method=oidc` flow) `labape-admin` got `labape-env-admins` (read lab2: 200; the platform's engine vault password: 403); `labape-dev`, made an owner of lab2 for the test, got `labape-env-lab2` (read 200, write `user/` 200, write `accounts/` 403, platform 403); the secret it wrote was adopted and then deleted through LABaPe; with the grant removed and a sync, the next sign-in had `default` only (403 everywhere). Found on the way: a single-address bind (OpenBao sealed for two minutes, fixed by `0.0.0.0` and a check), list values in `allowed_parameters`, Authentik's per-application issuer (Testing_Troubleshooting-Log.md). |

### 49.3 V2: VMs read their own secrets (built)

| Area | As built |
|---|---|
| Sign-in | Question 165 (a): a VM signs in to OpenBao with the platform certificate it already has (`labape_cert`: CN `<vm>.<domain>`, or `<vm>`) on a second listener, port 8210 (`LABAPE_OPENBAO_VM_PORT`; it asks for a client certificate, the UI's listener doesn't), through `auth/cert` (`stores.sh` enables it with people access). Each VM has a certificate role `labape-vm-<env>-<vm>`: the platform CA's chain as the trusted certificate, the VM's CN as the only allowed name, 15-minute tokens. |
| What a VM reads | Its policy `labape-vm-<env>-<vm>`: read on its own folder `env/<env>/vm/<vm>/*` (what owners store for that VM) and on exactly the shared secrets `env/<env>/user/<name>` whose VM list names it (R21: a secret shared by app1 and app2 is readable by those two only; nothing else in the folder). |
| Who writes it | As in V1b, never LABaPe: `stores.sh vm-policies FILE` (root token, fixed template, every environment, VM, CN and secret name checked) through the update agent's `openbao_vms` action, which also removes the policies and roles of VMs no longer listed. LABaPe (`openbao_vms.py`) builds the list (deployed environments in OpenBao, each VM with its CN and readable names) and asks whenever it changes (a digest in setting `openbao_vms.requested`; an admin's **Sync now** asks again). |
| VM lists | `Secret.vms` (migration 0028) for shared secrets, set on the Secrets panel's Store/Replace (**VMs that read it themselves**; Replace with an empty value changes only the list). Kept in LABaPe's catalog rather than OpenBao's custom metadata, which LABaPe's own policy can't write. |
| On the VM | Role `labape_secret_client` (after the platform certificates; host variable `labape_secret_client` from LABaPe: url, role, mount, environment, VM). Linux: `labape-secret get NAME` (`/usr/local/bin`, linked in `/usr/bin` for sudo's `secure_path`), as root (the certificate's key is root's), the token in a 0600 header file and revoked after. Windows: `Get-LabapeSecret NAME` (module `LabapeSecret`, ASCII for Windows PowerShell 5.1), elevated, with the machine's "LABaPe host certificate". Both try the VM's own folder, then the shared secrets. |
| Verified | pytest (the desired list per VM, asking only on a change, the host variables, the API's VM list, the template's paths and checks, the helpers). Live on lab2: `stores.sh setup` published 8210 and enabled `auth/cert`; a shared `v2-shared` for `l2lin1` and an own `win-only` for `l2win1`; the agent wrote 3 VMs' policies and roles; after a redeploy `sudo labape-secret get v2-shared` on `l2lin1` returned the stored value (hash compared) and `win-only` was refused; `Get-LabapeSecret win-only` on `l2win1` returned its value and `v2-shared` was refused. Both secrets removed afterwards and the policies rewritten. Found on the way: `win_copy` doesn't create the module folder; sudo didn't find `/usr/local/bin`. |

## 50. Access, mail, updates and files (the round of Planning_Access-Mail-Updates-Files.md)

Questions 167-173. Built batch by batch.

### 50.1 H1: monitoring tools on every host (built)

| Area | As built |
|---|---|
| Installer | `Prereq.Packages` installs `smartmontools`, `lm-sensors` and `ethtool` on every profile (the web-only profile, a stack host such as the Pi, had none of them; the RHEL family gets `lm_sensors` and `ethtool`), then runs `sensors-detect --auto` once (marker `/var/lib/labape/sensors-detected`) so `sensors` finds the chips without questions. |
| Existing hosts | A KVM or stack host's access-account setup (`hostadmin.SETUP`, run by **Set up access** / **Prepare**) installs the same three where missing and runs the probe once, so existing hosts get them without reinstalling. |
| Verified | pytest (both package lists and the setup script). Live: access setup on nkvm1 (host record 2) left `smartctl`, `sensors` and `ethtool` installed and the probe marker written. kvm1 and the Pi get them at their next access setup or Prepare. |

### 50.2 E1: paging and search on the environment's Secrets (built)

`EnvSecrets.tsx`: a search box (name, VM, place in the store, rotation) and filters for group, VM and store, over the whole list; then pages of the user's own size (`Paging.tsx`, as every list since L1), the groups' headings kept for the rows on the page; **Clear** resets them. The API is unchanged (the list is small and held in the browser). Verified: the UI built (type-checked) and deployed on kvm1.

### 50.3 F1: files on each KVM host, and the free-space alert (built)

| Area | As built |
|---|---|
| Files path | `KvmHost.files_path` (migration 0023); empty means beside the VM folder (`filedist.files_path`: the path up to `/VMs` with `/Files` instead, `/data/VMs/LABaPe` -> `/data/Files`, else the VM folder's parent's `Files`). Under it `ISOs/`, `Installers/`, `Other/` (`CATEGORY_DIRS`). Jobs on a host that isn't the primary (and doesn't read a share) copy what they need there instead of the hidden `.labape-isos` / `.labape-files` caches; a copy still in an old cache is moved into place on first use (`ensure(legacy=...)`). The primary keeps the stack's file locations (Settings → File locations); the host page shows the folder in use (`files_dir`). Linked stacks' backups stay in the hidden cache (`vm_cache_dir`). |
| Creating it | `hostadmin.ensure_files_path`: through the access account (sudo), the folder and its three subfolders owned by the service user (beside the VM folder they'd be under root-owned `/data`), ISOs labelled `virt_content_t` where SELinux runs. Called when the path changes and before a job copies files to the host. The installer has a `FilesPath` answer (same default; `Storage.Dirs` creates the folders), and **Prepare** passes it and records it on the host. |
| Changing it | On the host's settings (**Files path**; empty = the default). Saving a different path creates the new folders and queues `host.files_move`, which moves each subfolder's contents and removes the old folders it can (one directly under a root-owned folder stays, empty). |
| Free-space alert | Settings → Monitoring's **Free space below (%)** (`disk_free_pct`, default 20) replaces "Disk full at (%)" (`disk_pct`, 85, stack hosts only; KVM hosts had a fixed 90% full). Each host can set its own (**Free-space alert (%)** on its settings; empty = the global one). `hostadmin.warnings` applies it to every host's filesystems (pseudo filesystems under `/sys`, `/proc`, `/dev`, `/run` left out): a warning below the threshold, an error below a quarter of it; the host page and the alerts (dashboard, syslog, email) use the same rule. |
| Verified | pytest (the path rule in the app and the installer, an old cached copy moved not copied, the move script (Linux), the warning levels, pseudo filesystems, the per-host threshold in the alerts). Live on nkvm1: its files path is `/data/Files`; set to `/data/LabFiles` the folders were created (owned by `kvmsvc`) and the move job ran; a test file in `Other/` moved back to `/data/Files/Other` when the path was reset; the empty `/data/LabFiles` was left (root-owned parent) and removed by hand. kvm1's and nkvm1's filesystems are far below the threshold (no warnings). |

### 50.4 E2: connect as one of the environment's accounts (built)

| Area | As built |
|---|---|
| Who connects where | `remote.account_of` / `account_targets` (question 167): a VM's own local account (`vm/<vm>/windows_admin`, `windows_admin2`, `account/<name>`) RDP to that VM if it's Windows, SSH if it's Linux; a domain account (`domain_admin`, `domain_admin2`, `account/<name>` of kind domain) RDP to the Windows member servers, the domain controllers too only for domain admins (the domain's two admins, or an account in Domain Admins), and SSH to every Linux VM (they join the domain whenever it has a controller). Other secrets (sheet, inventory, owners' secrets, attempts, the environment-wide bootstrap password) aren't accounts. |
| Session | `POST /api/remote/sessions` takes `account` (the secret's name); `vm_target` signs in with that account's user name and stored password: RDP with the NetBIOS domain for a domain account, SSH as `user@domain` (sssd's fully qualified names; the domain from the inventory's `all: vars:`) or the local name, by password (no key). The password is filled in on the server and never reaches the browser; the session row and audit entry name the account (migration 0024). Owners and admins only, recorded when Settings → Remote access says so, as every session. |
| SSH by password | Clones have `ssh_pwauth: false` (cloud-init). `linux_common` adds `/etc/ssh/sshd_config.d/10-labape-accounts.conf` (`PasswordAuthentication yes`, sorted before cloud-init's, checked with `sshd -t`) and reloads sshd; LABaPe's admin user stays key-only (its password is locked). Existing environments get it at their next **Redeploy**. |
| Page | The Secrets panel's rows that are accounts have **Connect**: a menu of `<vm>: RDP|SSH`, opening the same session page as a VM's own Connect buttons (`remote_session.py --as` for scripts). |
| Verified | pytest (who connects where, the credentials each session uses, a session row naming its account, a refused DC for a domain user). Live on lab2 after a redeploy (the sshd drop-in), through guacd (`remote_session.py --as`): RDP to `l2win1` as its own Administrator, RDP to `l2dc1` as the domain Administrator, RDP to `l2win1` as domain user `jesse` (Security log 4624, logon type 10, `LABAPE\jesse`), SSH to `l2lin1` as `jesse` (sshd: "Accepted password for jesse@labape.test"). Found on the way: the domain's names are in the inventory's `all: vars:`, not on the hosts, so the first SSH went as a bare `jesse` ("invalid user"). |

### 50.5 E3: desktop sessions on Linux VMs, xrdp and VNC (built)

| Area | As built |
|---|---|
| Choosing | A Linux host group's `remote_desktop: [xrdp, vnc]` (question 168 c): **xrdp (RDP)** / **VNC** on the new-environment form, and the environment page's **Desktop sessions (Linux)** panel for an existing one (`PUT /api/environments/{id}/desktops`, owners; Windows groups refused); a Redeploy sets them up. |
| On the VM | Role `linux_remote_desktop` (site.yml, Linux hosts, after `linux_common`, only where asked): XFCE (EPEL `@Xfce` on the RHEL family, `xfce4` on Debian; `/etc/sysconfig/desktop` picks it); xrdp (PAM, so local and domain accounts); on a domain-joined VM `ad_gpo_map_remote_interactive = +xrdp-sesman` in sssd.conf (sssd denies unmapped PAM services, so domain accounts were refused on RDP while SSH worked); TigerVNC with one display per local account (`/etc/tigervnc/vncserver.users`, `vncserver@:N` / Debian `tigervncserver@:N`, `localhost=no`, `securitytypes=VncAuth`, `session=xfce`), each account's VNC password its own password (`vncpasswd -f`, written to `~/.vnc` and `~/.config/tigervnc`); firewalld or ufw opened for 3389 and 5900+N. |
| What LABaPe passes | `linux_desktop.write_host_vars` (with the A2 passwords, 0600): `labape_remote_desktop`, `labape_vnc_users` (the environment's local accounts on that VM in name order: display 1, 2, ...; their per-VM passwords) and `labape_desktop_ports`. |
| Connect | `remote.account_targets`: a local Linux account SSH, RDP where xrdp runs and VNC on its own display; a domain account SSH and RDP (xrdp) on every Linux VM with xrdp. RDP to a Linux VM signs in as `user@domain` (sssd) with no RDP domain; VNC goes to port 5900+display with the account's password. |
| Verified | pytest (what each VM offers, the displays and ports, the host variables, Connect's choices and the VNC/RDP parameters, the API for an existing environment). Live on lab2: local account `deskuser` added on `linux_server`, xrdp and VNC on group `l2lin`, redeployed (job 217); through guacd as `deskuser`: VNC on `:1` (5901), RDP (xrdp: "Access permitted ... session opened"), SSH ("Accepted password"); as domain user `jesse`: RDP after the sssd fix below (job 218; xrdp: "session opened for user jesse@labape.test"). |

### 50.6 M1: a mail server per environment, Mailpit (built)

| Area | As built |
|---|---|
| Catalog | Service `mail_server`, "Mail server (Mailpit)", Linux role `mailpit` (allow-listed in `catalog.SERVICE_ROLES` and site.yml's service-roles play); in the seed, and added to existing catalogs by migration 0025. Question 169 (b): one per environment, on the Linux VMs whose group has it. |
| On the VM | Role `mailpit`: the latest release binary (GitHub, amd64/arm64; downloaded with a 120 s timeout and retries, then unpacked with `tar`, which it installs), user `mailpit`, systemd unit (`CAP_NET_BIND_SERVICE` for port 25): SMTP on 25 accepting any client (and any AUTH), STARTTLS and the web UI/API on 8025 over HTTPS with copies of the VM's platform certificate, the UI behind `--ui-auth-file` (user `mailpit`), messages in `/var/lib/mailpit` (newest 5000 kept); firewalld/ufw opened for 25 and 8025. |
| Passwords | `mail_server.host_vars` (with the other per-VM host variables, 0600): the UI password, generated once and kept as `environment/<id>/vm/<vm>/mailpit` (on the environment's Secrets), passed as an htpasswd `{SHA}` line. |
| Relay | Off by default: Mailpit catches everything and sends nothing. Settings → **Lab mail relay** (Environments section; `lab_mail_relay`, password write-only in the store) names an SMTP account of its own (the VMs' owners have root there, so not Settings → Email's) and the recipients that may leave (addresses or `@domain`); a redeploy writes `relay.yaml` and `--smtp-relay-matching` (an anchored regex of exactly those) on each mail server. |
| Page | The Secrets panel shows each mail server's SMTP address, its web UI link and user, and what's forwarded. Other VMs aren't reconfigured: applications and tests point at the address shown. |
| Verified | pytest (the catalog item, the htpasswd line, the anchored relay regex, host variables with a stored UI password, the relay only when turned on, the settings API keeping the password write-only and checking the allow-list, the page's mail info). Live on kvm1: environment `mailtest` (one Rocky 9 VM `mail1` with the service); `live/mail_check.sh`: a message sent to `mail1:25` with STARTTLS (checked against the platform CA), Mailpit's API refusing a request without the password (401) and listing the message with it. The relay wasn't tried live (it needs an outside account). |

### 50.7 U1: Catalog → Updates (built)

| Area | As built |
|---|---|
| Sources | `update_sources` (migration 0026): name, kind, settings, the installers' OS targets, versions to keep (default 3), on/off, last check, newest version, problem, and what's kept (newest first: version, file, name, when). Kinds (`engine/package_updates.py`): **dotnet** (Microsoft's `release-metadata/<channel>/releases.json`: runtime, hosting bundle or SDK for a runtime ID; SHA-512 checked), **github** (latest release, the asset matching a pattern; SHA-256 checked where GitHub publishes the asset's digest), **url** (a fixed link; the version from the address it redirects to, `version_regex`, else a fingerprint of its ETag/Last-Modified), **page** (a page linking the newest version: `link_regex`, group 1 the link, group 2 the version), **chocolatey** (a community package's newest `.nupkg`). Admins add, change and remove them (question 170: "an option to add new ones"); removing one leaves its files. |
| Seeded | Once at start (`package_updates.seed`, marker `package_updates.seeded`, so a deleted one stays deleted): .NET 8 and 10 runtime, hosting bundle and SDK, Chrome's enterprise MSI, KeePassXC's MSI, PuTTY's MSI; all for Windows (question 171; Linux installs from the vendors' repositories, P1). |
| Checking | Job `files.update_check`: every enabled source, or one; the worker queues it weekly (Settings `package_updates`: day and hour UTC, default Sunday 03:00; the first one right away) and **Check all now** / a row's check. A new version is downloaded with Files → Download's code, now `downloads.fetch` (each redirect checked, resumed, the vendor's hash checked, the same content refused) into Files → Installers with the source's OS targets; the stored name is the vendor's, with the version added when it has none. Beyond `keep`, the oldest copies are deleted. |
| Page | Catalog → **Updates**: the schedule (weekly on/off, day, hour), each source with its newest version, how many are kept (the list in a tooltip), last check and problem; check, change, remove; **Add a source** with each kind's fields. API `/api/package-updates` (list for everyone, changes for admins). |
| Verified | pytest (each kind against a mock site, the stored name, a check downloading new versions and deleting beyond `keep`, nothing new, the weekly schedule, the API's checks, the seed). Live on kvm1: the first weekly check ran right after the deploy and downloaded all nine seeded sources (.NET 8.0.32 / 8.0.426 SDK, 10.0.13 / 10.0.402 SDK with their SHA-512s, KeePassXC 2.7.12 with GitHub's SHA-256, PuTTY 0.85, Chrome by ETag); a check of PuTTY again: "0.85 is the newest; already have it". The image build's Packer plugin installs now retry (a reset connection crashed Packer). |

### 50.8 P1: the round's applications (built)

| Area | As built |
|---|---|
| Applications | `catalog.P1_APPS` (in the seed; existing catalogs get them from migration 0027): .NET 8 and 10 runtime, hosting bundle (on Linux the ASP.NET Core runtime) and SDK; Chrome; KeePassXC; PuTTY; Claude Code. None is installed unless chosen (question 171: Claude Code "an option"). |
| Windows | An installer may be `source: update` with `update_source` (a Catalog → Updates source, U1): `runner.resolve_update_installers` turns it into the newest downloaded file (a local installer, staged and copied like any other) for the deploy; with none downloaded yet the installer is left out and Chocolatey installs instead. EXE installers now install too (`windows_common`: `args`, return codes 0, 3010, 1638); a local MSI needs no product code (read from the file). |
| Linux | The vendors' repositories: .NET from the RHEL family's AppStream, or Microsoft's `microsoft-prod` repository on the Debian family (new `repos/microsoft-prod.yml`); Chrome from Google's (`repos/google.yml`, x86-64); KeePassXC and PuTTY from EPEL or the distribution. |
| npm, kept up to date | A new install method, `npm: <package>` (Claude Code: `@anthropic-ai/claude-code`). Linux: Node.js (the RHEL family's `nodejs:22` stream; the default is too old), the package installed globally, `/etc/cron.weekly/labape-npm-update` keeps it current. Windows: Chocolatey's `nodejs-lts` (and Git, which Claude Code needs), the package installed into `C:\Program Files\nodejs` (on every user's PATH) through PowerShell (npm is a `.cmd`, which `win_command` can't start), and a weekly scheduled task as SYSTEM updates it (`Register-ScheduledTask`: the image has no `community.windows`). |
| Verified | pytest (the applications and their definitions, `update`/`npm` validation, the newest download or else Chocolatey). Live: environment `p1lin` (Rocky 9) with .NET 8 runtime, .NET 10 SDK, KeePassXC, PuTTY, Chrome and Claude Code deployed: `dotnet` 8.0.31 and SDK 10.0.112, KeePassXC 2.7.12, PuTTY 0.84, Chrome 155, `claude` 2.1.300, the weekly cron job. On `l2win1`, the role's own Windows tasks with Catalog → Updates' files: .NET 8.0.32 runtime from the EXE, PuTTY 0.85 from the MSI (no product code given), Claude Code 2.1.300 in `C:\Program Files\nodejs` and the weekly task (the first try found `win_command` can't start `npm.cmd`). |

## 51. Software bill of materials and releases (built)

| Area | As built |
|---|---|
| What | `Claude_Docs/Reference_SBOM.md` (for people: every package, image, tool and installer-added package, versions, licenses, purpose, known gaps) and `sbom/labape.cdx.json` (CycloneDX 1.5: package URLs, npm hashes, the dependency graph), both written by `tools/helpers/sbom.py`. |
| Sources | npm: `web/package-lock.json`. Pins: `container/Dockerfile`, the compose files, `deploy/labape_deploy/steps.py`. Everything the image really holds (Python, whose repository declares only lower bounds; Debian packages; OpenTofu, Packer, step; Ansible collections; the mirrored providers and plugins; image digests): `sbom/image-snapshot.json`, taken from the lab stack's worker container by `tools/helpers/sbom_snapshot.py` (over SSH with `LABAPE_LAB_SSH`; runs `sbom_probe.py` in the worker; changes nothing). The descriptions, purposes and third-party licenses are tables in `sbom.py`. |
| Guards | `test_sbom.py`: the two files equal what `sbom.py` would write (so a changed lock file, pin or snapshot forces a regeneration); the CycloneDX file is consistent (unique references, nothing dangling); the Dockerfile's tool pins and the compose files' images are in it, and a compose image missing from `IMAGES` fails; the snapshot parsers; `release.sh` does the SBOM after the version bump. |
| Release | `release.sh` takes the snapshot and regenerates the SBOM on the release branch, after `release_prep.py` bumps the version, so the release commit carries a current SBOM, and attaches `labape-X.Y.Z.cdx.json` to the GitHub release. It needs `LABAPE_LAB_SSH`, and the lab's worker image must be built from this clean `main` (`deploy-web.sh` stamps `git describe`; `release.sh` compares it with `main`'s and stops, saying what to run). `--skip-sbom` regenerates from the committed snapshot only (npm and pins refresh, the image-derived parts don't) and says so; `--sbom-anyway` accepts a different image and the document names the image it came from. `--dry-run` runs these checks without changing anything. |
| Verified | The automated snapshot reproduced the hand-made one (the same Python, Debian, tool, collection, provider, plugin and digest data; only ordering and the new image-version field differ). pytest (the parsers, the consistency checks, the guards). |

### 51.1 The Python lock (built)

| Area | As built |
|---|---|
| Files | `container/requirements.in` (what the image adds to `app/pyproject.toml`: `ansible-core==2.21.4`, `pywinrm`) compiled with `pyproject.toml` into `container/requirements.txt` by `tools/helpers/lock-python.sh` (uv's universal resolution, `--python-version 3.13`, `--generate-hashes`): 50 packages for Linux on amd64 and arm64 at once, with markers for the platform-specific ones (Windows' `sspilib`, `tzdata`). `--upgrade` moves everything to the newest allowed. |
| Build | `container/Dockerfile` installs `pip install --require-hashes -r requirements.txt` into the venv, then LABaPe itself with `--no-deps`, so nothing is resolved at build time. The Dockerfile's `ANSIBLE_CORE_VERSION` argument is gone: the pin is in `requirements.in`. |
| Effect | The venv is built with `--system-site-packages` (for `python3-libvirt`); the locked `requests`, `urllib3`, `certifi`, `idna` and `charset-normalizer` now go into the venv and shadow Debian's older copies (certifi 2025.1.31 became 2026.7.22). Packages only Debian provides (`libvirt-python`, `PyGObject`, `chardet`) stay outside the lock. The installer's `ansible-core` venv on KVM hosts still installs by range (older Pythons need other versions). |
| Guards | `test_python_lock.py`: every pin has hashes; each of `pyproject.toml`'s dependencies is locked within its declared range; `ansible-core` is locked at the version `requirements.in` pins; the Dockerfile installs from the lock with hash checking. The SBOM shows the locked version beside the lab image's and says when they differ. |
| Verified | The image built with hash checking on the lab; the snapshot then showed exactly the locked versions; the secret checks (Ansible and pywinrm over WinRM to lab2's Windows VMs, with the newer `requests`) and SSH to Linux VMs worked. The probe now lists a package once (the venv's copy, which wins on the import path), after it listed both the venv's and Debian's. |

## 52. The stack's own mail server, Mailpit (built)

| Area | As built |
|---|---|
| Why | OZ and Director had no SMTP configured, so monitoring alerts and job emails went nowhere. Every stack now runs a mail server of its own that needs no account: Mailpit catches what LABaPe sends, keeps it and shows it, and can forward chosen recipients to real inboxes. |
| Service | `labape-mailpit` in the base compose profile (`docker.io/axllent/mailpit:v1.31.5`), so the installer's `compose up -d` starts it everywhere. SMTP 1025 and the web UI 8025 (under `/mail`) are reachable inside the stack only, not published on the host. Messages in volume `labape-mailpit-data` (kept 5000 messages / 30 days by default, adjustable). |
| Web page | Caddy serves it at `/mail/` in all three Caddyfiles, after `forward_auth` to `GET /api/mail/auth`, which answers 200 only for a signed-in admin (the usual 401/403 otherwise; signed out gives 401). The client's `X-LABaPe-Local` break-glass marker is stripped on that check as on the main proxy. It shares the web interface's origin; only admins reach it. |
| LABaPe's own email | `stack_mail.startup`, at every start: Settings → Email becomes `labape-mailpit:1025`, no security, no login, From `labape@<host name>` (`labape.local` when the public address is an IP), when it was never set up (off and no server named). A server an admin named is left alone; Admin → Mail has **Use this server** to point it back. Job emails stay off until Settings turns them on. In the same step an empty Settings → Monitoring "Email alerts to" becomes `alerts@<host name>`, an address with no real mailbox: alerts land in Mailpit's inbox until an admin names real recipients (and a relay forwards them). Settings → Email and Monitoring link to the Mailpit page (`/mail/`). |
| Forwarding | Settings section `stack_mail` (`StackMailSettings`): the upstream server (host, port, none/STARTTLS/SSL, certificate check, user name, PLAIN/LOGIN, password write-only in the store), which recipients (addresses and `@domain`s, or every recipient), a replacement From, and what to keep. `stack_mail.apply` writes `mailpit.env` (retention, the relay options, a revision = hash of the content) and `relay.yaml` (the server and login, 0600) into volume `labape-mailpit-config`, shared with the API; on save and at start, only when the content changed. |
| Restarting | Mailpit reads its relay options only at start and LABaPe can't restart containers, so `container/mailpit/supervise.sh` (the container's entrypoint, mounted from the repository) polls those two files every 3 s, restarts Mailpit when they change, and writes the revision to `applied` once the new process has lived 2 s. Only the known keys are taken from the env file, as text (never run). Messages survive (the database is a separate volume). The page shows **applied** or **waiting**. |
| Page | Admin → **Mail** (`Mail.tsx`): status (version, messages kept, unread, applied), **Open Mailpit**, a warning with **Use this server** when Email points elsewhere, a test email, and the forwarding form. `GET /api/admin/mail`, `POST /api/admin/mail/test`, `POST /api/admin/mail/use-for-email`; the form saves through `PUT /api/admin/settings/stack_mail`. The environments' mail servers (M1, §50.6) and their separate relay account in Settings are unchanged. |
| Updates | The update agent and `deploy-web.sh` now recreate Caddy when the Caddyfile it sees differs from the repository's: the file is bind-mounted, and git or tar replacing it leaves the container on the old inode (neither `up -d` nor `caddy reload` fixes that), which would have shown LABaPe's own web app at `/mail/`. |
| Verified | pytest (the rendered options, the recipient pattern, apply writing only on change, the first-start Email rule, status, the API's validation and access, the compose/Caddy files, and the supervisor against a fake Mailpit on Linux). Live on OZ: Mailpit healthy; Email pointed at it; `/mail/` 401 signed out and Mailpit's page for an admin; a test email caught; forwarding to a throwaway upstream SMTP server (listed: only `@lab.test` arrived, all three messages kept; every recipient: also the other; off: `relay.yaml` removed); Mailpit restarted by the supervisor each time and kept its messages. |

## 53. The operations round (the user's 2026-10-15 list; Planning_Backlog.md)

### 53.1 Host updates and reboots (H2, built)

| Area | As built |
|---|---|
| Jobs | `host.update` and `host.reboot` (`engine/hostpower.py`), run by the worker itself like `host.install`, so each has a log, the heartbeat, Cancel and an audit entry. They also run on a host not enabled for deploys. One of the two per host at a time (409). |
| Update | `apt-get update` then `apt-get -y upgrade` (config files kept: `--force-confold`), or `dnf -y upgrade`; never `dist-upgrade`. Then the host's facts are collected again. `GET /api/hosts/{id}/os-updates` lists what is waiting by the lists as they are (`apt-get -s upgrade`, `dnf check-update`), the running jobs, whether a reboot is already needed and whether this stack runs on the host; the confirmation shows it. |
| Reboot | Waits (up to 12 h) until no other job is running on the host; meanwhile `runner.claim` starts nothing else there (a running `host.reboot` blocks its host). Then `shutdown -r +1`. Another host: waits for it to go down and return (15 min) and refreshes the facts. The stack's own host (`stackmon.is_self`): the job ends once the reboot is scheduled, since the worker goes down with it. Allowed on the primary file host and the stack host (the user, 2026-10-15). |
| From the OS | **Not built.** The user asked (2026-10-15) that a reboot issued on the host wait for a running job. A systemd inhibitor lock (`systemd-inhibit --mode=block`, taken through sudo because the access account gets "Access denied") shows up in logind, but `sudo systemctl reboot` is root and ignores it (tried on Director: it rebooted). It would only stop non-root and desktop sessions. Open question 174. |
| Page | KVM host -> Dashboard: **Update** and **Reboot**, each with a confirmation (the reboot's in red), then to the job. |

### 53.2 Every stack item logs to the stack syslog (S4, built)

| Area | As built |
|---|---|
| Containers | `container/compose.yaml` gives every service but the collector (`labape-syslog`, `labape-syslog-db`) the Docker `syslog` log driver: `udp://${LABAPE_SYSLOG_BIND:-127.0.0.1}:514`, tag `{{.Name}}`, `syslog-format: rfc5424micro` (the default format leaves the host name out, so the collector showed the bridge address). UDP so a collector that is down or missing never keeps a container from starting; `docker logs` still works (Docker keeps a local copy). The throwaway Authentik (`tools/authentik-test/compose.yaml`) does the same. |
| Installer | `Stack.Services` (`services_override`) writes `logging: !override {driver: journald}` for the services on Podman (no syslog driver) and `json-file` when `InstallSyslog` is no (one block per service, so no duplicate keys with the parked-profile blocks). `LOGGED_SERVICES` mirrors the compose file; a test compares them. |
| The hosts' own logs | Already built for KVM hosts (§29.1, `host.log_forwarding`, on by default at install). The stack host registered as `pi1` (Director) wasn't forwarding; turned on 2026-10-15 (`tools/helpers/live/host_logs.py`). |
| Deploy | A changed log driver recreates every container. `deploy-web.sh` now runs a plain `compose up -d` and then `stores.sh unseal` (a recreated OpenBao is sealed); the update agent already does both (§53.1 fix). |
| Verified | OZ: all 12 containers (and Authentik) on the syslog driver; the collector holds their messages with the host name `OZ`. |
