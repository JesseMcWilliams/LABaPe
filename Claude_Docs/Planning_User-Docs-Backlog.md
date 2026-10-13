# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Build a template: a **Datacenter** box (Windows Server; Standard otherwise); the template page shows and edits the Server SKU; the new-environment form has **Datacenter** for direct-ISO Windows Server groups; `build-template.sh --datacenter`.
- Template refreshes install every available Windows update, not only security and critical ones.
- OpenBao and Conjur in the stack: `Stack.Stores` / `container/stores/stores.sh` (setup, status, unseal), `stores/secrets/` backups, ports 8200/8444 on the host; Settings → Secret stores comes filled in.
- Engine secrets in OpenBao: the command line reads the vault password and VM key from the store (Command-Line.md §5 done); admin guide: `labape engine-secrets status|check`, backing up and deleting the old `engine-secrets/` and CLI copies.
- Bootstrap secrets in OpenBao: what happens at boot, `LABAPE_SECRETS_DIR`, changing the OIDC client secret by hand after the move, backing up `stores/secrets/` (secret zero) and deleting the old `container/secrets/`.
- Choosing the platform store (`LABAPE_PLATFORM_STORE`, installer `PlatformStore`) and moving between OpenBao and Conjur with `stores.sh setup`.
- Settings → Templates: versions to keep, the monthly refresh (on/off, day, time); how dated names (`<name>-YYYY.MM`) form versions.
- Template page: Delete (refused while an environment or VM uses the template).
- Applications marked `requires_gui` (Firefox) are skipped on Windows Server Core hosts instead of failing the deploy.
- Catalog → Services: the **Syslog collector** service (ports, TLS and mutual TLS, `/var/log/remote/` layout, getting a client certificate with `labape ca-sign`), and the `linux.role` field.
- The Admin area (left-hand list; Hosts and Networks are admin pages now), the filters on Jobs, Audit log and User errors (kept in the URL), the User errors export, and Catalog → Requests → New request.
