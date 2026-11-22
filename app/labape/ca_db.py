"""Reads the platform CA's own database: the certificates it issued, whoever asked.

step-ca keeps its issued certificates in a database of the stack's PostgreSQL (`stepca`; container/ca/init.sh points it
there). An ACME client such as Home Assistant's Let's Encrypt add-on asks the CA directly, so LABaPe's own register
never saw its certificates; `certs.sync_from_ca` copies them in from here. Read-only, and only on PostgreSQL."""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings

log = logging.getLogger("labape.ca_db")


def database_name() -> str:
    return os.environ.get("LABAPE_CA_DB") or "stepca"


def read() -> tuple[list[dict], set[str]] | None:
    """([{serial, der, provisioner, type}], the revoked serials), or None when the CA's database can't be read (SQLite,
    not switched yet, the CA has never started with it)."""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "postgresql":
        return None
    engine = create_engine(url.set(database=database_name()), pool_pre_ping=False)
    try:
        with engine.connect() as c:
            found = c.execute(text(
                "select convert_from(c.nkey, 'UTF8'), c.nvalue, d.nvalue from x509_certs c "
                "left join x509_certs_data d on d.nkey = c.nkey")).all()
            revoked = {r[0] for r in c.execute(text("select convert_from(nkey, 'UTF8') from revoked_x509_certs"))}
    except SQLAlchemyError as exc:
        log.info("the CA's database isn't readable yet: %s", str(exc).splitlines()[0] if str(exc) else exc)
        return None
    finally:
        engine.dispose()
    rows = []
    for serial, der, data in found:
        prov: dict = {}
        if data:
            try:
                prov = (json.loads(bytes(data)).get("provisioner") or {})
            except ValueError:
                prov = {}
        rows.append({"serial": serial, "der": bytes(der), "provisioner": prov.get("name", ""), "type": prov.get("type", "")})
    return rows, revoked
