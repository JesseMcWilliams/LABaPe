# Web interface: options

Research for Claude_Docs/Design_System-Overview.md §20 / M10 (2026-10-10). The
feature itself is designed in Claude_Docs/Planning_Environment-Templates.md;
this doc compares ways to build it. Decisions it depends on are in
Claude_Docs/Planning_Questions.md.

## What the interface has to do

From Claude_Docs/Planning_Environment-Templates.md §3, in order of effort:

1. **Run deploys as background jobs** (the largest piece): `tofu apply` +
   `site.yml` takes 15-90 minutes, can fail partway, and needs a queue,
   live log streaming, cancel and retry. Also builds, promotes and
   refreshes of templates (M6), which run 10-60+ minutes.
2. **Author environment templates**: host groups + software manifest +
   directory manifest as one composable object, with sub-assemblies.
   LABaPe-specific; no off-the-shelf tool models it.
3. **Browse catalogs**: OS catalog, template library, software catalog,
   (new) Windows roles/features.
4. **Show credentials** for a running environment, alongside the existing
   generated handout file.

Constraints from the rest of the project: the CLI stays first-class
(the UI should call `scripts/deploy.sh` and friends rather than
reimplement them); everything runs on the libvirt host today; the user
base is a small lab team.

## Options

### A. Semaphore UI (adopt as the job runner)

Open-source (MIT) web UI and REST API for Ansible, Terraform/OpenTofu,
PowerShell, Bash and Python; a single Go binary (BoltDB, MySQL or
PostgreSQL). Job queue, live logs, schedules, RBAC (owner, manager, task
runner, guest), notifications, and "survey" variables on a task template.
A Pro tier adds project runners, 2FA, a Terraform HTTP backend and log
export.

- Fits: item 1 almost entirely. A task template can run
  `scripts/deploy.sh <backend> <env> <profile> --test`, `destroy.sh`,
  `build-template.sh`, `refresh-template.sh` with survey inputs, which
  keeps the CLI the single implementation.
- Doesn't fit: item 2. Semaphore has no notion of a composable environment
  template; inputs are flat variables.
- Cost: low to run (one binary + service on the libvirt host).

### B. AWX (adopt)

The upstream of Red Hat Ansible Automation Platform: inventories, job
templates, surveys, RBAC, workflows. Releases are paused since 24.6.1 (July
2024) during a large refactor, and it installs via a Kubernetes operator.

- Fits item 1 for Ansible; OpenTofu only via wrapper scripts.
- Not recommended now: no current releases, heavy footprint for one lab
  host.

### C. Rundeck Community (adopt)

Apache-2.0 job scheduler/runbook tool with a web console, API and node
model; runs scripts and commands. Comparable to A for "run our scripts
with a UI", JVM-based, less native to Ansible/OpenTofu than Semaphore.

### D. Backstage (adopt as the portal)

Spotify's developer portal: Software Templates (scaffolder) give
form-driven self-service and can call external automation through custom
actions. Needs an execution engine behind it (A or B); Node/TypeScript,
plugin-heavy, a sizeable platform to own for a small team.

- Fits item 2's forms and item 3's catalog browsing; not item 1 on its
  own.

### E. Custom application

A small purpose-built app, e.g. Python FastAPI (matches the repo's
existing Python helpers) with server-rendered HTMX/Jinja pages or a small
Vue/React front end:

- Job runner: run the existing scripts as subprocesses, record jobs in
  SQLite, stream logs over Server-Sent Events, cancel by process group.
  `ansible-runner` is available if per-task Ansible events are wanted.
- Template authoring: the LABaPe-specific editor (§2 composition),
  stored as git-backed YAML.
- Fits everything, but item 1 is real engineering (queueing, concurrency
  limits per libvirt host, log retention, cancellation cleanup).

### F. Hybrid: custom authoring app + Semaphore runner

E's template editor and catalog views, without E's job runner: the app
writes the environment template (git-backed YAML), renders it into the
files `deploy.sh` reads, and starts a Semaphore task through its REST API;
users follow progress in Semaphore (or the app embeds/polls the task
log).

## Comparison

| | Job runner (1) | Authoring (2) | Catalogs (3) | Effort | Moving parts |
|---|---|---|---|---|---|
| A Semaphore | Strong | None | None | Low | 1 service |
| B AWX | Strong (Ansible) | None | None | Medium-high | k8s operator |
| C Rundeck | Good | None | None | Low-medium | JVM service |
| D Backstage (+A) | Via A | Forms, no composition model | Good | High | Node platform + A |
| E Custom | Build it | Build it | Build it | High | 1 app |
| F Custom + Semaphore | Strong (A) | Build it | Build it | Medium | 1 app + 1 service |

## Recommendation

**F, in two steps.** First stand up Semaphore with task templates for the
existing scripts (deploy/destroy, build/promote/refresh template): that
delivers the async job model — the largest piece — in days, with no
LABaPe code to maintain, and it's useful on its own. Then build the small
custom app for template authoring and catalogs, handing execution to
Semaphore's API. If a single self-contained app matters more than effort,
E is the alternative; the scripts-as-the-only-implementation boundary
keeps either choice reversible.

## Sources

- [Semaphore UI](https://semaphoreui.com/) and [docs](https://semaphoreui.com/docs)
- [AWX vs. Semaphore UI comparison (2026)](https://semaphoreui.com/blog/awx-vs-semaphore)
- [Backstage + Semaphore UI](https://semaphoreui.com/blog/backstage-with-semaphore)
- [ansible/awx (release status)](https://github.com/ansible/awx), [Upcoming changes to the AWX project](https://www.redhat.com/en/ansible-collaborative/upcoming-changes-to-the-awx-project)
- [Rundeck open source](https://www.rundeck.com/features)
- [ansible-runner Python interface](https://ansible-runner.readthedocs.io/en/stable/python_interface/)
- [Ansible Backstage plugins](https://github.com/ansible/ansible-backstage-plugins)
