"""The built-in store: encrypted rows in the database (security.py)."""
from __future__ import annotations

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from ..models import Secret
from ..security import _fernet, decrypt_value, encrypt_value
from .base import SecretStore


class BuiltinStore(SecretStore):
    name = "builtin"
    display_name = "Built-in store (database, encrypted)"

    def __init__(self, db: Session):
        self.db = db

    # Only rows the catalog places here: an external secret's row isn't a built-in value.
    def get(self, name):
        row = self.db.get(Secret, name)
        return decrypt_value(self.db, row.value) if row is not None and row.store == "builtin" else None

    def put(self, name, value):
        row = self.db.get(Secret, name)
        if row is None:
            self.db.add(Secret(name=name, value=encrypt_value(self.db, value), store="builtin"))
        else:
            row.value, row.store = encrypt_value(self.db, value), "builtin"
        self.db.commit()

    def delete(self, name):
        self.db.execute(sql_delete(Secret).where(Secret.name == name, Secret.store == "builtin"))
        self.db.commit()

    def list(self, prefix=""):
        q = select(Secret.name).where(Secret.store == "builtin").order_by(Secret.name)
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
