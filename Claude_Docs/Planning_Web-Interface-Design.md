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
| 10d | Secrets providers (built-in, OpenBao/Vault, Conjur) and `tools/secrets-test/` |
| 10e | Remote libvirt, multiple hosts, environments spanning hosts with best-fit placement (section 15) |
| 10f | VM storage moves (offline) |
| 10g | Environment import (migration), central repository configuration applied to all tools, bundled-Authentik profile with blueprint |
| 10h | Direct LDAP and Active Directory providers (section 12) |

Each phase ends with the stack deployed on the lab host and its features
exercised end to end, like the M1-M6 work.

## 18. Open questions

Remaining for this design (Claude_Docs/Planning_Questions.md): 29 (Kerberos timing),
34 (Packer builds with a remote app), 35 (backups), 36 (log and audit
retention), 38 (notifications). None blocks phase 10a.

## 19. Phase 10a as built

Phase 10a was deployed on the lab host under Docker and exercised end to
end. Findings are in Claude_Docs/Testing_Troubleshooting-Log.md, "M10
phase 10a". Where the build differs from, or adds detail to, the
sections above:

**Code and UI**

| Area | As built |
|---|---|
| Backend | `app/labape/` with FastAPI, SQLAlchemy 2 and Authlib. Tables come from `create_all`; Alembic arrives with the first schema change. `pytest app/tests` runs the API, break-glass, permission and claim tests on SQLite. |
| Web UI | React 19, TypeScript and Vite, with **MUI** as the component library (question 9). Pages: sign-in, break-glass, environments (list, create form, detail with deploy/destroy/credentials), jobs (list, detail with live log, cancel, retry), hosts, audit. |

**Jobs and state**

