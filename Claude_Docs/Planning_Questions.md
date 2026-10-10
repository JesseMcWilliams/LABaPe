# Open questions

Decisions needed before the next pieces of work, numbered so they can be
answered by number. Numbers are stable: answered questions stay in place,
marked **Answered** with the decision, and the decision is also recorded
in the relevant design doc. Background: Claude_Docs/Planning_Web-Interface-Options.md
(web interface), Claude_Docs/Reference_Backend-Parity.md (KVM vs Hyper-V),
Claude_Docs/Design_Base-Images.md (templates).

## Web interface (M10)

1. ~~Who will use it, and roughly how many people?~~ **Answered (2026-10-10):** Admins and developers; a small team. (Claude_Docs/Planning_Web-Interface-Options.md)
2. ~~What authentication does it need?~~ **Answered (2026-10-10):**
   Authentik as the identity provider (local accounts, SAML, OIDC,
   LDAP/Active Directory, MFA, optional Kerberos), the app as an OIDC
   client plus a local break-glass admin, app-issued API tokens, roles
   mapped from groups.
3. ~~Should some users only deploy from templates while others can edit them, or can everyone do everything?~~ **Answered (2026-10-10):** Yes: roles admin / template editor / deployer / viewer, plus ownership (decision 9). (Claude_Docs/Planning_Web-Interface-Options.md)
4. ~~Semaphore hybrid or one custom app?~~ **Answered (2026-10-10):** one
   custom app (UI, REST API, job runner); Semaphore dropped once the web
   UI became primary (second UI and user system; no SAML).
5. ~~Where should environment templates be stored: git-backed YAML in this repo (reviewable, consistent with everything else) or the app's database?~~ **Answered (2026-10-10):** Git-backed YAML in a repository the app keeps in its own volume (works offline), optional sync with an external remote, bundle export/import for disconnected sites (decision 11). (Claude_Docs/Planning_Web-Interface-Options.md)
6. ~~Does the CLI stay first-class?~~ **Answered (2026-10-10):** the web UI
   is the primary interface; the existing scripts remain the engine the
   job runner calls and stay usable from a shell.
7. ~~Where does the web interface run?~~ **Answered (2026-10-10):** a
   container stack (Docker primary, Podman supported) on the KVM host by
   default; remote libvirt (`qemu+ssh://`) also supported so it can run
   elsewhere.
