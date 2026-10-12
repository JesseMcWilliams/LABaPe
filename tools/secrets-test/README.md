# Throwaway OpenBao and Conjur for LABaPe

This folder stands up an **OpenBao** server and a **CyberArk Conjur Open Source** server in containers, so LABaPe's external secret stores (design §11, phase 10d) can be built and tested against real instances. It's for development and lab testing, never for real secrets: OpenBao has a single unseal key, kept in `.env` next to its root token.

What a provider needs from a store is in [Claude_Docs/Reference_Secrets-Backend-Requirements.md](../../Claude_Docs/Reference_Secrets-Backend-Requirements.md).

## What you get

| Service | Port | Set up for LABaPe |
|---|---|---|
| OpenBao 2.7 (`openbao/openbao`) | 8200, HTTPS | KV v2 at `labape/`; AppRole role `labape` with policy `labape` (read and write `labape/*`) |
| Conjur Open Source 1.27 (`cyberark/conjur`), with PostgreSQL 17 and an nginx TLS proxy | 8443, HTTPS | Account `labape`; host `labape/app`, which owns policy `labape/secrets` (it declares variables there, then sets and reads them) |

Both serve a certificate from the **LABaPe platform CA**, and the host trusts the platform root. The ports and the publish address are in `.env` (`OPENBAO_PORT`, `CONJUR_PORT`, `BIND_ADDRESS`).

## Deploy

Run as root on a Linux host. Docker (with compose v2), `curl`, `openssl` and `python3` are installed on Debian or Ubuntu if missing.

**On a VM deployed from the LABaPe web interface** (it already has a platform certificate in `/etc/labape/tls/`, renewed on the VM):

```bash
sudo tools/secrets-test/deploy.sh
```

**On the LABaPe host itself** (the stack's `container/` folder is on this machine; there is no VM certificate, so the stack's CA signs one with `labape ca-sign`):

```bash
sudo tools/secrets-test/deploy.sh --tls-from-stack /path/to/LABaPe/container --bind <host LAN address>
```

`--bind` matters there: Caddy holds `127.0.0.1:8443` for break-glass access, so Conjur can't publish on every address. `--names a,b,c` sets the certificate's names and addresses (default: the short host name, the full one, and the primary address).

Each run is safe to repeat: it keeps the data and the credentials, unseals OpenBao if needed, re-applies the configuration, and ends with a smoke test. The smoke test signs in to each store as LABaPe would (AppRole, and Conjur host authentication), writes a value and reads it back, using the system trust store.

## Credentials

Everything secret goes into `.env` here (mode 0600, git-ignored), and nothing secret is printed:

| Key | What it is |
|---|---|
| `OPENBAO_ROLE_ID`, `OPENBAO_SECRET_ID` | LABaPe's AppRole login. `OPENBAO_SECRET_ID` goes once LABaPe has rotated it (below). |
| `OPENBAO_ROOT_TOKEN`, `OPENBAO_UNSEAL_KEY` | Administration only |
| `CONJUR_HOST_API_KEY` | API key of host `labape/app` (login `host/labape/app`). Goes once LABaPe has rotated it (below). |
| `LABAPE_OWNS_LOGINS` | `1` once LABaPe has rotated a login: the script then never issues those logins again |
| `CONJUR_ADMIN_API_KEY` | Conjur's `admin` user, administration only |
| `CONJUR_DB_PASSWORD`, `CONJUR_DATA_KEY` | Conjur's database password and its encryption key |

If `CONJUR_HOST_API_KEY` is removed from `.env`, the next run issues the host a new key with the admin key (the old one stops working).

**After LABaPe rotates its logins.** LABaPe rotates its secret ID and host API key daily (its design §25.3). LABaPe's OpenBao policy includes `update` on its own role's `secret-id` and `secret-id/destroy` paths for this. Once LABaPe has rotated them, the copies here are stale, and they're removed automatically:
- On every run, and at boot (`--unseal`), `deploy.sh` tries each copy. If the store refuses it, the line is removed from `.env` and `LABAPE_OWNS_LOGINS=1` is set. A store that isn't answering yet changes nothing.
- With `LABAPE_OWNS_LOGINS=1`, the script never issues the logins again. In particular it doesn't give the Conjur host a new key, which would lock LABaPe out.
- The smoke test doesn't use them: it signs in to OpenBao with a throwaway secret ID (destroyed afterwards) and skips the Conjur host check with a note.
- The copy for the live tests (`~/.config/labape/secrets-test.env`, from `tools/helpers/`) is deleted by `lab-live.sh` once `stores.py` or `stores_rotate.py` reports that none of its logins work any more.

If you ever need LABaPe's login again, for example after restoring an old LABaPe backup, issue a new one as root and paste it in LABaPe's Settings → Secret stores. For OpenBao: `bao write -f auth/approle/role/labape/secret-id`. For Conjur: `PUT /authn/labape/api_key?role=host:labape/app` as admin. Either way, the previous one stops working.

## What runs on the host

| Unit | Does |
|---|---|
| `labape-secrets-test.service` | At boot: starts the containers and unseals OpenBao (`deploy.sh --unseal`) |
| `labape-secrets-test-tls.path` (VM certificate) | When `/etc/labape/tls/host.crt` is renewed: copies it and reloads OpenBao (SIGHUP) and nginx |
| `labape-secrets-test-tls.timer` (`--tls-from-stack`) | Daily: has the stack sign a new certificate when fewer than 10 days are left, then reloads |

## Other commands

```bash
sudo tools/secrets-test/deploy.sh --unseal          # unseal OpenBao by hand
sudo tools/secrets-test/deploy.sh --tls-reload      # pick up the VM certificate now
sudo tools/secrets-test/deploy.sh --tls-renew       # stack mode: renew now if due
sudo tools/secrets-test/deploy.sh --down            # stop, keep everything
sudo tools/secrets-test/deploy.sh --down --purge    # delete the data, .env, tls/ and the units
```

Administration inside the containers:

```bash
sudo docker compose -f tools/secrets-test/compose.yaml exec -e BAO_TOKEN=<root token> openbao bao secrets list
sudo docker compose -f tools/secrets-test/compose.yaml exec conjur conjurctl policy load labape /policy/labape.yml
```

## Notes

- OpenBao 2 has no `file` storage; this uses one-node integrated (Raft) storage.
- `bao status` exits 2 when OpenBao is sealed. The script reads its output, not its exit code.
- OpenBao exits with status 0 when the host shuts down, and Docker's `unless-stopped` then left it stopped after a reboot. It uses `restart: always`, and the boot unit runs `docker compose up -d` before unsealing anyway.
- `conjurctl policy load` prints the API keys of the roles it creates. The script captures that output and never shows it.
- The unseal key and root token are passed to `bao` on the command line inside the container, so other root processes on the host can see them while it runs. That's acceptable for a test instance only.
