"""Rotators for the external stores' own logins (design §25, phase P3).

Each store's login is kept in the built-in store (`stores/<name>/<field>`) and
rotated like any privileged secret, every 24 hours by default. A policy exists
only once its store is set up (Settings → Secret stores), and is scheduled
only while the store is configured.

- OpenBao / Vault: issue a new AppRole secret ID, sign in with it, store it,
  then destroy the old one. LABaPe's policy needs `update` on its role's
  `secret-id` and `secret-id/destroy` paths, or the policy names an elevated
  account holding a token that has them.
- Conjur: the host asks for a new API key, which replaces the old one at
  once; it's stored straight away, then a sign-in with it is checked.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import stores
from ..models import SecretPolicy
from ..security import put_secret

# Rotator kind -> the store whose login it rotates. Defined before importing
# rotation, which imports this module at its end.
STORE_ROTATORS = {"openbao_secret_id": "openbao", "conjur_api_key": "conjur"}


def store_ready(db: Session, kind: str) -> bool:
    return STORE_ROTATORS[kind] in stores.configured(db)


from .rotation import RotationError, _write, elevated_value, rotator  # noqa: E402


@rotator(kind="openbao_secret_id", label="OpenBao / Vault: LABaPe's AppRole secret ID",
         notes="Issues a new secret ID for LABaPe's AppRole role, signs in with it, stores it, then destroys the old "
               "one; no restart. LABaPe's OpenBao policy needs update on auth/approle/role/<role>/secret-id and "
               ".../secret-id/destroy, or choose an elevated account holding a token that has them.")
def rotate_openbao_secret_id(db: Session, policy: SecretPolicy, log) -> str:
    try:
        store = stores.open_store(db, "openbao")
        token = elevated_value(db, policy) if policy.elevated_ref else None
        old = store._credential
        new = store.issue_secret_id(token)
        if not store.check_login(new):
            try:
                store.destroy_secret_id(new, token)
            except stores.StoreError:
                pass
            raise RotationError("the new secret ID doesn't sign in; the old one is still in use")
        put_secret(db, stores.credential_secret("openbao"), new)
        stores.forget("openbao")
        _write(log, "labape: OpenBao: new secret ID signs in and is stored")
        try:
            store.destroy_secret_id(old, token)
        except stores.StoreError as exc:
            _write(log, f"labape: OpenBao: couldn't destroy the old secret ID: {exc}")
            return "new secret ID in use; the old one is still valid (destroy it in OpenBao)"
    except stores.StoreError as exc:
        raise RotationError(str(exc)) from exc
    if store.check_login(old):
        return "new secret ID in use, but the old one still signs in"
    return "new secret ID in use; the old one is destroyed"


@rotator(kind="conjur_api_key", label="CyberArk Conjur: LABaPe's host API key",
         notes="The host asks Conjur for a new API key, which replaces the old one at once; LABaPe stores it straight "
               "away and checks it signs in; no restart.")
def rotate_conjur_api_key(db: Session, policy: SecretPolicy, log) -> str:
    try:
        store = stores.open_store(db, "conjur")
        new = store.rotate_api_key()
    except stores.StoreError as exc:
        raise RotationError(str(exc)) from exc
    # The old key is already dead: keep the new one before anything else can fail.
    put_secret(db, stores.credential_secret("conjur"), new)
    stores.forget("conjur")
    _write(log, "labape: Conjur: new API key stored")
    if not store.check_login(new):
        raise RotationError("Conjur issued a new API key that doesn't sign in; set the host's key by hand "
                            "(Settings → Secret stores)")
    return "new API key in use"