8. ~~How is it reached: LAN only, over a VPN, or from the internet?~~ **Answered (2026-10-10):** LAN only, over HTTPS; self-signed to start, ACME supported, hostname asked at setup (decision 16). (Claude_Docs/Planning_Web-Interface-Options.md)
9. ~~Any language or framework preference for the app (the suggestion is Python with FastAPI, matching the repo's helpers; the UI could be server-rendered HTMX or a Vue/React single-page app)?~~ **Answered (2026-10-10):** React single-page app over a Python/FastAPI API; comparison in the "UI technology" section. (Claude_Docs/Planning_Web-Interface-Options.md)
10. ~~Who may see an environment's credentials in the web UI: anyone who can see the environment, or only its owner/admins?~~ **Answered (2026-10-10):** Owners and admins only (decision 9). (Claude_Docs/Planning_Web-Interface-Options.md)
## KVM vs Hyper-V parity

11. Is Hyper-V still a target backend? If not, it could be marked
    "maintained as-is, RHEL family only" and the gaps left open.
12. If yes, where does closing the gaps rank against M7 (NAT mode),
    M8 (CI/lint), M9 (certificate authority) and M10 (web UI)?
13. If yes, is the suggested order right (interface parity and
    `deploy.sh` support first, then the boot-order fix, Windows, Debian
    family, templates, ownership guard), or is something more urgent?
14. Is there a physical Hyper-V host for this, or should development keep
    using the nested `hvhost1` VM on the libvirt host?

## Templates

15. How many dated versions of each template should be kept before old
    ones are deleted (§8 says "until nothing references them")?
16. Should template refreshes run on a schedule (e.g. monthly, or Patch
    Tuesday for Windows), or stay manual? (The web UI's job runner could
    schedule them.)
17. Windows updates during refresh: security, critical and rollups only
    (current default), or all available updates?
18. Which earlier test templates should be deleted: `rocky9-base-2026.10`,
    `win2022-base-2026.10` (promoted), and the `*-packer-2026.10` Server
    builds now superseded by `*-desktop-*` and `*-core-*`?
19. Windows Server edition: Standard (current) or Datacenter? The
    evaluation ISOs expire after 180 days: are licence keys or a KMS server
    available, or are short-lived evaluation VMs fine?

## Lab and operations

20. lab1 runs Server Core and was built from ISO. Keep it as it is, or
    rebuild it from Desktop Experience templates?
21. Should a block of addresses be reserved for test environments
    (offsets 100-199 hit a device on `.117` once), and recorded in
    environment.yml?
22. Is DHCP-mode addressing needed for anything yet (§17.4 defers it
    until there's a real need)?
23. Firefox can't install on Server 2019 Core: drop it from the example
    software manifest for `windows_server`, or leave it and accept the
    failure on 2019 Core hosts?
24. Should `Published_Docs/` end-user `.docx` guides start now (the
    user-docs backlog has about fifteen items)?

## Web interface: follow-up questions (added 2026-10-10)

25. ~~Roles: is admin / template editor / deployer / viewer the right set?~~ **Answered (2026-10-10):** Roles confirmed. Environments have owners; templates have owners and users; plus a file manager for ISOs, installers and other files (decisions 9, 10). (Claude_Docs/Planning_Web-Interface-Options.md)
26. ~~Should Authentik be part of the LABaPe container stack, or should LABaPe use an Authentik instance you already run?~~ **Answered (2026-10-10):** Use an existing Authentik instance by default; optionally deploy and configure one in the stack if none is available (decision 12). (Claude_Docs/Planning_Web-Interface-Options.md)
27. ~~Which sign-in sources are needed on day one: Authentik local accounts only, Active Directory (which domain), and/or an upstream SAML or OIDC provider (e.g. Entra ID)?~~ **Answered (2026-10-10):** Authentik local accounts (decision 13). (Claude_Docs/Planning_Web-Interface-Options.md)
28. ~~MFA policy: required for everyone, required for admins only, or optional?~~ **Answered (2026-10-10):** Optional (decision 14). (Claude_Docs/Planning_Web-Interface-Options.md)
29. ~~Kerberos (Windows integrated sign-on, so users on domain-joined Windows machines are signed in with their Windows login): needed at launch, or later?~~ **Answered (2026-10-10):** Later; an option on the Active Directory or Authentik provider, not needed at launch (decision 24). (Claude_Docs/Planning_Web-Interface-Options.md)
30. ~~Podman: rootful only (simplest, given the libvirt socket and `/dev/kvm`), or must rootless Podman work too?~~ **Answered (2026-10-10):** Rootless must work too; Quadlet units provided (decision 17). (Claude_Docs/Planning_Web-Interface-Options.md)
31. ~~Where should the container image be published: GitHub Container Registry (public or private), or a local registry?~~ **Answered (2026-10-10):** Local by default, external registry optional, all sources in one central repository configuration (decisions 18, 19). (Claude_Docs/Planning_Web-Interface-Options.md)
32. ~~HTTPS: should the stack include a reverse proxy (e.g. Caddy or Traefik) that terminates TLS, or will it sit behind an existing proxy?~~ **Answered (2026-10-10):** Self-signed to start, ACME supported, hostname asked at setup; whether a reverse proxy container terminates TLS is a design detail (decision 16). (Claude_Docs/Planning_Web-Interface-Options.md)
33. ~~Remote libvirt: how many KVM hosts should one LABaPe instance manage?~~ **Answered (2026-10-10):** Up to 4 KVM hosts, configurable (decision 20). (Claude_Docs/Planning_Web-Interface-Options.md) Spanning: **Answered (2026-10-10):** environments can span hosts, per host group in the template or best-fit placement (decision 26).
34. Remote libvirt: when the app doesn't run on a KVM host, where should
    Packer template builds run: on the app's container host (needs
    `/dev/kvm`), or on each KVM host?
35. Backups: who backs up PostgreSQL (app and Authentik data) and the
    template library, and how often?
36. How long should job logs and the audit log be kept?
37. ~~Should the app take over existing environments (lab1's OpenTofu state, the vault, `environment.yml`) on first start, or start empty and run alongside the current CLI setup until migrated?~~ **Answered (2026-10-10):** Optional import of existing CLI-built environments; most will be new (decision 15). (Claude_Docs/Planning_Web-Interface-Options.md)
38. Notifications on job completion or failure: email, Teams, Slack, a
    webhook, or none?

## Web interface: design questions (added 2026-10-10, second round)

39. ~~File manager: is there a maximum upload size (Windows ISOs are 5-8 GB), and should uploads be limited to admins, or can any developer add installers and other files?~~ **Answered (2026-10-10):** Admins and the file manager role upload/delete; chunked, resumable uploads with no fixed size limit (decision 10). (Claude_Docs/Planning_Web-Interface-Options.md)
40. ~~Template and environment sharing: should "users" of a template be picked individually, by Authentik group, or both?~~ **Answered (2026-10-10):** Both, on the same object (decision 9). (Claude_Docs/Planning_Web-Interface-Options.md)
41. ~~Where do new files land: the existing directories on the KVM host (`/data/OS_Images`, `software-store/`), or a new LABaPe-managed directory that the engine is pointed at?~~ **Answered (2026-10-10):** Default `/data/<type>`, each location configurable; VM storage is configurable per KVM host, with moving VMs between storage locations (decisions 10, 23). (Claude_Docs/Planning_Web-Interface-Options.md)
42. ~~Secrets manager: which would you use first, OpenBao (or HashiCorp Vault) or CyberArk Conjur, and is there an instance available to test against?~~ **Answered (2026-10-10):** Modular provider interface; OpenBao/Vault and Conjur first. No instance exists: the repo will include a script and doc to deploy throwaway OpenBao and Conjur containers for testing (decision 22). (Claude_Docs/Planning_Web-Interface-Options.md)
43. ~~VM storage migration: is an offline move (VM shut down during the copy) acceptable for the first version, with live migration later?~~ **Answered (2026-10-10):** Yes: offline move first, live move later (decision 23). (Claude_Docs/Planning_Web-Interface-Options.md)
44. ~~Break-glass: is the recommended design right (a command run in the container enables a one-time login for 5 minutes, single use, 1-hour session, audited; optionally host-only)?~~ **Answered (2026-10-10):** Yes: one-time login enabled by a command run in the container, 5 minutes, single use, 1-hour session, audited, optionally host-only (decision 25). (Claude_Docs/Planning_Web-Interface-Options.md)
45. ~~Direct LDAP/AD on day one, or after Authentik sign-in works (the design puts it in phase 10h)?~~ **Answered (2026-10-10):** No: direct LDAP/AD comes after Authentik sign-in works (phase 10h). (Claude_Docs/Planning_Web-Interface-Options.md)
