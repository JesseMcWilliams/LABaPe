# Directory Objects: OUs, Groups, Users, and Membership

Extends DESIGN.md §8 beyond "stand up the domain and join computers" to
populating it — organizational units, domain and local groups, domain
and local users, and group membership. One manifest file,
`directory-manifest.yml`, changed between runs the same way
`software-manifest.yml` is (DESIGN.md §11) — this is lab test data, not
stable reference data, so unlike the software catalog there's no
separate repo-committed "catalog" half to split out.

## 1. Two scopes, two toolchains, one real constraint

| | Domain objects | Local objects |
|---|---|---|
| Modules | `microsoft.ad.ou` / `.group` / `.user` / `.membership` | `ansible.windows.win_group`/`win_user`/`win_group_membership` (Windows); `ansible.builtin.group`/`user` (Linux) |
| Runs against | The `domain_controller`-role host (needs the ActiveDirectory PowerShell module, present by default post-promotion) | Whichever specific host(s) the entry targets |
| Only needs | The domain to exist (DESIGN.md §8) | That specific host to exist and be joined |

**Membership direction is constrained, not just a style choice**: a
domain user or domain group can be a member of either a domain group or
a local group on any domain-joined host — but a **local** account can
never be a member of a **domain** group; AD has no mechanism for that.
The manifest schema (§4) makes the invalid direction hard to even
express rather than relying on everyone remembering the rule.

## 2. Organizational units

```yaml
organizational_units:
  - name: Sales
    path: "DC=company,DC=com"            # parent DN
  - name: Workstations
    path: "OU=Sales,DC=company,DC=com"   # nested under Sales
```

Explicit OUs are only half the story — see §3 for the "create it if it
doesn't exist" behavior triggered by a group/user's `ou:` field.

## 3. Domain groups — OU and group type, as asked for

```yaml
domain_groups:
  - name: Sales-Users
    ou: "OU=Sales,DC=company,DC=com"
    scope: Global          # DomainLocal | Global | Universal — default Global
    category: Security     # Security | Distribution — default Security
  - name: Sales-RemoteAccess
    ou: "OU=Sales,DC=company,DC=com"
    scope: DomainLocal
    category: Security
```

`scope` and `category` are AD's actual two independent axes for "group
type" — modeled as two fields rather than one, since a manifest author
picking `DomainLocal` shouldn't also have to remember that it implies
nothing about Security vs. Distribution. Both default to the common
case (Global, Security) so a minimal entry just needs `name` + `ou`.

**OU auto-creation**: if `ou:` names a path that doesn't exist yet, it's
created — walking the DN from the top down, creating each missing level
with `microsoft.ad.ou` (idempotent, so a partially-existing path is
fine) — *before* the group itself is created. This is what makes
`organizational_units:` (§2) optional scaffolding rather than a strict
prerequisite: a group/user's `ou:` field is sufficient on its own.

## 4. Domain and local users

```yaml
domain_users:
  - name: jdoe
    ou: "OU=Sales,DC=company,DC=com"     # auto-created if missing, §3
    display_name: "Jane Doe"
    password_vault_key: sales_jdoe_password   # optional — see §6
    groups: [Sales-Users]                     # convenience inline membership

local_users:
  - name: local_tester
    hosts: [windows_workstation]          # a role name — see below
    password_vault_key: local_tester_password
    groups: ["Remote Desktop Users"]      # local group, same host
```

