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

## Networks and IP addresses (added 2026-10-10)

Background: Claude_Docs/Planning_Web-Interface-Design.md §20. A new VLAN, 172.21.48.0/22 (gateway 172.21.48.1, DHCP enabled), is cabled to the KVM host's `enp14s0f1`.

46. ~~Addressing on the new VLAN: should VMs take DHCP leases (LABaPe then finds each VM's address by its MAC after boot), or static addresses from a range the DHCP server never hands out (which range)? Domain controllers need a static address either way. Recommended: DHCP by default, plus a small static pool excluded from the DHCP scope, for domain controllers.~~ **Answered (2026-10-10):** Both. DHCP scope 172.21.48.16-172.21.49.254; LABaPe's static pool 172.21.50.1-172.21.51.254 (both in 172.21.48.0/22). Domain controllers use the static pool.
47. ~~The VMs need a bridge (`br1`) on `enp14s0f1`. My sudo doesn't cover NetworkManager, so do you want to run the commands in §20.6 yourself, or add `/usr/bin/nmcli` to AutomationUser's sudoers?~~ **Answered (2026-10-10):** Done: `br1` on `enp14s0f1` (takes a DHCP lease; never the default route), and bridged traffic is no longer filtered by iptables.
48. ~~Who may use which network: can anyone allowed to deploy use any enabled network attached to the host, or should each network be limited to chosen roles or groups?~~ **Answered (2026-10-10):** Yes: networks can be limited to chosen roles and/or groups.
49. ~~Should 172.21.20.0/22, where lab1 lives, stay a LABaPe network? If so, which range is LABaPe's static pool (e.g. .100-.199)?~~ **Answered (2026-10-10):** No: VMs migrate off 172.21.20.0/22; the web interface keeps listening on it.
50. ~~CLI parity: should `environment.yml` (the CLI) get the same network catalog and per-host-group network choice, or does the CLI keep its single network while the catalog lives in the web UI?~~ **Answered (2026-10-10):** Yes: `environment.yml` gets the same network catalog and per-host-group network choice.

## Web UI feedback round (added and answered 2026-10-11)

51. ~~Services added on top of a template: a curated service catalog (each service maps to Windows features or Linux packages/units plus an Ansible role), or raw Ansible role names?~~ **Answered (2026-10-11):** The curated catalog, with a separate page for creating services. Users request a service; admins create and publish it.
52. ~~Applications: managed in the database with YAML import/export for the CLI, or a git-backed YAML file?~~ **Answered (2026-10-11):** Like 51: users choose from a curated list and can request additions. Admins can import a pre-packaged YAML or create an entry from scratch. Services and applications can both be exported as packages.
53. ~~Application packages: may they include services as well as applications, with one level of nesting?~~ **Answered (2026-10-11):** Yes; packages can group packages, one level deep.
54. ~~Reload rights: a silent re-sign-in with Authentik (prompt=none), or a group lookup through the Authentik API?~~ **Answered (2026-10-11):** The silent re-sign-in.
55. ~~May LABaPe use a read-only Authentik API token (in the secrets store) for a group search?~~ **Answered (2026-10-11):** Yes.
56. ~~Network VLAN field: a label only, or tagged trunk support?~~ **Answered (2026-10-11):** A label only.
57. ~~KVM host load thresholds: warn above 4 vCPUs per core, above 90% RAM committed and below 15% free disk; block only above 100% RAM committed?~~ **Answered (2026-10-11):** Agreed.
58. ~~Order: A UI fixes, B host resources, C Files tab (phase 10b), D services/applications/packages, E admin settings, F Authentik group lookup?~~ **Answered (2026-10-11):** Agreed.
59. ~~Error log: an admin-only Errors page, kept 90 days?~~ **Answered (2026-10-11):** Yes, with the retention configurable.
60. ~~Host names given per host: unique across the KVM host, or within a pattern?~~ **Answered (2026-10-11):** Unique across the KVM host.
61. ~~Template metadata (OS, edition, minimums): infer the existing templates' OS from their names, or confirm each one?~~ **Answered (2026-10-11):** Infer from the names, and add a description and a notes field. Notes record quirks and after-tasks.

## Dashboard, list pages, installer, CA and downloads (added and answered 2026-10-11)

Design: `Claude_Docs/Planning_Web-Interface-Design.md` §22.

62. ~~Per-VM free disk space is only visible from inside the guest. May every image (templates and ISO installs) get **qemu-guest-agent**, so the dashboard shows real free space and a "responding" state? Without it, the dashboard can show only the disk file's allocated size against its virtual size.~~ **Answered (2026-10-11):** Yes, on every host.
63. ~~Online/Offline per VM: use the libvirt state (running is online; shut off, paused or crashed is offline), plus "not responding" when running but the guest agent doesn't answer?~~ **Answered (2026-10-11):** Yes.
64. ~~Version and updates: bake a release tag (`vX.Y.Z`) into the image at build, and have the worker check GitHub releases for `JesseMcWilliams/LABaPe` once a day. The repo is public, so no token is needed. A setting turns the check off for air-gapped sites. There are no tags yet, so this round would start them (proposed `v0.2.0`) with a root `CHANGELOG.md`.~~ **Answered (2026-10-11):** Yes.
65. ~~Secrets back ends on the dashboard: show what exists today (the built-in store and the engine's Ansible vault), with OpenBao/Vault and Conjur appearing when phase 10d builds them. Or should 10d join this round?~~ **Answered (2026-10-11):** Yes: show what exists; 10d later.
66. ~~Dashboard history: current values plus a 24-hour sparkline from one-minute samples, kept 24 hours?~~ **Answered (2026-10-11):** Yes.
67. ~~List pages: non-admins see **Hosts** (those they can deploy to) and **Networks** (those they may use) read-only, with no Add button or edit page? The same list-and-detail pattern also goes to catalog items and templates.~~ **Answered (2026-10-11):** Yes.
68. ~~Branding: only the app name for now (top bar, browser title, emails, syslog default app name), or also an uploaded logo or icon?~~ **Answered (2026-10-11):** Name only now; a logo later.
69. ~~Installer profiles: `--profile web` (web stack only) needs the web stack to drive a **remote KVM host over SSH**, which is phase 10e and not built yet. Build 10e in this round, starting with one or more remote hosts reached by `qemu+ssh` with an installer-generated SSH key? `--profile kvm` works without it.~~ **Answered (2026-10-11):** Yes.
70. ~~Platform CA (`Claude_Docs/Planning_Certificate-Authority.md` §5):~~ **Answered (2026-10-11):** Option A. Parts (b) and (c) take the recommendations: sibling environment CAs under the platform root, and the proposed lifetimes.
    - (a) option **A** (platform root, environment CAs subordinate), **B** (platform CA for the web UI and KVM hosts only), or **C** (none)?
    - (b) under A, may an environment with both AD CS and step-ca have both as siblings under the platform root, replacing §2's Linux-under-Windows chain?
    - (c) lifetimes: 10-year root, 5-year platform intermediate, 1-year environment intermediates, 30-day leaf certificates with renewal?
71. ~~Downloads from a URL: allow any http(s) address, with a proxy setting in Settings and link-local and metadata addresses (169.254.0.0/16) refused? May admins and file managers both start downloads?~~ **Answered (2026-10-11):** HTTPS only, with a global setting to allow HTTP. Admins and file managers can both start downloads.
72. ~~Hash verification: SHA-256 (default), SHA-1, SHA-512 and MD5. The hash is typed or pasted as a `SHA256SUMS` line, for both uploads and URL downloads, and a mismatch deletes the file. Is a checksum-file URL (fetch `SHA256SUMS` from the vendor) also wanted now?~~ **Answered (2026-10-11):** LABaPe's own hashes are always SHA-256. Some vendors don't publish a SHA-256, so verification of downloads (and uploads) also accepts SHA-1, SHA-512 and MD5.
73. ~~Order: G dashboard (with per-VM stats and the version check), H list pages and branding, I URL downloads and hashes, J installer profiles and remote KVM hosts, K platform CA?~~ **Answered (2026-10-11):** Yes, the suggested order.

## Update flow and secret rotation (added and answered 2026-10-11)

Design: `Claude_Docs/Planning_Web-Interface-Design.md` §23.2.

76. ~~"Branch" in the update box: GitHub **pre-releases** (a preview channel; tags such as `v0.4.0-rc.1`, each with release notes), or **git branches** (such as `main`), which have no release notes, only a commit list?~~ **Answered (2026-10-11):** Pre-releases.
77. ~~What **Continue** does: really update the running stack, through a host-side update agent the installer sets up (database backup, check out the tag, rebuild, restart, health check, automatic return to the previous version on failure), or only show the commands to run?~~ **Answered (2026-10-11):** Yes, through the host-side agent. The new version could come up yet be partly broken, so the previous image is kept on standby for an instant rollback, and data stays in the persistent volumes. A **maintenance mode** stops writes during maintenance and upgrades while reads and status keep working (§23.3).
78. ~~Downgrade: list the earlier releases as well, behind a warning, and always take a database backup first. The way back is restoring that backup with the newer version. Allow it whenever, or only to releases with the same database layout?~~ **Answered (2026-10-11):** Only the previous version from the app. Going back further is a documented manual method.
79. ~~Releases: cut **v0.3.0** now (round 3 plus the tooltips and confirmations), so the update flow has a real release to update to and downgrade from?~~ **Answered (2026-10-11):** Not yet: the release signing key comes first.
80. ~~Should the update agent refuse a release whose tag isn't signed by a key in a pinned allowed-signers file?~~ **Answered (2026-10-11):** Yes. The signing key is being set up; no release until then.
81. ~~Secret rotation: one framework (make, apply, verify, commit, undo; old and new both valid until verified), scheduled per secret, with a Settings > Secrets page, audit and alerts; privileged secrets every 24 hours, read-only ones every 7 days?~~ **Answered (2026-10-11):** Agreed, with three rules:
    - A secret whose account can't rotate itself may name an **elevated account** that does it.
    - A rotation that needs a service **restart** runs every **7 days, inside a set maintenance window**, with a note on the secret saying why.
    - Exception: when an **external secrets provider** rotates the secret itself, LABaPe leaves it alone.
82. ~~Does "only the previous version" also cover the automatic rollback after failed health checks?~~ **Answered (2026-10-11):** Yes.
83. ~~Lab VM passwords: rotate on running environments, or only for new ones?~~ **Answered (2026-10-11):** Domain admin and local admin rotate every 7 days by default; the interval is set per environment, and rotation can be turned off per environment. The DSRM password never rotates.
84. ~~Build rotation on the built-in secret store, ready for the OpenBao/Vault provider (phase 10d) later?~~ **Answered (2026-10-11):** Yes. Several secret stores may be in use at once, so the store is chosen per secret.
