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
| 10b | File manager (chunked uploads, `/data/<type>`), ISO and installer catalogs feeding `os_iso_paths` and the software store, template library view, build/promote/refresh jobs |
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

Retiring lab1 itself waits for the owner's go-ahead.

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
