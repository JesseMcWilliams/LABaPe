# Throwaway Authentik for testing

LABaPe signs users in through an existing Authentik by default. If you need one for testing, this folder deploys a disposable instance with everything LABaPe expects already configured:

- the `labape` OIDC application;
- the role groups (`labape-admins`, `labape-template-editors`, `labape-deployers`, `labape-file-managers`, `labape-viewers`);
- three local users: `labape-admin`, `labape-dev` (deployer) and `labape-viewer`;
- your own administrator login: the installer asks for one (`AuthentikAdminUser`) and `deploy.sh --admin-user U --admin-email E` creates it (the password on standard input); `create-admin.sh USER EMAIL` does it later on an existing stack (superuser, and a LABaPe admin through `labape-admins`). akadmin is for LABaPe's own checks and rotation, not for people.
- akadmin's password (the first one is `AUTHENTIK_BOOTSTRAP_PASSWORD`): `deploy.sh` stores it in the LABaPe secret store and blanks it in `.env`; see it under LABaPe → Admin → Secrets → Authentik administrator → Reveal (design §54). The rotation account may also reset it.
- a secret-rotation account, `labape-rotator`, whose token may set the directory token's key. Authentik checks that as `add_token` on that token, which `deploy.sh` grants on the token object only. Add it in LABaPe under Secrets → Add elevated account (`LABAPE_TEST_ROTATOR_TOKEN` in `.env`). Once LABaPe rotates the directory token, the value in `.env` no longer works. The same token may also change the `labape` provider (`view_oauth2provider` and `change_oauth2provider` on that provider only), so LABaPe can rotate its OIDC client secret; after that, `LABAPE_TEST_CLIENT_SECRET` in `.env` is out of date too, and `--configure-labape` writes the old value.
- a read-only directory account, `labape-directory` (a service account whose only permission is viewing groups), with a non-expiring API token for LABaPe's group search. To use it, enable **Settings → Directory** in LABaPe and paste `LABAPE_TEST_DIRECTORY_TOKEN` from `.env`.

It isn't meant for production. The bundled-Authentik profile in phase 10g covers that.

```bash
container/setup.sh --hostname labape.lab.lan            # if not done yet
tools/authentik-test/deploy.sh --labape-hostname labape.lab.lan --configure-labape
cd container && docker compose up -d                    # pick up the OIDC settings
```

- `deploy.sh` generates every secret into `tools/authentik-test/.env` (gitignored, mode 0600) and never prints them. Read the test users' password from `LABAPE_TEST_USER_PASSWORD` in that file.
- Authentik listens on plain HTTP port 9000 (and 9443 with its own self-signed certificate) on this host. Browsers and the LABaPe containers both reach it at `--public-host`, which defaults to the host's first IP address.
- `--configure-labape` writes the issuer and client ID into `container/.env`, and writes the client secret into `container/secrets/oidc_client_secret`.
- `deploy.sh --down` removes the containers and their data.
