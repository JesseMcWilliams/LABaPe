# Requirements for a new secrets backend

What a secret store must offer, and what its LABaPe provider must do, before it can hold LABaPe's secrets. It applies to the first external stores (OpenBao/HashiCorp Vault and CyberArk Conjur, phase 10d) and to any store after them. The design is Planning_Web-Interface-Design.md §11 (the provider interface) and §24 (rotation); test instances come from `tools/secrets-test/`.

## 1. Where a backend fits

Today every secret lives in the **built-in store**: encrypted rows in PostgreSQL (`security.get_secret` / `put_secret`, envelope encryption with rotating data keys). Each rotation policy already names its store (`secret_policies.store`, offered from `STORES` in `app/labape/api/secrets.py`), so several stores can be in use at once and the store is chosen **per secret** (question 84).

A backend is a **provider**: a Python class registered under the `labape.secrets_providers` entry point group, so a new one doesn't touch the core. The **secret catalog** (the `secrets` table) has one row per stored secret naming its store and linking its rotation policy; `get_secret`/`put_secret` follow that row, and a new environment secret goes to the default store set in Settings (design §25.2).

What may move to an external store: the engine's Ansible vault password, bootstrap and elevated credentials, per-environment passwords and credential sheets, the worker's SSH key for remote KVM hosts, SMTP and directory tokens. What stays out: the store's own login (section 5), the master key and the database password (the API needs them before it can reach any store).

## 2. What the store itself must offer

| Requirement | Why |
|---|---|
| An HTTPS API | LABaPe talks to stores only over TLS (section 6) |
| Machine authentication | A non-human identity with a credential LABaPe can hold: OpenBao AppRole, a Conjur host and API key. No user passwords, no interactive login. |
| Short-lived session tokens, renewable by logging in again | A leaked session token expires on its own |
| Scoped permissions | LABaPe's identity reads and writes its own area only (section 7) |
| Read, write, delete, list (or an equivalent) | The provider interface, section 3 |
| Values up to 64 KB | SSH private keys, credential sheets, a whole vault file |
| An audit log of its own (preferred) | Cross-checks LABaPe's audit trail |
| Versioning, or writes that can't half-apply (preferred) | Rotation's undo step (section 8) |
| A test instance that can be scripted | A service in the stack (`container/stores/`, set up by `stores.sh`), as OpenBao and Conjur are; the throwaway `tools/secrets-test/` was retired on 2026-10-12 |

## 3. The provider interface

