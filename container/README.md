# LABaPe container stack

This folder runs the LABaPe web interface: the API, the web UI and the job worker, together with PostgreSQL and Caddy. The design is in [Claude_Docs/Planning_Web-Interface-Design.md](../Claude_Docs/Planning_Web-Interface-Design.md) §14.

| Container | What it does |
|---|---|
| `labape-api` | FastAPI backend and the built React UI (`labape serve`) |
| `labape-worker` | Job runner (`labape worker`). It runs `scripts/deploy.sh` and `destroy.sh` against the local libvirt. |
| `postgres` | App data and OpenTofu state (the `pg` backend, one workspace per environment) |
| `caddy` | HTTPS: its own internal CA by default, ACME optional |

Docker Compose v2 is the reference. Podman works with the same file (`podman compose`) or with the Quadlet units in `quadlet/`.

## Host prerequisite: Docker and bridged VMs

Installing Docker on a KVM host cuts bridged VMs off from the LAN. Docker loads `br_netfilter`, which sends bridged frames through iptables, and its FORWARD DROP policy then drops them. VMs can still reach the host but not the gateway. Run this once, as root, before or after installing Docker:

```bash
cat > /etc/sysctl.d/90-labape-bridge-nf.conf <<'EOF'
# libvirt VMs are bridged onto the LAN; don't filter bridged frames with iptables.
net.bridge.bridge-nf-call-iptables = 0
net.bridge.bridge-nf-call-ip6tables = 0
net.bridge.bridge-nf-call-arptables = 0
EOF
echo br_netfilter > /etc/modules-load.d/labape-br_netfilter.conf   # loaded before sysctl applies
modprobe br_netfilter && sysctl --system
```

## Set up

```bash
container/setup.sh --hostname labape.lab.lan     # writes .env and secrets/, makes config/ and engine-secrets/
```

### Configuration (`container/config/`, mounted read-only at `/etc/labape`)

| File | Notes |
|---|---|
| `environment.yml` | Same format as `tofu/environment.yml`: network, ISO paths, domain. The job runner replaces the storage paths with the KVM host's own. |
| `software-manifest.yml` | Same as `ansible/software-manifest.yml` |
| `directory-manifest.yml` | Optional. Same as `ansible/directory-manifest.yml`. |

### Engine secrets (`container/engine-secrets/`, mounted read-only at `/run/labape-secrets`)

| File | Notes |
|---|---|
| `secrets.vault.yml` | The Ansible vault the CLI uses |
| `vault-pass` | Its password file |
| `ssh/<key>` and `ssh/<key>.pub` | One key pair. The runner uses it in place of the vault's `ansible_ssh_private_key_path`, because that path belongs to the CLI host. |
| `ssh/history/` | Previous VM keys, kept by the rotation (7) and still offered to VMs |

The update agent rotates the vault password and the VM key here daily, and copies the key to the service user's `~/.ssh/labape_bootstrap` for the command line (design §24.3).

### Generated secrets (`container/secrets/`)

`setup.sh` creates `postgres_password`, `secret_key` (signs session cookies and encrypts stored credentials), `database_url` and `tofu_pg_conn`.

- `oidc_client_secret` starts empty; paste the Authentik client secret into it. Once LABaPe rotates the secret (Settings > Secrets), the rotated one lives in its built-in store; pasting a new value here later takes over again.
- The directory is mode 0700. The files are 0644 because each container reads them as its own user (PostgreSQL reads as uid 70).
- Never commit any of these. `.gitignore` already excludes them.

### Sign-in (`container/.env`)

Point LABaPe at Authentik (or any OIDC provider):

| Setting | Value |
|---|---|
| `LABAPE_OIDC_ISSUER` | e.g. `https://authentik.lab.lan/application/o/labape/` |
| `LABAPE_OIDC_CLIENT_ID` | the client ID from the Authentik application |
| redirect URI (set in Authentik) | `https://<LABAPE_HOSTNAME>/api/auth/oidc/callback` |
| scopes | `openid email profile`; the `groups` claim comes from the profile scope |

Roles come from Authentik groups. The defaults are:

| Authentik group | LABaPe role |
|---|---|
| `labape-admins` | admin |
| `labape-template-editors` | template_editor |
| `labape-deployers` | deployer |
| `labape-file-managers` | file_manager |
| `labape-viewers` | viewer |

Override them with `LABAPE_ROLE_MAP` (JSON), or add bindings under the API's `/api/admin/role-bindings`.

