# Environment Templates & Web Interface

Extends Claude_Docs/Design_System-Overview.md §20 — a future (not yet implemented) web interface for
creating, duplicating, editing, and deploying **environment templates**,
built out of composable, assemblable pieces, plus browsing an OS/
software/feature "repository" instead of hand-editing YAML/HCL. This is
explicitly aspirational, scoped further out than M1-M8 (Claude_Docs/Design_System-Overview.md §18) —
recorded now so later work has a starting design rather than a blank
page, not because it's next up.

## 1. What a template actually is

Today, describing an environment means editing three separate,
hand-written files: a `.tfvars` profile (`host_groups`, Claude_Docs/Design_System-Overview.md §9),
`software-manifest.yml` (§11), and `directory-manifest.yml`
(Claude_Docs/Design_Directory-Objects.md). A **template** is those three merged into
one object: for every host (group), how many, what OS, what roles
(including the new `certificate_authority` role, docs/
certificate-authority.md), what software, and what users/groups it has
or is a member of. "Build a test environment from a template and hit
go" means: pick a template, optionally tweak it, deploy — the web UI's
job is producing exactly the inputs `scripts/deploy.sh` (or its
successor) already consumes, not replacing the provisioning pipeline
itself (§5).

```yaml
# Illustrative shape, not a finalized schema
name: "small-ad-lab"
host_groups:
  - name: dc
    count: 1
    os: windows_server_2022
    roles: [domain_controller, certificate_authority]
  - name: linsrv
    count: 2
    os: rocky9
    roles: [linux_server, certificate_authority]
software:
  linsrv: [...]           # same shape as today's software-manifest.yml
directory:
  domain_users: [...]     # same shape as today's directory-manifest.yml
  memberships: [...]
```

## 2. Composability: assemblies and sub-assemblies

A template can **include** other templates as components — an
"assembly with sub-assemblies," per the original ask. Resolved design
decisions (all chosen over the alternative during design discussion):

- **No separate sub-assembly type.** Any template — a full 10-host lab
  or a 2-host "web farm" — can be deployed standalone *or* included
  inside a larger one. One concept, used two ways, rather than two
  concepts to keep in sync.
- **Auto-prefixed host-group names on inclusion.** Including a template
  under a given inclusion name (e.g. `prod-web`) automatically prefixes
  its host-group names (`prod-web-websrv1`, `prod-web-websrv2`) so the
  same template can be included more than once in one parent, or
  alongside another template that happens to use the same host-group
  names, without manual renaming or collisions.
- **Parent can override included fields.** A sub-assembly's host count,
  OS, or roles can be overridden by whatever includes it, without
  forking the sub-assembly into a new template. The sub-assembly's own
  values are the defaults; the parent's `overrides:` (shape TBD) win
  where present.

```yaml
# Illustrative — a parent template including a sub-assembly twice
name: "two-site-lab"
includes:
  - template: web-farm
    as: site-a
  - template: web-farm
    as: site-b
    overrides:
      host_groups.websrv.count: 4   # site-b gets 4 web servers, site-a keeps web-farm's default
```

**Not yet resolved** (flag for whoever picks this up):

- **Versioning/pinning**: if `web-farm` is edited after `two-site-lab`
  already includes it, does `two-site-lab` pick up the change on its
  next deploy (live reference), or does it need to be re-pinned
  explicitly (snapshot)? Claude_Docs/Design_System-Overview.md §2's "idempotent, repeatable builds"
  goal leans toward snapshot/pinning being the safer default, but this
  needs a real decision before implementation, not an assumption.
- **Role-conflict detection**: if two included templates both carry a
  singleton role (`domain_controller`, `certificate_authority` — §9,
  Claude_Docs/Planning_Certificate-Authority.md §3), should composing them be a hard
  error at "compile" time? Almost certainly yes, but not designed.
- **Cycle detection**: a template including itself (directly or via a
  chain) needs to be caught, not just left to whatever OpenTofu/Ansible
  would do with a malformed result.

## 3. The web interface itself

Three things the UI needs to do, roughly in order of how novel they are
relative to what already exists:

1. **Browse/manage an OS, software, and feature "repository."** Largely
   a friendlier view onto data that's already designed to be
   YAML/catalog-driven — the OS catalog (Claude_Docs/Design_Base-Images.md), the
   software manifest's package catalog (§11), and (new) Windows Server
   Roles/Features as a similar toggle-able catalog. Not a new data
   model so much as a UI on top of existing ones, plus the new Features
   catalog.
2. **Create/duplicate/edit templates**, including composing sub-
   assemblies (§2) — the template-authoring surface. This *is* new data
   (§1's merged template object) that doesn't fully exist as a concept
   yet, even though every piece it's made of already does.
3. **Deploy ("hit go") and show progress.** The one piece that can't be
   a thin synchronous wrapper: a real deploy is a `tofu apply` +
   multi-role `ansible-playbook` run that took 15-40+ minutes in this
   project's own Hyper-V testing, and can fail partway needing
   visibility or intervention (this project hit provider crashes, stuck
   installs, and state that needed manual cleanup more than once — see
   Claude_Docs/Testing_Troubleshooting-Log.md). The UI needs an async job model — a
   queue, live log streaming, retry/cancel — not a request/response
   button. This is realistically the biggest single piece of new
   engineering in this whole feature, bigger than the UI or the
   template model.
4. **Live credentials lookup for a running environment**
   (Claude_Docs/Reference_Credentials.md §8). Today, tester access credentials for a
   deployed environment come from a plain generated handout file
   (`ansible/inventory/credentials.generated`) — deliberately simple,
   built before this web interface existed. Once this UI exists, add a
   credentials view here as a *second, selectable* way to get the same
   information (a tester picks an environment, sees its accounts) —
   explicitly not a replacement for the generated-file option. Some
   teams will still want a plain handout instead of routing every
   access request through a web page; both should keep working, chosen
   per-environment or per-deployment rather than one deprecating the
   other.

## 4. Open questions (not yet resolved)

- ~~**Storage**~~ — **resolved (2026-10-10):** git-backed YAML in a
  repository the app keeps in its own data volume (works fully offline),
  optionally synced with an external remote when configured and
  reachable, with bundle export/import for disconnected sites. Templates
  have owners (edit/share/delete) and users (see/deploy).
  Claude_Docs/Planning_Web-Interface-Options.md decisions 9 and 11.
- ~~**Auth / multi-tenancy**~~ — **resolved (2026-10-10):** multiple
  users with role-based permissions; sign-in through Authentik (local,
  SAML, OIDC, LDAP/AD, MFA, optional Kerberos), API tokens issued by the
  app, roles mapped from groups. Claude_Docs/Planning_Web-Interface-Options.md.
- ~~**Relationship to the CLI**~~ — **resolved (2026-10-10):** the web UI
  is the primary interface; the job runner shells out to the *existing*
  scripts, which stay the engine and stay usable from a shell, rather
  than reimplementing their logic. Delivered as a container stack
  (Docker primary, Podman supported) on the KVM host, with remote
  libvirt also supported. Claude_Docs/Planning_Web-Interface-Options.md.
