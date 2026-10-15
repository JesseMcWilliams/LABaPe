"""Elevated accounts: check that their stored credential still works, and restore an Authentik one with akadmin.

An elevated account (Secrets → Add elevated account) is what rotators use to change secrets that can't change
themselves. For an Authentik token, such as `authentik-rotator` (the token `labape-rotator`), the stored value can
fall out of step with Authentik: the token was recreated, `deploy.sh` ran again, someone rotated it by hand. The
check (`secret_health`, kind `elevated_account`) asks Authentik who the token is; when Authentik refuses it, the
stored akadmin password signs in, sets a new key on the token, and the new key is stored (design §54.1).
"""
from __future__ import annotations

import secrets as _secrets

import httpx
from sqlalchemy.orm import Session

from . import appsettings, authentik_admin as aa, ca
from .security import get_secret, put_secret

PREFIX = "elevated/"
IDENTIFIERS = {"authentik-rotator": "labape-rotator"}       # elevated account -> its Authentik token's identifier


class ElevatedError(RuntimeError):
    pass


def token_identifier(account: str) -> str | None:
    """The Authentik token an elevated account is, when it is one LABaPe can restore."""
    if account in IDENTIFIERS:
        return IDENTIFIERS[account]
    return "labape-" + account.removeprefix("authentik-") if account.startswith("authentik-") else None


def _where(db: Session) -> tuple[str, object]:
    from .api.directory import api_base
    cfg = appsettings.get(db, "directory")
    base = api_base(cfg)
    if not base:
        raise ElevatedError("no Authentik URL (Settings > Directory)")
    return base, (ca.trust() if cfg.verify_tls else False)


def works(db: Session, account: str) -> bool | None:
    """True when Authentik accepts the stored credential, False when it refuses it (401/403), None when there is
    nothing stored or Authentik can't be asked."""
    value = get_secret(db, PREFIX + account)
    if not value:
        return None
    base, verify = _where(db)
    r = httpx.get(f"{base}/api/v3/core/users/me/", headers={"Authorization": f"Bearer {value}", "Accept": "application/json"},
                  timeout=15, verify=verify)
    if r.status_code in (401, 403):
        return False
    r.raise_for_status()
    return True


def restore(db: Session, account: str, log=None) -> str:
    """Sign in as akadmin, set a new key on the account's Authentik token, store it, and check it works."""
    ident = token_identifier(account)
    if not ident:
        raise ElevatedError(f"LABaPe can't restore {account}: it isn't an Authentik token it made")
    password = get_secret(db, aa.SECRET)
    if not password:
        raise ElevatedError("akadmin's password isn't stored (Secrets → Authentik administrator), so there's nothing to restore with")
    base, verify = _where(db)
    new = _secrets.token_urlsafe(48)
    try:
        with aa.admin_client(base, password, verify=verify) as c:
            r = c.post(f"{base}/api/v3/core/tokens/{ident}/set_key/", json={"key": new})
    except aa.SignInRefused as exc:
        raise ElevatedError(f"akadmin's stored password doesn't sign in ({exc}): fix that secret first") from exc
    if r.status_code >= 300:
        raise ElevatedError(f"Authentik refused to set a key on token {ident} (HTTP {r.status_code})")
    put_secret(db, PREFIX + account, new)
    if works(db, account) is not True:
        raise ElevatedError(f"the new key for {ident} was stored but Authentik doesn't accept it")
    if log is not None:
        log.write(f"labape: akadmin set a new key on token {ident}; stored as elevated account {account}\n")
    return f"akadmin set a new key on token {ident}"
