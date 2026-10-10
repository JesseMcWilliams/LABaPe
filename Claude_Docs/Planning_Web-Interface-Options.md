# Web interface: options and decisions

Research and decisions for Claude_Docs/Design_System-Overview.md §20 / M10
(2026-10-10). The environment-template feature itself is designed in
Claude_Docs/Planning_Environment-Templates.md; this doc covers how the interface is
built, deployed and secured. Remaining choices are in
Claude_Docs/Planning_Questions.md.

## Decisions (2026-10-10)

1. **The web interface is the primary way to use LABaPe.** The scripts
   (`deploy.sh`, `destroy.sh`, `build-template.sh`, `promote-to-template.sh`,
   `refresh-template.sh`) stay as the engine underneath and stay usable
   from a shell, but users work through the web UI and its API.
2. **One custom application, no Semaphore UI.** A single LABaPe app
   provides the UI, the REST API and the job runner. Semaphore was
   dropped once the UI became primary: it would have meant two web
   interfaces and two sets of accounts and permissions, and it doesn't
   support SAML (see "Options considered" below).
3. **Authentication through Authentik.** The app is an OIDC client of
   Authentik (plus direct LDAP/AD, decision 24, and break-glass access by
   command, decision 25). Authentik provides:
   - local user accounts;
   - SAML 2.0 to upstream providers (Entra ID, ADFS, Okta, ...);
   - OIDC to upstream providers (Entra ID, Google, GitHub, ...);
   - LDAP / Active Directory as a user source;
   - multi-factor authentication (TOTP, WebAuthn passkeys/security keys);
   - Kerberos (Windows integrated sign-on), optional.
4. **API tokens** for scripts and CI, per user and per service, issued
   and checked by the app (the API replaces the CLI as the main entry
   point).
5. **Roles from groups.** Authentik/AD groups map to app roles, so access
   is managed in one place: admin, template editor, deployer, file
   manager, viewer (see decision 9 for ownership on top of roles).
6. **Delivered as containers, Docker primary, Podman supported.** A
   Compose stack runs the LABaPe app, PostgreSQL and Authentik. Docker
   (Compose v2) is the supported, tested runtime; Podman (with
   `podman compose` or Quadlet units) is supported and tested, but Docker
   is the reference. The image is built so it doesn't depend on
   Docker-only features (no Docker socket access, standard OCI image,
   explicit volume and device mappings).
7. **Runs on the KVM host by default, remote libvirt also supported.**
   See "Deployment" below.

### Further decisions (2026-10-10, second round)

8. **Users:** admins and developers. A small team; no multi-tenant
   separation beyond roles and ownership.
9. **Ownership on top of roles.**
   - Every **environment** has an owner. Deployers change or destroy only
     environments they own; admins can act on any.
   - Every **environment template** has owners (edit, share, delete) and
     users (see and deploy from it). Template editors create templates
     and own what they create.
   - Owners and users can be granted **individually and by Authentik
     group**, both on the same object.
   - Credentials of a running environment are visible to its **owner and
     admins only**.
10. **File manager.** The UI manages the files the engine depends on:
    OS ISOs, application installers (the software store) and other files
    (e.g. scripts, certificates, licence files). Upload, download, list,
    delete, with checksums and who/when metadata. **Admins and file
    managers** upload and delete; everyone else can use the files.
    Uploads are chunked and resumable (Windows ISOs are 5-8 GB; no fixed
    size limit). Files are stored under **`/data/<type>`** by default
    (e.g. `/data/isos`, `/data/installers`, `/data/files`), each type's
    location configurable; the engine reads the same paths, so the CLI
    keeps working.
11. **Environment templates are git-backed YAML, with a built-in
    repository.** The app keeps templates in its own git repository inside
    its data volume (works with no network access at all). Optionally it
    syncs with an external remote (GitHub, GitLab, Gitea, ...) when one is
    configured and reachable: push on save, pull on request, conflicts
    surfaced in the UI rather than auto-resolved. For disconnected sites,
    templates can be exported and imported as a bundle (git bundle or
    archive). The repository is the source of truth; the database only
    indexes it for search and permissions.
