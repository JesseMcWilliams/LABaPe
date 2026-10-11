# LABaPe container stack

This folder runs the LABaPe web interface: the API, the web UI and the job worker, together with PostgreSQL and Caddy. The design is in [Claude_Docs/Planning_Web-Interface-Design.md](../Claude_Docs/Planning_Web-Interface-Design.md) §14.

| Container | What it does |
|---|---|
| `labape-api` | FastAPI backend and the built React UI (`labape serve`) |
| `labape-worker` | Job runner (`labape worker`). It runs `scripts/deploy.sh` and `destroy.sh` against the local libvirt. |
| `postgres` | App data and OpenTofu state (the `pg` backend, one workspace per environment) |
| `caddy` | HTTPS: its own internal CA by default, ACME optional |

Docker Compose v2 is the reference. Podman works with the same file (`podman compose`) or with the Quadlet units in `quadlet/`.

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

### Generated secrets (`container/secrets/`)

`setup.sh` creates `postgres_password`, `secret_key` (signs session cookies and encrypts stored credentials), `database_url` and `tofu_pg_conn`.

- `oidc_client_secret` starts empty; paste the Authentik client secret into it.
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

For a throwaway Authentik already configured this way, see [tools/authentik-test/](../tools/authentik-test/README.md).

## Run

```bash
cd container
docker compose up -d --build
docker compose exec labape-api labape breakglass enable     # first admin sign-in, see below
```

Then open `https://<LABAPE_HOSTNAME>/` and sign in as an admin. Register the KVM host under **Hosts**; the defaults match a local host using `/data/VMs/LABaPe`. Deployers can then create environments.

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

`podman compose` uses `compose.yaml` unchanged. For systemd-managed containers, use the Quadlet units in `quadlet/`:

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

## Notes

- The worker mounts `/var/run/libvirt` and `/data` at the same path as on the host, because libvirt resolves disk and ISO paths on the host. It also gets `/dev/kvm`, for Packer builds in phase 10b.
- Each environment has an engine directory at `/var/lib/labape/environments/<name>/engine`, refreshed from the image before every job. OpenTofu state is in PostgreSQL, not in that directory.
- Job logs are kept in `/var/lib/labape/logs/`. After a deploy, the full inventory and the tester credentials are moved into the encrypted secrets table and deleted from disk.
