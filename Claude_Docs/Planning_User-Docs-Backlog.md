# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Build a template: a **Datacenter** box (Windows Server; Standard otherwise); the template page shows and edits the Server SKU; the new-environment form has **Datacenter** for direct-ISO Windows Server groups; `build-template.sh --datacenter`.
- Template refreshes install every available Windows update, not only security and critical ones.
- OpenBao and Conjur in the stack: `Stack.Stores` / `container/stores/stores.sh` (setup, status, unseal), `stores/secrets/` backups, ports 8200/8444 on the host; Settings → Secret stores comes filled in.
- Settings → Templates: versions to keep, the monthly refresh (on/off, day, time); how dated names (`<name>-YYYY.MM`) form versions.
- Template page: Delete (refused while an environment or VM uses the template).
- Applications marked `requires_gui` (Firefox) are skipped on Windows Server Core hosts instead of failing the deploy.