`hosts:` on a local object is a **role name** — one of
`domain_controller`, `windows_server`, `windows_workstation`,
`linux_server`, `linux_workstation` (DESIGN.md §9), the same values
`software-manifest.yml`'s `roles:` key is already keyed by — not a
literal hostname, and **not** a `host_groups` entry's own `name:`
field (e.g. a profile's `winws`/`linsrv` labels). This matters because
it's what `scripts/generate-inventory.py` actually creates Ansible
inventory groups from (`ALL_ROLE_GROUPS`) — a `host_groups` block's
`name:` is just a naming convenience for the hosts it produces
(`winsrv1`, `linsrv1`, …), it isn't itself an inventory group. Confirmed
the hard way during M5's implementation: an earlier draft of this doc
and the example manifest both used host-group names here, and every
local-object task silently no-op'd (an empty `for_host_group` match,
docs/directory-objects.md's own local-object implementation) until
corrected to real role names.

## 5. Local groups

```yaml
local_groups:
  - name: Remote Desktop Users    # built-in group — ensuring membership, not creating it
    hosts: [windows_workstation]
  - name: DeploymentTesters       # a genuinely new local group
    hosts: [linux_workstation]
```

## 6. Passwords

`password_vault_key` names a key in `secrets.vault.yml` (docs/credentials.md
§1), consistent with how every other credential in this design is
handled — never a plaintext value in the manifest. Since this is lab
test data and often many disposable users are wanted at once, an entry
can omit `password_vault_key` entirely and fall back to a single
`default_user_password` vault key — one password for every test user
that doesn't need its own, with per-user overrides available for the
cases that do. The domain's password policy still applies to whatever
value that resolves to.

## 7. Membership — the explicit list, and the direction constraint

Inline `groups:` on a user (§4) covers the common case. The explicit
`memberships:` list handles group nesting and the cross-scope case
(a domain principal added to a local group):

```yaml
memberships:
  - member: Sales-Users              # domain group nested inside another domain group
    group: Sales-RemoteAccess
    group_scope: domain

  - member: "COMPANY\\Sales-Users"   # domain group added to a LOCAL group — the common
    group: Administrators            # "domain admins in the local admins group" pattern
    group_scope: local
    hosts: [windows_workstation]
```

`group_scope: local` entries need `hosts:` (which role's hosts to apply
the membership on); `group_scope: domain` entries don't, since there's
only one domain. A `member:` with no `DOMAIN\` prefix is resolved in the
same scope as `group:` — a bare name only needs domain-qualifying when
crossing from domain scope into a local group.

**Validation**: a `group_scope: domain` entry whose `member:` resolves
to a local (not domain) account is rejected before anything runs — the
direction §1 describes isn't just documented, it's checked.

## 8. Processing order

Within a single apply, order matters and isn't left implicit:

1. **OUs** — explicit `organizational_units:` entries, sorted shallowest
   path first, plus any OU implicitly referenced by a group/user's `ou:`
   that isn't already covered (§3).
2. **Domain groups**, then **domain users** — a user's inline `groups:`
   only resolves correctly if the group already exists.
3. **Local groups**, then **local users** — same reasoning, per host.
4. **Memberships** — last, since a nested-group or cross-scope
   membership needs every group on both sides of it to already exist.

## 9. Where this runs, and the two kinds of "different steps"

**Structural staging** (domain vs. local) already falls out of the
existing pipeline (DESIGN.md §4) rather than needing a new phase system:
domain objects (§2–§4, §7's domain-scope entries) run as part of a new
`domain_directory` role, right after `domain_controller`'s promotion —
they only need the domain to exist. Local objects (§5, §7's local-scope
entries) run per-host, folded into `windows_common`/`linux_common`'s
existing per-host pass — they need that specific host to exist and be
joined first. Two stages you already have, not a new concept.

**Operational staging** — adding a group or user to a lab that's
*already up*, without re-running domain join or software install: every
directory-object task carries a `directory_objects` tag (and the finer
`ou`/`domain_group`/`domain_user`/`local_group`/`local_user`/`membership`
tags for even narrower re-runs), so
`ansible-playbook site.yml --tags directory_objects` applies just this
manifest's changes against a running environment. Since every module
here is idempotent, re-running it is safe — existing OUs/groups/users
aren't recreated, only what's new or changed actually does anything.

## 10. Repository layout additions

```
ansible/
  directory-manifest.example.yml
  roles/
    domain_directory/     # OUs, domain groups/users, domain-scope memberships — runs post-promotion
    local_accounts/        # local groups/users, local-scope memberships — folded into per-host config,
                            # or added as tasks inside windows_common/linux_common directly
```