12. **Authentik: use an existing instance by default.** LABaPe connects
    to an Authentik instance the organisation already runs (an OIDC
    provider and application are configured there). If none is
    available, the stack can deploy and configure its own Authentik
    (optional Compose profile, with the LABaPe OIDC application created
    automatically).
13. **Day-one sign-in: Authentik local accounts.** SAML, upstream OIDC,
    LDAP/AD and Kerberos stay supported through Authentik for later.
14. **MFA is optional** (users may enrol; not enforced). Can be enforced
    later in Authentik without app changes.
15. **Migration is optional.** Most environments will be created in the
    app; an import path brings existing CLI-built environments (OpenTofu
    workspace state, `environment.yml`, vault secrets, inventory) under
    the app's management for those that need it.
16. **LAN only, over HTTPS.** Starts with a self-signed certificate;
    ACME is supported (an internal ACME CA or a public one where the
    host is reachable). The hostname is asked for at setup.
17. **Rootless Podman must work too** (in addition to rootful Podman and
    Docker). Systemd Quadlet units are provided as the Podman-native way
    to run the stack as services (see "Container runtimes").
18. **Container images: local registry by default, external optional.**
    Images are built and kept locally (a local registry in the stack, or
    the runtime's own image store); publishing to an external registry is
    optional.
19. **One central repository configuration.** A single settings area
    (file and UI) lists every external source the app and engine use, so a
    disconnected or mirrored site changes them in one place: container
    image registry, template git remote, OpenTofu provider mirror,
    Packer plugin mirror, Ansible collection source, OS package mirrors
    and Chocolatey sources. (A provider mirror would also have avoided the
    transient registry timeout seen in M6.)
20. **Up to 4 KVM hosts per LABaPe instance**, configurable (the limit is
    a setting, default 4); one environment may span several (decision 26).
21. **UI built with React** (a single-page app over the REST API);
    backend Python/FastAPI. See "UI technology".
22. **Optional secrets-manager integration.** Secrets (the vault
    password, lab/bootstrap credentials, per-environment credentials)
    can live in an external secrets manager instead of the app's own
    encrypted store: **OpenBao** (Vault-compatible API, so HashiCorp
    Vault works the same way) and **CyberArk Conjur** as the first
    providers. **Modular:** each secrets manager is a provider plugin
    behind one small interface (get, put, list, delete, test connection),
    selected per installation, so others (e.g. Azure Key Vault, AWS
    Secrets Manager, Bitwarden Secrets Manager) can be added later
    without touching the rest of the app. The built-in store stays the
    default. For testing, the repo will include a script and doc that
    deploy throwaway OpenBao and Conjur instances as containers on the
    lab host (none exists today).
23. **Configurable VM storage, with migration.** Where each KVM host
    keeps VM disks and seed ISOs is a per-host setting (default
    `/data/VMs/LABaPe`, as today), and the template library location is
    configurable too. Existing VMs can be moved to another storage
    location from the UI: offline move by default (shut down, copy the
    disk through libvirt into the target storage pool, redefine the
    domain, start) in the first version, live move (`virsh blockcopy`)
    later; a
    cloned VM's backing template must be reachable from the target, or
    the disk is flattened during the move.
24. **Modular authentication providers, LDAP and Active Directory
    alongside Authentik.** Sign-in goes through pluggable providers behind
    one interface, several enabled at once in a configured order: OIDC
    (Authentik, or any OIDC provider), LDAP, and Active Directory (LDAP
    with AD defaults: `sAMAccountName`/UPN logins, nested groups via
    `LDAP_MATCHING_RULE_IN_CHAIN`, LDAPS or StartTLS, optional Kerberos
    later). Groups from any provider map to the same roles. New providers
    (e.g. direct SAML, Kerberos/SPNEGO) are added as plugins.
25. **Break-glass access by command, not by standing password.** When
    every provider is down, an operator runs a command inside the
    container (e.g. `docker exec labape labape breakglass enable`), which
    prints a one-time password or sign-in link valid for 5 minutes
    (configurable), usable once, for a session of limited length (default
    1 hour). Running it proves control of the host or container runtime;
    the command and everything done in the session are audited. Optionally
    the one-time login is accepted only from the host itself. The same
    tool can show provider status and disable a broken provider so normal
    sign-in can be repaired. There is no permanently enabled local admin
    password.
26. **Environments can span KVM hosts.** Each host group is placed on a
    host named in the template, or the app picks one ("best fit") from
    free memory, CPU, disk in the target storage, running jobs and
    whether the needed template or ISO is already on that host. All hosts
    in one environment must share the lab network segment (the bridge).

## What the interface has to do

From Claude_Docs/Planning_Environment-Templates.md §3, in order of effort:

1. **Run long jobs in the background**: deploys (`tofu apply` +
   `site.yml`, 15-90 minutes), template builds, promotes and refreshes
   (10-90+ minutes). Needs a queue, live log streaming, cancel, retry, a
   concurrency limit per KVM host, and kept history.
2. **Author environment templates**: host groups + software manifest +
   directory manifest as one composable object, with sub-assemblies.
3. **Browse catalogs**: OS catalog, template library, software catalog,
   Windows roles/features.
4. **Show credentials** for a running environment, alongside the existing
   generated handout file.

## Architecture

- **App container** (`labape`): the web UI, REST API and job worker in
  one image. The worker runs the existing scripts as child processes,
  records each job (who, what, parameters, status, timestamps) in
  PostgreSQL, streams output to the browser (Server-Sent Events) and
  keeps the full log. Cancel stops the job's process group and leaves
  any half-built environment for the usual `destroy.sh --test` cleanup.
  Suggested stack (open question): Python with FastAPI, matching the
  repo's existing Python helpers; Authlib for OIDC.
- **The image carries the whole toolchain**, pinned per release:
  OpenTofu, Packer + plugins, Ansible + collections, virt-install and
  libvirt clients, xorriso, qemu-img, Python libraries. Upgrades and
  rollback are an image tag change.
- **PostgreSQL** for app data (jobs, environments, users' tokens, audit
  log). Authentik uses its own database in the same Postgres instance or
  a separate one.
- **Authentik** (server + worker containers) as the identity provider;
  the app is an OIDC client of it.
- **Secrets** (vault password, lab credentials, OIDC client secret) move
  from files in a home directory into app-managed secrets (container
  secrets / environment, encrypted at rest in the database where stored
  there), or optionally into an external secrets manager (OpenBao,
  Vault, CyberArk Conjur; decision 22).

## UI technology

The API has to exist anyway (API tokens, decision 4), so the choice is
how the browser side is built on top of it:

| | Server-rendered (FastAPI + Jinja + HTMX) | Vue (SPA) | React (SPA) |
|---|---|---|---|
| Languages | Python + HTML; little JavaScript | TypeScript/JavaScript front end + Python API | TypeScript/JavaScript front end + Python API |
| Build tooling | None beyond Python | Vite build step | Vite build step |
| Forms, tables, CRUD | Very good | Very good | Very good |
| Rich editors (template composer, drag and drop) | Possible, gets awkward | Strong | Strong |
| Live job logs | Server-Sent Events, simple | Server-Sent Events/WebSocket, simple | Same as Vue |
| Component libraries | Few | PrimeVue, Vuetify, Quasar | MUI, Ant Design, Chakra, many more |
| Learning curve | Lowest | Low-moderate (single-file components, built-in state/router choices) | Moderate (more choices to make: state, routing, data fetching) |
| Fit here | Fastest start, limits the composer UI | Matches the other project (shared skills, components, conventions) | Largest ecosystem, no existing use |

**Chosen: React** with TypeScript and Vite; component library (e.g.
MUI or Ant Design) to be picked in the design; backend Python/FastAPI.

## Deployment

### On the KVM host (default)

The container drives the host's libvirtd and needs:

- the libvirt socket (`/var/run/libvirt/libvirt-sock`) mounted in;
- the VM storage directory (`/data` today) mounted at **the same path**,
  because libvirtd opens disks and config ISOs at the paths the scripts
  write;
- `/dev/kvm` passed through, for Packer's QEMU template builds, which
  run inside the container;
- the template library and ISO directory under the same mount.

VMs stay on the host's bridge (libvirtd runs them, not the container);
only the app's web port and Authentik's are published. The container is
a **privileged, trusted component**: access to the libvirt socket is
equivalent to root on the host. It isn't an isolation boundary, so it
stays off the internet, behind Authentik, with the app's own role
checks. Its value is packaging: a pinned toolchain, one-command upgrade
and rollback, the same stack on any KVM host, and a host that only needs
libvirt, KVM and a container runtime.

### Remote libvirt (also supported)

The app can drive libvirt hosts it doesn't run on, over
`qemu+ssh://`, so it can live on a management VM and drive up to 4 KVM
hosts (configurable). Required changes to the engine:

- Disks and config ISOs are created **through libvirt** instead of
  written to a local `/data` path: storage-pool volumes, uploaded with
  `virsh vol-upload` (answer-file/seed ISOs) and created with
  `vol-create-as` / `backing_store` (disks, clones).
- The template library is a libvirt storage pool on each KVM host
  (already true for `labape-templates`); promote and refresh already copy
  through libvirt (`vol-create-from`).
- Packer template builds need KVM where the build VM runs. With the app
  remote, builds either run on a container host that has `/dev/kvm`, or
  run on the KVM host itself (e.g. Packer's libvirt plugin, or the build
  executed over SSH there). Open question.
- Serial console logs, screenshots and the IP pre-flight check move from
  local commands to libvirt API / SSH equivalents.
- SSH key and host-key handling for each managed KVM host.

Hyper-V is already remote (WinRM) and works the same from a container.

### Container runtimes

- **Docker** (Engine + Compose v2): primary; the reference `compose.yaml`
  is written and CI-tested against it.
- **Podman**: supported, rootful and rootless. The same compose file runs
  under `podman compose`, and **Quadlet** units are provided: Podman's way
  of running containers as ordinary systemd services, from small unit
  files (`.container`, `.volume`, `.network`) that systemd starts,
  restarts and logs like any other service, including rootless services
  under a user account that start at boot. Rootless needs the running
  user in the `libvirt` and `kvm` groups, the groups carried into the
  container (`--group-add keep-groups`), the libvirt socket and
  `/dev/kvm` passed through, and SELinux volume labels (`:z`/`:Z`) where
  enforcing; the design spells these out and CI tests rootless as well
  as rootful.

## Options considered

| | Job runner | Authoring | Catalogs | Auth | Notes |
|---|---|---|---|---|---|
| Semaphore UI | Strong | None | None | Local, LDAP, OIDC (no SAML) | Was the recommendation while the CLI was primary; dropped: a second UI and a second user/permission system once the web UI is primary |
| AWX | Strong (Ansible) | None | None | Local, LDAP, SAML, OIDC | Releases paused since 24.6.1 (July 2024); Kubernetes operator |
| Rundeck Community | Good | None | None | Local, LDAP (SSO in commercial) | JVM; less native to Ansible/OpenTofu |
| Backstage | Needs an engine | Forms, no composition model | Good | Many via plugins | Large Node platform to own |
| **Custom app + Authentik** | Built in | Built in | Built in | All via Authentik + app tokens | **Chosen** |

Identity provider: Authentik over Keycloak for its lighter footprint
(about 600 MB idle memory versus about 1.5 GB), easier admin UI and
built-in LDAP/RADIUS/proxy providers; Keycloak remains the alternative
for large federated Active Directory estates.

## Sources

- [Semaphore UI](https://semaphoreui.com/), [authentication](https://semaphoreui.com/docs/admin-guide/authentication), [OpenID Connect](https://semaphoreui.com/docs/admin-guide/openid)
- [AWX vs. Semaphore UI comparison (2026)](https://semaphoreui.com/blog/awx-vs-semaphore)
- [ansible/awx (release status)](https://github.com/ansible/awx), [Upcoming changes to the AWX project](https://www.redhat.com/en/ansible-collaborative/upcoming-changes-to-the-awx-project)
- [Rundeck open source](https://www.rundeck.com/features)
- [Backstage + Semaphore UI](https://semaphoreui.com/blog/backstage-with-semaphore), [Ansible Backstage plugins](https://github.com/ansible/ansible-backstage-plugins)
- [Authentik vs Keycloak (2026)](https://use-apify.com/blog/authentik-vs-keycloak-2026), [Keycloak vs authentik comparison](https://skycloak.io/blog/keycloak-vs-authentik-comparison/)
- [ansible-runner Python interface](https://ansible-runner.readthedocs.io/en/stable/python_interface/)
