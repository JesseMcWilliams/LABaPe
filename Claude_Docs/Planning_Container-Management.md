# Plan: a Containers page for Stack and KVM hosts

> **Stage: Planning / building.** The idea of 2026-11-06 (after OZ's failed-out drive), with the decisions the user gave the same day. Questions 203-207 are answered ([Planning_Questions.md](Planning_Questions.md)). **C1 (read) is built** (design §75); C2 (actions) and C3 (clean-up) are next. Today's Stack section is [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) §30; Drive Pools (§53.4) is the model for guarded host actions.

## 0. Decisions so far (2026-11-06)

1. A **Containers** section on a host's page, for **any host that runs Docker or Podman**: Stack hosts and KVM hosts alike (a host with neither runtime shows no section).
2. **Read first.** Batch C1 has no actions; guarded actions come in C2.
3. **The Stack's own containers are read-only** in this page, whatever the action level (§3). Restarting the API, worker, database or secret stores from a page they serve kills the request, running jobs or the unseal state.
4. **Docker and Podman are treated alike**: one collector script and one parsed shape; the runtime name is shown.
5. **Logs are admin-only, read on request, never stored.** Reading them is audited (that they were read, not what they said).
6. **Collection is periodic and on demand**: the page's facts refresh every 5 minutes with the host's other facts, and **Refresh** reads now; logs and live stats are fetched only when asked.
7. **Privilege is the admin's choice per host** (§3): what actions may touch.

## 1. What exists

| Thing | Today |
|---|---|
| Stack section | `StackSection` (`HostSections.tsx`) lists name, state, health, restarts and image of the containers in the `labape` and `authentik-test` compose projects, and the volumes. Stack hosts only; read every 5 minutes (`stackmon.STACK_SCRIPT`, filtered by `PROJECTS`). |
| Alerts | `stackmon.evaluate` raises Monitoring alerts for the stack's own containers (down, unhealthy, restarting). |
| Host facts | `hostadmin.FACTS` runs one script over the SSH access account every 5 minutes and stores the raw values; `hostpools.build` parses them on the web stack. |
| Guarded actions | `api/hosts.py` `pool_action`: fixed commands, arguments validated, audited, facts re-read after. |

## 2. Batches

| Batch | What | Notes |
|---|---|---|
| C1 | **Read.** Every container on the host (all compose projects and plain `run`s): name, image, compose project and service, state and status text, health, started/finished times, exit code, restart count, ports, mounts, networks; CPU and memory from `stats --no-stream`. Images (size, age, dangling) and volumes (size, links, unused). A container detail dialog with **Logs** (tail N lines, since T, a filter) and live stats, fetched on request. Monitoring alerts for a container on a non-stack host that is unhealthy, restart-looping or exited with an error. | New fact `containers` in `hostadmin.FACTS`; parser `engine/hostcontainers.py`; section `ContainersSection` in its own file (`HostSections.tsx` is near the size limit). The Stack section's table stays, linking to the new one. |
| C2 | **Guarded actions.** Start, stop, restart; pull and recreate a compose service. Each confirmed in the page, audited as `host.container.<op>`, facts re-read after. Limited by the host's action level (§3). | Fixed commands with the name checked against the host's own list; never `exec`. |
| C3 | **Clean-up.** Remove an exited container, an unused image, an unused volume, each shown first with what it would free; prune only after a preview. | Never touches a volume in use or the Stack's own. |

## 3. The action level (C2)

A per-host setting, **off** by default, that the admin chooses on the host's page:

| Level | What the page may act on |
|---|---|
| off | Nothing: the Containers page is read-only. |
| known | Only containers the admin has marked as known (their names are on a list kept for the host). A container that appears later is shown but not actionable until it is marked. |
| new | Known containers, and containers created after the level was set (including a compose service the page recreates). |
| all | Every container on the host except the Stack's own. |

The Stack's own containers (compose project `labape`, and `authentik-test` on a test stack) are never actionable, at any level. Question 203 asks whether this reads the three levels as meant.

## 4. Privileges and safety

- Docker access is root-equivalent. The SSH access account needs the `docker` group (or rootless Podman); Prepare / Set up access would add it, only when the level is above off. Podman: the access account's own rootless containers, plus the system ones when it may read them.
- C2 runs fixed commands (`docker start|stop|restart <name>`), the name taken from the host's last facts and checked with the same pattern as pool names; no shell string from the browser.
- Logs are capped (lines and bytes) and sent through the API, not kept; a log can hold secrets, so the section needs the admin role, as the rest of host administration does.
- On a Podman host the Quadlet-managed containers are restarted through their systemd unit in C2, not with `podman restart`, so systemd does not undo it. Question 204.

## 5. Questions (all answered 2026-11-06, the recommendations)

| # | Question | Answer |
|---|---|---|
| 203 | Do the levels in §3 match what you meant by "known containers, new containers, all containers"? | As written. |
| 204 | Quadlet-managed (systemd) containers: act through the unit? | Yes. |
| 205 | The default level for a host, and may the Stack host itself ever be set above off (its own non-LABaPe containers)? | off; yes, non-LABaPe containers only. |
| 206 | Log reads: the cap (lines, bytes) and whether to mask obvious secrets (passwords, tokens in `KEY=value` lines)? | 2,000 lines / 1 MB, mask `*_PASSWORD`, `*_TOKEN`, `*_SECRET`, `*_KEY` values. |
| 207 | Alerts for containers on non-stack hosts: on by default, per host switchable? | On; switchable. |
