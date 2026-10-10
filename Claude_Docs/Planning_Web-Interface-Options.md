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
3. **Authentication through Authentik.** The app implements OIDC only,
   plus one local break-glass admin account for when Authentik is down.
   Authentik provides:
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
   is managed in one place (proposed: admin, template editor, deployer,
   viewer; see the questions file).
6. **Delivered as containers, Docker primary, Podman supported.** A
   Compose stack runs the LABaPe app, PostgreSQL and Authentik. Docker
   (Compose v2) is the supported, tested runtime; Podman (with
   `podman compose` or Quadlet units) is supported and tested, but Docker
   is the reference. The image is built so it doesn't depend on
   Docker-only features (no Docker socket access, standard OCI image,
   explicit volume and device mappings).
7. **Runs on the KVM host by default, remote libvirt also supported.**
   See "Deployment" below.

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
  there).

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

The app can drive a libvirt host it doesn't run on, over
`qemu+ssh://`, so it can live on a management VM and drive one or more
KVM hosts. Required changes to the engine:

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
- **Podman**: supported. The same compose file runs under
  `podman compose`; Quadlet units may be provided for systemd-managed
  hosts. Device and socket mappings and SELinux volume labels (`:z`/`:Z`)
  are documented for Podman; whether rootless Podman is supported is an
  open question (libvirt socket and `/dev/kvm` access make rootful the
  simpler default).

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
