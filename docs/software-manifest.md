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

  internal_agent:                 # no package manager has this at all,
                                   # and it's fetched from a URL — contrast
                                   # with vendor_tool below, §8
    windows:
      type: msi
      source: url
      url: https://internal.example.com/agent.msi
      product_id: "{B1234567-...}"   # for win_package's idempotency check
    linux:
      type: deb
      source: url
      url: https://internal.example.com/agent.deb

  vendor_tool:                    # no repo, no reachable URL either — §8
    windows:
      type: msi
      source: local
      path: vendor_tool/vendor-tool-4.1.msi
      product_id: "{C7654321-...}"
      # no `arguments:` — msi gets a sensible silent default, §9
    linux:
      type: rpm
      source: local
      path: vendor_tool/vendor-tool-4.1.rpm

  legacy_exe_tool:                 # EXE bootstrapper needing its own
                                    # unattended answer file — §9
    windows:
      type: exe
      source: local
      path: legacy_exe_tool/setup.exe
      answer_file: legacy_exe_tool/setup.iss   # InstallShield response file
      arguments: '/s /f1"{{ answer_file_remote_path }}"'
      creates_path: 'C:\Program Files\Legacy Tool\tool.exe'   # idempotency check, §9
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
      windows: { type: msi, source: url, url: "https://.../agent-v2.msi", product_id: "..." }
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
- `type: msi`/`exe`, `source: url` → `ansible.windows.win_package` with
  `path:` set to the URL directly — `win_package` fetches it itself, no
  copy step, so this needs the target host to have outbound access to
  that URL (same egress assumption the software-manifest package
  installs already depend on).
- `type: msi`/`exe`, `source: local` → copy-then-install, §8.
- Either way, `arguments:` (if set) is passed straight through to
  `win_package`'s own `arguments:` parameter — including a rendered
  `answer_file_remote_path` if the entry copied a response file, §9.
- Otherwise → `chocolatey.chocolatey.win_chocolatey`, with `version:`
  passed through if the entry pins one (§7).

**`linux_common`**, for each entry:
- Determine the package manager from `ansible_facts['pkg_mgr']`
  (`apt`/`dnf`/`zypper`) rather than hardcoding per-distro logic —
  matches whichever OS family (DESIGN.md §5) the host actually is.
- Run any repo-setup tasks the resolved list needs first (§3), each
  exactly once.
- `type: deb`/`rpm`, `source: url` → the native module's URL-install form
  (`ansible.builtin.apt` with `deb: <url>`; `dnf`/`community.general.zypper`
  pointed at the URL directly) — same no-copy, host-fetches-it-itself
  behavior as the Windows URL case above.
- `type: deb`/`rpm`, `source: local` → copy-then-install, §8.
- Either way, `.deb` installs run with `DEBIAN_FRONTEND=noninteractive`
  set and any `debconf_selections:` (§9) applied first, so a package
  that would otherwise prompt during configuration doesn't hang the run.
- Otherwise → the native module (`apt`/`dnf`/`community.general.zypper`)
  with the platform-specific package name, `version:` passed through if
  pinned.
- If the platform field is absent for this host's package manager, skip
  it (§2) — logged, not failed.

## 7. Versioning

Every catalog or inline entry can optionally pin a version:

```yaml
- name: internal_agent
  windows: { type: msi, source: url, url: "...", product_id: "...", version: "2.3.1" }
```

Omitted means "whatever's current" — the practical default for a
disposable test/lab environment. Pinning exists for the case that
matters here specifically: reproducing a bug against a known-bad or
known-good version rather than whatever happens to be latest that day.

## 8. Software with no repo and no reachable URL

Some software isn't in any package manager *and* isn't hosted anywhere
a lab VM can reach it — a vendor's installer that only exists as a file
on your own machine or file share. `source: local` (§1's `vendor_tool`
example) covers this with an explicit two-step install instead of the
one-step URL case, since nothing except the control machine actually
has the file:

1. **Copy** — `ansible.windows.win_copy` (Windows) /
   `ansible.builtin.copy` (Linux) pushes the file from the control
   machine to a temp path on the target host (registered as
   `installer_remote_path` — and `answer_file_remote_path` too, if the
   entry has an `answer_file`, §9), over the same WinRM/SSH connection
   Ansible already has open. This is the fundamental difference from the
   URL case: the *target host* fetches a URL itself, but a local file
   only exists on the control machine, so the control machine has to
   push it there first.
2. **Install** — `win_package`/`apt`/`dnf`/`zypper` then run against
   `installer_remote_path`, exactly like the URL case just substitutes a
   local path for a URL.

### Where local files live