| Member | Contract |
|---|---|
| `name`, `display_name` | Stable key (`openbao`, `conjur`) stored in `secret_policies.store`; the label for the UI and dashboard |
| `settings_model` | A pydantic model for its Settings section: URL, CA bundle, auth method, identity. Fields holding a credential are marked secret: kept in the built-in store, reported to the UI only as "is set". |
| `get(path) -> str \| None` | The current value; `None` when it doesn't exist. Never cached longer than the policy allows (section 8). |
| `put(path, value)` | Creates or replaces. Declares whatever the store needs first (Conjur: load a variable into the provider's policy branch). |
| `delete(path)` | Removes it; deleting a missing secret isn't an error |
| `list(prefix) -> [path]` | For migration and cleanup |
| `test_connection() -> (ok, message)` | Signs in and reads something harmless. The dashboard's "Secrets and certificates" panel calls it (cached), and so does the Settings test button. The message never contains a secret. |
| `rotates_itself` (optional) | True when the store rotates a secret on its own schedule; the policy is then `managed_externally` and LABaPe only reads |

Errors are raised as one provider exception with a plain message ("OpenBao refused the login: permission denied"), never with a value, token or response body that could carry one.

## 4. Naming and paths

LABaPe names secrets with slashes: `settings/smtp_password`, `elevated/<name>`, `platform/oidc_client_secret`, `environment/<id>/passwords`. Each provider maps a name onto its store without collisions and keeps the mapping reversible, so `list` returns LABaPe names.

| Store | Mapping |
|---|---|
| OpenBao / Vault (KV v2) | `<mount>/data/<prefix>/<name>`, with the value in the `value` field. Default mount `labape`. |
| Conjur | Variable `<branch>/<name>` in a policy branch the host owns (default `labape/secrets`). `put` loads `- !variable <name>` into the branch the first time. |

Characters outside `[A-Za-z0-9._/-]` are percent-encoded by the provider. A path must never come from user input without that check, since a crafted name could otherwise reach another area of the store.

## 5. The provider's own credential

The login a provider uses (AppRole secret ID, Conjur API key) can't live in the store it opens. It's kept in the **built-in store**, entered in Settings and never shown again, like the SMTP password. Its rotation is a policy of its own:
- AppRole secret IDs: issue a new one, log in with it, then revoke the old one.
- Conjur host API keys: rotate the key with the current one, which the host may do for itself.

The session token is held in memory only, renewed before it expires, and fetched again after a 401 or 403.

## 6. TLS

- HTTPS only; no plain-HTTP option.
- Certificates are verified, against the LABaPe platform root by default (the stack's stores use certificates from the platform CA) or a CA bundle set in the store's settings. Turning verification off is a separate, visible setting, as for the directory (Settings → Directory).
- Timeouts: 5 s to connect, 15 s per request. A store that doesn't answer is an error, never a hang.

## 7. Least privilege in the store

LABaPe's identity may read, write, list and delete its own area, and nothing else. The test setups are the reference:

- **OpenBao**: policy `labape`:

  ```hcl
  path "labape/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
  path "labape/metadata/*" { capabilities = ["read", "list", "delete"] }
  path "labape/metadata"   { capabilities = ["list"] }
  path "auth/token/lookup-self" { capabilities = ["read"] }
  path "auth/approle/role/labape/secret-id"         { capabilities = ["update"] }
  path "auth/approle/role/labape/secret-id/destroy" { capabilities = ["update"] }
  ```

  The last two let LABaPe rotate its own secret ID (design §25.3); without them, give the rotation policy an elevated account holding a token that has them.

  It's attached to AppRole role `labape` (token TTL 1 h, max 4 h).
- **Conjur**: host `labape/app` owns policy `labape/secrets`, so it can declare variables there and read and update them; it has no rights outside that branch.

With people's access to OpenBao (design §49.2) the `labape` policy also lets LABaPe keep the owners of each environment: identity entities `labape-user-*`, entity and group aliases, the members of groups `labape-*` (types internal and external, no `policies` parameter), lookups, and reading `sys/auth` and the policy list. It can't write a policy or attach one: those are written with the root token by `stores.sh people-policies` from a fixed template. Don't widen this with an `allowed_parameters` glob on `policies`: OpenBao matches a glob only against a string, and a comma list in a string passes it.

Administration (the root token, Conjur's admin) is never given to LABaPe.

## 8. Rotation and caching (design §24)

- Rotators write through the provider: make the new value, apply it, verify, commit, undo. A provider gives undo a way back: write the old value again, or (KV v2) read the previous version.
- A `put` that fails leaves the old value readable. A store that can half-apply a write is acceptable only when the provider checks the value by reading it back.
- `managed_externally`: when the store rotates a secret itself, LABaPe never writes it, and reads it fresh before each use. The cache, if any, lasts at most 60 seconds.
- Elevated accounts stay in the built-in store (section 5).

## 9. Failure behavior

| Situation | Behavior |
|---|---|
| Store unreachable or login refused | The operation fails with the provider's message. A job fails before it starts work, not halfway. There's **no silent fallback** to the built-in store. |
| Secret missing | `get` returns `None`; the caller decides (most callers report "not set") |
| Permission denied on one path | Reported with the path's LABaPe name, not the store's response |
| Store slow | The timeouts in section 6; the dashboard shows the check as failing |

Values are never logged, put into job logs or exceptions, or sent to syslog. Audit entries record the action, the secret's name and the store, never the value.

## 10. Moving a secret between stores

Changing a policy's store copies the secret: read from the old store, write to the new one, read back and compare, switch the policy, then delete the old copy. If any step fails, the policy stays on the old store. The whole move is audited.

## 11. What to build and test

1. **The provider**, with its settings model and entry point.
2. **Settings**: a section per store (address, CA, auth, credential, Test connection). The store appears in the Secrets page's store list once it's configured and its test passes.
3. **Dashboard**: its `test_connection` in "Secrets and certificates".
4. **pytest**: the store's HTTP API faked with `httpx` (sign-in, get, put, delete, list, permission denied, timeout, session expiry and re-login), path mapping, no value in any error message, and a move between stores.
5. **The test instance**: a service in the stack's `stores` compose profile with its setup in `container/stores/stores.sh` (least-privilege identity, smoke test) and a line in `container/README.md`.
6. **Live test**: a `tools/helpers/live/` script that points LABaPe at the test instance, moves a secret there and back, and rotates it.
7. **Docs**: design §11 and §24, `User_Docs/Web-Interface-Admin.md` §8 (stores), CHANGELOG, and this document if a requirement changed.
