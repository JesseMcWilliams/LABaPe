# Plan: stop running the API and worker as root (2026-11-12)

> **Stage: Planning.** Review item 8 of [Planning_Project-Review-2026-10-23.md](Planning_Project-Review-2026-10-23.md), left over from batch B1 of [Planning_Next-Round-2026-11-08.md](Planning_Next-Round-2026-11-08.md). Questions 227 and 228 in [Planning_Questions.md](Planning_Questions.md) hold the choices; every recommendation is the default if there is no answer. Nothing here is built.

## 1. Today

`container/Dockerfile` has no `USER`, so `labape-api` and `labape-worker` run as root inside the container. A root process in a container that holds the libvirt socket, `/dev/kvm`, the data root and the stack's secrets is a bigger prize than it needs to be: a bug in the API (a path traversal in the file manager, an SSRF, a command built from user text) then runs as root. Root is not needed for anything LABaPe does: the host side already runs everything privileged over SSH with sudo.

What root hides today, which a plain `USER` line would break:

| Thing | Why root works today | What a non-root user needs |
|---|---|---|
| `labape-data` volume (`/var/lib/labape`: logs, job homes, `home/.ssh` worker key, environments' engine folders) | A new named volume is root-owned | Existing installs: a one-time `chown -R` to the new uid. New installs: the image makes the folder owned by that uid |
| `/data` (`LABAPE_DATA_ROOT`: uploads, VM disks, ISOs; the same path inside and outside) | Root writes anywhere; files it makes are root-owned | The host's service user already owns the tree (`engine/pathperms.py` checks it for writes), so the container user should be that uid and gid |
| `./update` (the update agent's request folder) | Root | Made by `setup.sh` as the service user (0755), so the same uid works |
| `engine-secrets/` (0700 dir, mounted read-only) and `secrets/` | Root reads a 0700 directory of any owner | Owned by the service user, so the same uid works; `secrets/*` are 0644 already |
| `/dev/kvm` and `/var/run/libvirt/*-sock` | Root | The host's `kvm` and `libvirt` gids, added with `group_add` (gids differ per host) |
| `ssh`, `git` and `ansible` | `getpwuid` finds root | A passwd entry for the uid, or `ssh` refuses: "No user exists for uid" |
| `NET_RAW` for `ping` (check-network.sh) | `cap_add` reaches a root process's effective set | A non-root process gets it only in the bounding set: the ping binary needs the file capability, or `net.ipv4.ping_group_range` must include the gid |
| Volumes written by other containers (`labape-syslog-*`, `labape-mailpit-config`, `labape-recordings`, `labape-transcripts`, `labape-ca` read-only) | Root writes or reads whatever the other container's user made | Check each one; the shared ones need a gid or mode both sides accept |

Rootless Podman (the Quadlet install) differs: container root already maps to the service user, so the exposure is smaller; a non-root uid in the container there needs `UserNS=keep-id` so it maps back to the same host uid.

## 2. The change

1. **One uid, the host's service user.** `setup.sh` writes `LABAPE_UID` and `LABAPE_GID` (`id -u`, `id -g`) and the `kvm` and `libvirt` gids (`getent group`; blank when the group is missing) into `container/.env`. `compose.yaml` gets `user: "${LABAPE_UID}:${LABAPE_GID}"` and `group_add` on the api and worker services (the worker needs both groups, the API neither). The installer's stack step writes the same values, so a fresh install needs no manual step.
2. **A passwd entry that follows the uid.** An entrypoint wrapper in the image appends `labape:x:$UID:$GID::/var/lib/labape/home:/bin/bash` to `/etc/passwd` when the uid has none (the file is group-writable for gid 0 at build time), sets `HOME`, then `exec`s the command. Without it `ssh`, `git` and Ansible fail on a uid the image never heard of.
3. **`/var/lib/labape` ownership.** The same entrypoint, when it runs as the service user, only checks that the volume is writable and exits with a message naming the fix if it is not. The fix is a one-time `chown` run by the update agent (host side, root) before the new image starts: `docker run --rm -u 0 -v labape-data:/v <image> chown -R UID:GID /v`, guarded by a marker file (`.owner`) so it runs once. A fresh volume is initialised the same way by the installer.
4. **`ping`.** Prefer `setcap cap_net_raw+ep /bin/ping` in the image (a file capability, effective for a non-root user as long as `NET_RAW` stays in the bounding set via `cap_add`), with a test that `check-network.sh` still gets a reply. Drop it for the API: only the worker pings.
5. **Read-only root filesystem and `no-new-privileges`** as a second step once the first runs clean: `read_only: true` with `tmpfs` for `/tmp` and the job scratch, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` plus `NET_RAW` on the worker. Separate PR; it needs a list of every path the code writes.
6. **Quadlet units** get `User=`/`UserNS=keep-id` as above and the same `GroupAdd=keep-groups` they have.

## 3. Migration of an existing install

The update agent applies a release as root-capable host code, so it does the volume change, in this order: stop the stack, run the `chown` helper over `labape-data` (and `labape-transcripts`, `labape-mailpit-config`, `labape-syslog-conf`/`-tls`, `labape-recordings` where this container writes), write the new `.env` keys, start the new image, health check. A failed health check rolls back as today; the rollback image runs as root and a root process reads files owned by any uid, so rolling back needs no reverse `chown`. The release notes say the first start after the update takes longer on a big `labape-data` (the job logs and environments' engine folders are the bulk).

## 4. Tests

- Unit: the entrypoint (adds the passwd entry once, leaves an existing one, refuses an unwritable volume with the message), `setup.sh` env generation (a missing `libvirt` group leaves the gid blank and the compose file still renders: `docker compose config`).
- Live on a scratch stack on OZ: a fresh install; an update from the v0.7.0 image with data in the volume; a deploy (OpenTofu, Ansible, `ssh`), a Packer build (`/dev/kvm`), a file upload into `/data`, `check-network.sh`, the update agent's request and status round trip, a rollback.
- The existing check that the API reads `engine-secrets/` as non-root: a test that `stat`s the mount modes and fails when `engine-secrets` is not owned by the container uid.

## 5. Open questions

| # | Question | Recommendation |
|---|---|---|
| 227 | Which uid does the container run as? | The host service user's uid and gid, from `.env` (matches `/data`, `update/` and `engine-secrets/` as they are). The alternative, a fixed `10001` in the image, needs a `chown` or an ACL on the shared `/data` tree and the secrets folder, which also libvirt-qemu and the host's service user use |
| 228 | Who migrates an existing install's volumes? | The update agent, once, before the first non-root start, guarded by a marker (section 3); not a manual step in the release notes |

## 6. Order

1. Entrypoint, `USER`/`user:` and the `.env` keys, with the tests above (one PR). Live-test on OZ before merging.
2. The agent's migration step (same PR or the next, before a release uses it).
3. Hardening: read-only root, `no-new-privileges`, `cap_drop` (own PR).