`path:` in a `source: local` entry is relative to a **software store**
— a directory the control machine can read, kept **outside git**
(installer binaries don't belong in a git repo, especially without
Git LFS, which this design doesn't require). Location is configurable,
defaulting to `./software-store/` next to the rest of the repo:

```yaml
# environment.yml
software_store_path: /srv/labape/software-store   # optional override; defaults to ./software-store
```

```
software-store/                  # .gitignore'd
  internal_agent/
    agent.msi
    agent.deb
  vendor_tool/
    vendor-tool-4.1.msi
    vendor-tool-4.1.rpm
```

Since Ansible copies the file directly from the control machine over
its existing connection, the store only has to exist in **one place**
— unlike Packer templates, which are backend/hypervisor-specific, the
software store doesn't need to be replicated anywhere per backend or
per environment.

### Versioning local files

No separate mechanism from §7 — a version bump for a locally-sourced
package just means a new file in the store and an updated `path:`
(e.g. `vendor_tool/vendor-tool-4.2.msi`), optionally alongside the
`version:` field for `win_package`'s/`apt`'s own idempotency checks.
Keeping the old file around lets a manifest still reference an older
version deliberately, the same reasoning §7 already covers for
URL-sourced packages.

## 9. Instructing a silent/unattended install

`chocolatey`/`apt`/`dnf`/`zypper` entries don't need anything here —
package-manager packages already encapsulate correct unattended
behavior; that's what a package manager is for. This section only
matters for the **custom installer path** (`type: msi/exe/deb/rpm`, any
`source`), since that path bypasses a package manager's own conventions
and talks to `win_package`/`dpkg`/`rpm` directly. Nothing about it is
specific to `source: local` — a URL-sourced EXE (§1's `internal_agent`)
needs exactly the same silent-install instructions as a locally-sourced
one.

### `arguments`

A string or list, passed straight through to `win_package`'s own
`arguments:` parameter:

- **MSI**: has an actual standard here — unlike everything else in this
  section, a sensible default (`/qn /norestart`) applies automatically
  when `arguments:` is omitted. Still overridable for an MSI that needs
  custom properties, e.g. `arguments: "/qn INSTALLDIR=D:\\Tools ACCEPTEULA=1"`.
- **EXE**: no standard exists — every vendor's bootstrapper picks its
  own silent flag (`/S`, `/SILENT`, `/VERYSILENT`, `/quiet`, or
  something entirely proprietary). There's no default to fall back to;
  `arguments:` has to be set explicitly per EXE entry, found from that
  installer's own documentation (or `setup.exe /?`/`/help` if it has
  one). A related field that also needs setting explicitly for EXE
  (unlike MSI, which can usually determine its own idempotency from the
  file itself): `product_id` if the installer registers one, or
  `creates_path`/`creates_service` pointing at something the install
  leaves behind (`legacy_exe_tool`'s example above) — without one of
  these, `win_package` has no reliable way to tell "already installed"
  from "not," and a re-run could reinstall every time instead of
  no-op'ing.
- **deb/rpm**: `apt`/`dnf`/`rpm` are already non-interactive by
  default for straightforward packages — `arguments:` is rarely needed
  here. `debconf_selections` (below) covers the actual common failure
  mode on Linux (a `.deb`'s postinst script prompting for
  configuration) rather than an install-time flag.

### `answer_file` — for an installer with its own response-file format

Distinct from the OS-level answer files in `docs/base-images.md` (those
answer autounattend.xml/kickstart/cloud-init questions the *OS
installer* asks) — some Windows EXE installers (InstallShield being the
classic case) have their **own** unattended mechanism: a response file
(`.iss` for InstallShield) recorded once interactively
(`setup.exe /r /f1"template.iss"`) and replayed silently on every future
install (`setup.exe /s /f1"template.iss"`).

`answer_file:` names a file in the software store (§8) — copied
alongside the installer itself in the same `win_copy` step, to
`answer_file_remote_path` — and referenced from `arguments:` via that
variable, as `legacy_exe_tool` in §1 shows. Only relevant for the small
set of installers that actually use this pattern; most EXEs just need a
`/silent`-style flag in `arguments:` with no separate file at all.

### `debconf_selections` — pre-answering a `.deb`'s configuration prompts

Some `.deb` packages ask interactive questions during
`postinst` (via `debconf`) — a license prompt, a config choice — which
would otherwise hang an unattended install. Setting
`DEBIAN_FRONTEND=noninteractive` for the install task (done
automatically by `linux_common` for every `.deb` install, §6) silences
the prompt but doesn't answer it, which can leave a package
half-configured. `debconf_selections` pre-seeds the actual answers
before installing:

```yaml
some_deb_tool:
  linux:
    type: deb
    source: local
    path: some_deb_tool/tool.deb
    debconf_selections:
      - "some-deb-tool some-deb-tool/accept-license boolean true"
```

Applied via `ansible.builtin.debconf` (one task per line) immediately
before the install task. Only needed for packages that actually prompt
— most don't, and this field is simply omitted for them.