Group fields (such as a network's "Or limit to groups") search Authentik as you type once an admin sets **Settings → Directory**. That section takes an Authentik API token for a service account that can only view groups (the `authentik_core.view_group` permission). The token is kept in the encrypted secrets store. Without a token, suggestions come from the groups LABaPe has seen at sign-in. A name can always be typed in.

## Platform CA

The stack runs its own CA, `labape-ca` (step-ca), initialized on first start from `secrets/ca_password`. Web-interface deploys give every VM a certificate from it, renewed on the VM, and add the LABaPe root to the VM's trust store (design §22.5). VMs renew on `https://<LABAPE_HOSTNAME>:9444` (`LABAPE_CA_PORT`). To serve the web interface with a certificate from the same CA, set `LABAPE_CADDYFILE=Caddyfile.labape-ca` in `.env` and recreate Caddy. Then import the root (dashboard → Download the root certificate, or `/api/ca/root.crt`) into the browsers that use LABaPe. Keep `secrets/ca_password` and the `labape-ca` volume backed up together; losing them means re-issuing every certificate.

For a throwaway Authentik already configured this way, see [tools/authentik-test/](../tools/authentik-test/README.md).

## Run

```bash
cd container
export LABAPE_VERSION=$(git describe --tags --always)   # shown on the admin dashboard
docker compose up -d --build
docker compose exec labape-api labape breakglass enable     # first admin sign-in, see below
```

Then open `https://<LABAPE_HOSTNAME>/` and sign in as an admin:
1. Register the KVM host under **Hosts**. The defaults match a local host using `/data/VMs/LABaPe`. A KVM host on another machine uses `qemu+ssh://user@host/system`. Install it with `deploy/install-labape.py --profile kvm`, add the key shown on the host's page (or from `docker compose exec labape-worker labape worker-key`) to that user's `authorized_keys`, then use **Check connection**.
2. Add the VM networks under **Networks**: CIDR, gateway, DNS, allowed addressing, static pools, DHCP scope, reserved ranges, and optionally which roles or groups may use each one.
3. Attach networks to the host (**Hosts → Networks**): the bridge for each, an optional slice of static addresses for this host, and the default network.

Deployers can then create environments. Static addresses are allocated from the pools automatically; DHCP addresses are found after boot (Claude_Docs/Planning_Web-Interface-Design.md §20).

### HTTPS

`LABAPE_TLS=internal` (the default) has Caddy's own CA issue the certificate. Browsers will warn until they trust the CA's root, which is at `/data/caddy/pki/authorities/local/root.crt` in the `caddy-data` volume:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt labape-root.crt
```

For ACME, set `LABAPE_TLS` to your contact email. Using an internal ACME server also needs an `acme_ca` global option added to `caddy/Caddyfile`.

`LABAPE_HOSTNAME` can be an IP address. The Caddyfile sets `default_sni` so clients that connect by IP still get a certificate.

## Break-glass access

There's no standing local admin password. If every sign-in provider is down, someone with control of the container runtime runs:

```bash
docker compose exec labape-api labape breakglass enable [--minutes 5] [--local-only]
```

It prints a URL (`/breakglass`) and a one-time credential:
- **Lifetime:** the credential is valid for 5 minutes by default and works once. It opens a 60-minute admin session.
- **Host-only (`--local-only`):** the credential works only through `https://localhost:8443` on the host, or an SSH tunnel to it. Caddy publishes that listener on the host's loopback only and marks its requests for the API; the public listener strips the mark.
- **Revoking:** `labape breakglass revoke` cancels any unused credentials.
- **Audit:** every step goes to the audit log.

`labape auth status` shows each sign-in provider's health. `labape auth disable-provider <name>` and `labape auth enable-provider <name>` switch one off or on.

## Podman and Quadlets

`podman compose` uses `compose.yaml` unchanged, **with the Docker Compose v2 binary as its provider**. Install it (Debian package `docker-compose`, without Docker itself) and enable Podman's API socket (`systemctl enable --now podman.socket`); `podman compose` picks the binary up automatically. Don't use `podman-compose`, the Python implementation: it leaves this file's `${VAR:-default}` values unexpanded in the shared settings block, so the app can't start. `deploy/install-labape.py` sets this up with `ContainerRuntime: podman`; verified rootful on a fresh Debian 13 VM.

For systemd-managed containers, use the Quadlet units in `quadlet/`:

```bash
podman build -f container/Dockerfile -t localhost/labape:local .
for s in postgres-password database-url tofu-pg-conn secret-key oidc-client-secret; do
  podman secret create "labape-$s" "container/secrets/$(echo $s | tr - _)"
done
sudo mkdir -p /etc/labape && sudo cp -r container/config container/engine-secrets container/caddy/Caddyfile /etc/labape/
# /etc/labape/container.env: LABAPE_PUBLIC_URL=https://<host>, LABAPE_HOSTNAME, LABAPE_TLS, LABAPE_OIDC_*
sudo cp container/quadlet/* /etc/containers/systemd/
sudo systemctl daemon-reload && sudo systemctl start labape-caddy
```

For rootless Podman:
- Put the units in `~/.config/containers/systemd/` and use `systemctl --user`.
- The user needs to be in the `libvirt` and `kvm` groups. The worker unit keeps them with `GroupAdd=keep-groups`.
- Ports below 1024 need `net.ipv4.ip_unprivileged_port_start` lowered, or publish 8443 and 8080 instead.

## Maintenance, updates and secret rotation

- **Maintenance mode** (Settings; a banner shows on every page): changes pause while reads and status keep working; the worker starts no new jobs. A weekly window can be set (design §23.3).
- **Updates**: the host agent from `deploy/update-agent/` (installer step `Stack.UpdateAgent`) applies signed releases and rolls back. It watches `container/update/` (design §23.2, `deploy/README.md` § Updates).
- **Secrets page**: each internal secret's interval, store and status, with Rotate now. Secrets in host files (master key, PostgreSQL and CA passwords, vault password, VM key) are rotated by the agent; the others by the worker (design §24). Each environment's page sets its Windows password rotation.
- **CLI in the containers** (`docker compose exec labape-api labape <command>`): `maintenance on|off|status`, `backup`, `restore`, `health [--deep]`, `db-status`, `worker-key`; the agent also uses `rewrap-master`, `ca-repass` and `push-vm-key`.

## Notes

- The worker mounts `/var/run/libvirt` and `/data` at the same path as on the host, because libvirt resolves disk and ISO paths on the host. It also gets `/dev/kvm`, for Packer builds in phase 10b.
- Each environment has an engine directory at `/var/lib/labape/environments/<name>/engine`, refreshed from the image before every job. OpenTofu state is in PostgreSQL, not in that directory.
- Job logs are kept in `/var/lib/labape/logs/`. After a deploy, the full inventory and the tester credentials are moved into the encrypted secrets table and deleted from disk.