| Area | As built |
|---|---|
| Job claim | A `pg_advisory_xact_lock` serializes claims so per-host `concurrency_limit` holds across workers, plus `FOR UPDATE SKIP LOCKED`. Workers heartbeat every 10 s, and a job whose worker stops for 2 minutes is marked failed. The global limit and Packer's double weight wait for 10b. |
| Working directory | Per **environment**, not per job: `/var/lib/labape/environments/<name>/engine`, refreshed from the image's engine snapshot before each job. That keeps `.rendered/` answer files and `.terraform` between deploy and destroy. Only one job per environment runs at a time, so environments never share a directory. It's removed after a successful destroy. |
| Inputs | The runner writes `environment.yml` (the instance base from `/etc/labape` plus the host's storage paths) and `tofu/environments/labape-ui.tfvars`. The tfvars holds the host groups, `static_ip_offset_start`, the host's bridge and `libvirt_uri`; a `-var-file` beats the vault's `TF_VAR_libvirt_uri`. It also copies the software and directory manifests and the vault, and adds `labape_backend_override.tf` (`backend "pg"`, with `PG_CONN_STR` from the environment). |
| Script changes | `destroy.sh` gained `--yes`, `--delete-workspace` and a `tofu init` when `.terraform` is missing. `deploy.sh` and `destroy.sh` honor `LABAPE_SSH_PRIVATE_KEY_PATH`. `safe-undefine.sh` skips sudo when it's root. `create-from-template.sh` raises a disk smaller than its template to the template's size. |
| Outputs | `hosts.generated` is stored on the environment and shown to anyone who can see it. The full inventory and `credentials.generated` go to the encrypted `secrets` table and are deleted from disk. |

**Security and packaging**

| Area | As built |
|---|---|
| Break-glass host-only | Section 12 assumed a loopback client IP. Behind the runtime's port publishing, a host-local browser arrives from the bridge gateway instead. So Caddy runs a second listener published on `127.0.0.1:8443` that sets `X-LABaPe-Local: 1`, and the public listener strips that header. The API also refuses cross-origin writes, except from that listener. |
| Image | Debian trixie with OpenTofu 1.12.6 and Packer 1.16.1 (checksum-verified), and ansible-core 2.21.4 plus pywinrm and the four collections. The venv uses `--system-site-packages` for virt-install. OpenTofu providers are mirrored into `/opt/labape/tofu-mirror` (`TF_CLI_CONFIG_FILE`), and Packer plugins are pre-installed. About 950 MB. |
| Stack | `container/compose.yaml` (postgres, labape-api, labape-worker, caddy; `init: true`), `container/setup.sh` (`.env` and generated secrets), and Quadlet units in `container/quadlet/`. The containers run as root in rootful Docker. Rootless Podman and the Quadlets are written but not yet exercised on the lab host. |
| Hosts | Only local `qemu:///system` hosts are accepted; `qemu+ssh` is refused until 10e. |

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

## 22. Dashboard, list pages, installer profiles, platform CA, downloads (planned 2026-10-11)

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

## 24. Secret rotation (phases R1 to R4 built)

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
| OIDC client secret | privileged | 24 h with an elevated Authentik token, otherwise a reminder | |
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
| Rotators | `builtin_data_key` (24 h): re-encrypts every secret with a new data key and verifies before committing. `worker_ssh_key` (24 h): adds the new key on each enabled remote host with the old one, checks it logs in, then removes the old key there and swaps it here (undone on failure). `directory_token` (read-only, 7 days): sets a new key on the Authentik token with the elevated token, checks group search with it, and puts the old key back if that fails. The other seven secrets are listed as manual or reminder, with notes naming the phase that brings their rotator. |
| UI | Top bar → Secrets: every secret with its class, store, interval, last and next rotation and status (a "restart" chip, and the reason on hover when failed), plus Add elevated account. Each secret's page shows how it rotates, the restart note, the last job, its settings and Rotate now (confirmed). |
| Test Authentik | A `labape-rotator` service account and token. Authentik checks `POST /tokens/<id>/set_key/` as `add_token` on that token object; the `set_token_key` its docs name isn't checked in this version. The blueprint importer can't assign object permissions to a role, so `deploy.sh` grants them with `ak shell`, on the directory token only. Both tokens use `state: created`, so the blueprint never resets a rotated key. |
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
| Vault password | The agent copies the vault, runs `ansible-vault rekey` on the copy in a one-off worker container, checks the copy opens with the new password, then swaps the vault and its password back to back and checks again (put back on failure). A job that starts mid-swap would copy a mismatched pair, so the runner checks its copies open and copies again (up to five tries). |
| VM key | The agent makes an ed25519 key beside the old one and runs `labape push-vm-key`. For each deployed environment it adds the new public key to the Linux VMs' admin account (signing in with the current key or one of the previous ones), checks the new key signs in, and removes keys older than the window. Then the old key moves to `ssh/history/`, the new one takes its place, and both are copied into the service user's `~/.ssh` (`labape_bootstrap`, history in `labape-history/`) so the command line stays in step (question 90, option b). **7 previous keys** are kept; jobs offer them after the current key (`ANSIBLE_SSH_COMMON_ARGS -i ...`), so a VM that was off during a rotation is still reached. A VM that can't be reached is reported, not fatal. Environments on remote KVM hosts are skipped: the stack can't reach their VMs. |
| Reinstall triggers | The Linux answer files and clone seeds carry the public key, and the VMs' reinstall triggers hash them, so a rotated key would reinstall every Linux VM on its next deploy. The `vm` module now records the key each VM was created with (`terraform_data.creation_ssh_key`, `ignore_changes`) and hashes the files with that key instead. New VMs still get the current key. Upgrading only adds that record (VMs built before R2 all have today's key, so their hashes don't change; a VM whose key had already rotated would be reinstalled once); a rotated key changes nothing; a real seed edit still reinstalls. All three were checked with a throwaway OpenTofu config, then on `lab2`. |
| Lab passwords (R4) | Policies `env.<id>.windows_admin` (the local Administrator on Windows hosts other than DCs) and `env.<id>.domain_admin` (the domain Administrator, changed on one DC) are created for each deployed environment that has such hosts, weekly by default. Owners and admins change the interval (in days) or turn it off on the environment's page; **DSRM never rotates**. A policy is skipped while its environment isn't deployed, and kept so its settings survive a redeploy. Environments on remote KVM hosts get no policies. |
| How a password changes | WinRM's Basic auth sends the password with **every request**, so a module that changes the password of the account it's connected as fails on its next request although the change worked (Reference_Credentials.md §9). The change therefore runs detached (`async: 120, poll: 0`) through a generated playbook, with the new password in a 0600 variables file. Signing in decides the outcome: `win_ping` with the new password (first after 10 s, then every 15 s, for up to 75 s, so a lockout policy sees few failures), then with the old one. **All or nothing:** if any host didn't change, the ones that did get their old password back. The candidate password is stored first (`environment/<id>/rotation-candidate/<kind>`), so neither a crash nor a host where neither password works loses it. |
| What follows a change | The stored inventory, the owners' credentials sheet (the Password line of the matching section; the sheet now has a separate "Domain administrator" section) and the environment's password overlay `environment/<id>/passwords`. Each job rewrites its own vault copy with the overlay (`overlay_vault`), so later deploys, joins and new hosts use the rotated passwords. The DC's inventory entry uses `domain_admin_password` (`LABAPE_DOMAIN_ADMIN_PASSWORD` for `generate-inventory.py`), so the two no longer have to be equal once rotated. Destroying an environment clears its overlay: a new DC is built from the vault's matching pair. One job runs per environment at a time (the claim skips an environment with a running job). |
| Linux admin account (question 91) | Settings → VM accounts → **Linux admin user** (default `labape`; a valid Linux name, not a system one). It's recorded in the environment's spec when it's created, so a change applies to new environments only. It flows into `environment.yml`, the OpenTofu backends' `linux_admin_user`, the answer files and clone seeds (`${admin_user}`), the inventory's `ansible_user` and the credentials sheet. Packer templates keep their build account, `labape`. With `labape`, every answer file renders byte-identically to before, so existing VMs aren't replaced. |
| Verified | Live on the lab host. The vault password rotated with maintenance off, and the worker opened the new pair. The VM key rotated: added and checked on `lab2`'s Rocky VM, swapped in both places, the previous key in history. The first attempt failed (ansible-core has no `json` callback; the old key stayed, as designed). On `lab2` (DC, Windows member, Rocky): the Windows Administrator and then the domain Administrator rotated, and the credentials sheet followed. The first Windows attempt hit the Basic-auth problem above: the password changed but was reported failed, so it was lost. `l2win1` was put back through the domain admin over NTLM, which led to the detached change. A full redeploy of `lab2` with all four rotated secrets then succeeded on every host, but it **reinstalled `l2lin1`** because its seed hash included the rotated key, which led to the creation-key fix above. Rotating once more before deploying the fix reinstalled it a second time, as expected for a VM whose key rotated before the record existed (only possible on this lab, since R2 ships with the fix). After a third rotation, a redeploy replaced nothing: only the seed file was rewritten, and every VM kept its domain ID. pytest: 93. |

