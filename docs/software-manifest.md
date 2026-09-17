# Software Manifest: Package Mapping Across Chocolatey/apt/dnf/zypper

The original requirement (DESIGN.md's premise): the software list "isn't
always the same" between runs. This splits that into two files with
different lifecycles, rather than one file trying to be both a stable
reference and a per-run choice:

- **`ansible/package_catalog.yml`** — repo-committed, stable. Maps one
  generic name (`vscode`) to whatever that package is actually called
  on each package manager (`chocolatey`, `apt`, `dnf`, `zypper`), plus
  anything extra needed to install it (a repo to add first, or a direct
  installer URL for something with no package-manager entry at all).
- **`software-manifest.yml`** — the thing that actually changes between
  runs. Per role, which catalog entries (or one-off inline packages not
  worth cataloging) get installed.

## 1. The catalog

```yaml
# ansible/package_catalog.yml
packages:
  firefox:
    chocolatey: firefox
    apt: firefox
    dnf: firefox
    zypper: MozillaFirefox        # deliberately not the same string everywhere

  vscode:
    chocolatey: vscode
    apt:
      package: code
      repo: microsoft             # needs Microsoft's repo added first, §3
    dnf:
      package: code
      repo: microsoft
    # no zypper entry — silently skipped on openSUSE/SLES hosts, §2

  sevenzip:
    chocolatey: 7zip
    apt: p7zip-full
    dnf: p7zip
    zypper: 7zip

  notepadplusplus:
    chocolatey: notepadplusplus
    # windows-only tool — no linux fields at all, same silent-skip rule

  internal_agent:                 # no package manager has this at all
    windows:
      type: msi
      url: https://internal.example.com/agent.msi
      product_id: "{B1234567-...}"   # for win_package's idempotency check
    linux:
      type: deb_url
      url: https://internal.example.com/agent.deb
```

Ships with a modest starter set of common tools (browsers, 7-Zip,
editors) as both working examples and genuinely useful defaults — easy
to extend, never a blocker to add more.

## 2. Per-platform fields are optional, and that's not an error

Not every package exists on every platform (`notepadplusplus` is
Windows-only; `vscode` has no `zypper` entry above because it isn't
being packaged for openSUSE yet in this catalog). The resolution logic
(§4) looks up the field for a host's platform and **silently skips**
that package on hosts where the field is absent, rather than failing
the whole run — the OS matrix (DESIGN.md §5) is wide enough that "not
available here" is the normal case, not an exception.

## 3. Packages needing a repo first

Some software (VS Code, Docker, Google Chrome, …) isn't in a distro's
default repos — a repo/GPG key has to be added before `apt`/`dnf` can
even see the package. A catalog entry's `apt`/`dnf` field can be an
object with `package` + `repo` instead of a bare string; `repo` names a
small, reusable repo-setup task
(`ansible/roles/linux_common/tasks/repos/microsoft.yml`, etc.) that adds
the key and repo file. `linux_common` collects the **unique set** of
repos actually referenced by a host's resolved package list and runs
each repo-setup task once, before the package-install loop — so a host
that doesn't need Microsoft's repo never gets it added.

## 4. The manifest: which catalog entries apply to which role

```yaml
# software-manifest.yml
roles:
  windows_server:
    - sevenzip
  windows_workstation:
    - firefox
    - vscode
    - notepadplusplus
  linux_server:
    - sevenzip
  linux_workstation:
    - firefox
    - vscode
    - name: internal_agent        # inline override example, not required here —
      windows: { type: msi, url: "https://.../agent-v2.msi", product_id: "..." }
                                   # this would shadow the catalog entry for this run only
```

Most entries are just a catalog key (a string). An entry can instead be
an inline object — same shape as a catalog entry, plus a `name` — for a
one-off package not worth committing to the shared catalog, or to
override a catalog entry for a single run without editing the shared
file (e.g. testing a newer build of `internal_agent` before promoting it
into the catalog for everyone).

## 5. Resolving a host's package list — the part that needs to be explicit

A host can carry more than one role (DESIGN.md §9 — e.g. a domain
controller that's also a general-purpose Windows server), and Ansible's
default variable behavior does **not** merge/union list-type variables
across the multiple inventory groups a host belongs to — the
higher-precedence group's list simply wins outright. Relying on that
default would silently drop software for any multi-role host, so
resolution is explicit, done once per host at the top of the play:

```yaml
- name: Resolve this host's package list from every role it carries
  set_fact:
    resolved_packages: >-
      {{ group_names
         | map('extract', software_manifest_roles)
         | select('defined')
         | sum(start=[])
         | unique }}
```

(`software_manifest_roles` is `software-manifest.yml`'s `roles:` map,
loaded via `-e @software-manifest.yml`.) This gives every host the
**union** of its roles' package lists — the domain-controller-plus-
windows-server example above gets both `domain_controller`'s list (if
it has one) and `windows_server`'s, not just whichever one Ansible
happened to load last.

## 6. `windows_common` and `linux_common`

**`windows_common`**, for each entry in `resolved_packages`:
- Look up the entry's `windows`/`chocolatey` field (inline entries use
  the same shape as catalog entries, so one lookup path handles both).
- `type: msi`/`exe` → `ansible.windows.win_package` (url + product_id
  for idempotency).
- Otherwise → `chocolatey.chocolatey.win_chocolatey`, with `version:`
  passed through if the entry pins one (§7).

**`linux_common`**, for each entry:
- Determine the package manager from `ansible_facts['pkg_mgr']`
  (`apt`/`dnf`/`zypper`) rather than hardcoding per-distro logic —
  matches whichever OS family (DESIGN.md §5) the host actually is.
- Run any repo-setup tasks the resolved list needs first (§3), each
  exactly once.
- `type: deb_url`/`rpm_url` → the native module's URL-install form
  (`ansible.builtin.apt` with `deb:`, `ansible.builtin.dnf`/`zypper`
  pointed at a URL/local path directly).
- Otherwise → the native module (`apt`/`dnf`/`community.general.zypper`)
  with the platform-specific package name, `version:` passed through if
  pinned.
- If the platform field is absent for this host's package manager, skip
  it (§2) — logged, not failed.

## 7. Versioning

Every catalog or inline entry can optionally pin a version:

```yaml
- name: internal_agent
  windows: { type: msi, url: "...", product_id: "...", version: "2.3.1" }
```

Omitted means "whatever's current" — the practical default for a
disposable test/lab environment. Pinning exists for the case that
matters here specifically: reproducing a bug against a known-bad or
known-good version rather than whatever happens to be latest that day.
