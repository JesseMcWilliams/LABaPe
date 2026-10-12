"""The built-in store: encrypted rows in the database (security.py)."""
from __future__ import annotations

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from ..models import Secret
from ..security import _fernet, get_secret, put_secret
from .base import SecretStore


class BuiltinStore(SecretStore):
    name = "builtin"
    display_name = "Built-in store (database, encrypted)"

    def __init__(self, db: Session):
        self.db = db

    def get(self, name):
        return get_secret(self.db, name)

    def put(self, name, value):
        put_secret(self.db, name, value)

    def delete(self, name):
        self.db.execute(sql_delete(Secret).where(Secret.name == name))
        self.db.commit()

    def list(self, prefix=""):
        q = select(Secret.name).order_by(Secret.name)
        if prefix:
            q = q.where(Secret.name.startswith(prefix))
        return list(self.db.scalars(q))

    def test_connection(self):
        try:
            f = _fernet()
            ok = f.decrypt(f.encrypt(b"labape-self-test")) == b"labape-self-test"
        except Exception as exc:  # noqa: BLE001 — any failure here is the finding
            return False, f"Self-test failed ({type(exc).__name__}); the secret key may have changed"
        return ok, "Encrypt/decrypt self-test passed"
